# Copyright (C) 2022 Jae-Won Chung <jwnchung@umich.edu>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import typing
import itertools
from unittest.mock import call

import pynvml
import pytest

from zeus.monitor import Measurement, ZeusMonitor

if typing.TYPE_CHECKING:
    from unittest.mock import MagicMock
    from pytest_mock import MockerFixture

ARCHS = [
    pynvml.NVML_DEVICE_ARCH_PASCAL,
    pynvml.NVML_DEVICE_ARCH_VOLTA,
    pynvml.NVML_DEVICE_ARCH_AMPERE,
]


@pytest.fixture
def pynvml_mock(mocker: MockerFixture):
    """Mock the entire pynvml module."""
    mock = mocker.patch("zeus.monitor.pynvml", autospec=True)
    
    # Except for the arch constants.
    mock.NVML_DEVICE_ARCH_PASCAL = pynvml.NVML_DEVICE_ARCH_PASCAL
    mock.NVML_DEVICE_ARCH_VOLTA = pynvml.NVML_DEVICE_ARCH_VOLTA
    mock.NVML_DEVICE_ARCH_AMPERE = pynvml.NVML_DEVICE_ARCH_AMPERE

    return mock


@pytest.fixture(params=sum([list(itertools.product(ARCHS, repeat=i)) for i in [1, 2, 4]], []))
def mock_gpus(request, pynvml_mock: MagicMock) -> tuple[int]:
    """Mock `pynvml` so that it looks like there are GPUs with the given archs.

    This fixture automatically generates different combinations of GPUs with
    the given architectures (ARCH)using itertools.product.
    """
    archs = request.param
    count = len(archs)

    index_to_handle = {i: f"handle{i}" for i in range(count)}
    handle_to_arch = {f"handle{i}": arch for i, arch in enumerate(archs)}

    pynvml_mock.nvmlDeviceGetCount.return_value = count
    pynvml_mock.nvmlDeviceGetHandleByIndex.side_effect = lambda index: index_to_handle[index]
    pynvml_mock.nvmlDeviceGetArchitecture.side_effect = lambda handle: handle_to_arch[handle]

    return archs


def test_monitor(pynvml_mock, mock_gpus, mocker: MockerFixture):
    """Test the `ZeusMonitor` class."""
    num_gpus = len(mock_gpus)
    old_arch_flags = [arch < pynvml.NVML_DEVICE_ARCH_VOLTA for arch in mock_gpus]
    num_old_archs = sum(old_arch_flags)

    mkdtemp_mock = mocker.patch("zeus.monitor.tempfile.mkdtemp", return_value="mock_log_dir")
    which_mock = mocker.patch("zeus.monitor.shutil.which", return_value="zeus_monitor")
    popen_mock = mocker.patch("zeus.monitor.subprocess.Popen", autospec=True)
    mocker.patch("zeus.monitor.atexit.register")

    monotonic_counter = itertools.count(start=4, step=1)
    mocker.patch("zeus.monitor.time.monotonic", side_effect=monotonic_counter)

    energy_counters = {
        f"handle{i}": itertools.count(start=1000, step=3)
        for i in range(num_gpus) if not old_arch_flags[i]
    }
    pynvml_mock.nvmlDeviceGetTotalEnergyConsumption.side_effect = lambda handle: next(energy_counters[handle])
    energy_mock = mocker.patch("zeus.monitor.analyze.energy")

    ########################################
    # Test ZeusMonitor initialization.
    ########################################
    monitor = ZeusMonitor()

    if num_old_archs > 0:
        assert mkdtemp_mock.call_count == 1
        assert monitor.monitor_log_dir == "mock_log_dir"
        assert which_mock.call_count == 1
    else:
        assert mkdtemp_mock.call_count == 0
        assert not hasattr(monitor, "monitor_log_dir")
        assert which_mock.call_count == 0

    # Zeus monitors should only have been spawned for GPUs with old architectures.
    assert popen_mock.call_count == num_old_archs
    assert list(monitor.monitors.keys()) == [i for i in range(num_gpus) if old_arch_flags[i]]

    # Start time would be 4, as specified in the counter constructor.
    assert monitor.monitor_start_time == 4

    ########################################
    # Test measurement windows.
    ########################################
    def tick():
        """Calling this function will simulate a tick of time passing."""
        next(monotonic_counter)
        for counter in energy_counters.values():
            next(counter)

    def assert_window_begin(name: str, begin_time: int):
        """Assert monitor measurement states right after a window begins."""
        assert monitor.measurement_states[name][0] == begin_time
        assert monitor.measurement_states[name][1] == {
            # `begin_time` is actually one tick ahead from the perspective of the
            # energy counters, so we subtract 5 instead of 4.
            i: pytest.approx((1000 + 3 * (begin_time - 5)) / 1000.0)
            for i in range(num_gpus) if not old_arch_flags[i]
        }
        pynvml_mock.nvmlDeviceGetTotalEnergyConsumption.assert_has_calls([
            call(f"handle{i}") for i in range(num_gpus) if not old_arch_flags[i]
        ])
        pynvml_mock.nvmlDeviceGetTotalEnergyConsumption.reset_mock()

    def assert_measurement(name: str, measurement: Measurement, begin_time: int, elapsed_time: int):
        """Assert that energy functions are being called correctly.

        Args:
            name: The name of the measurement window.
            measurement: The Measurement object returned from `end_window`.
            begin_time: The time at which the window began.
            elapsed_time: The time elapsed when the window ended.
        """
        assert name not in monitor.measurement_states
        assert num_gpus == len(measurement.energy)
        assert elapsed_time == measurement.time
        energy_mock.assert_has_calls([
            call(f"mock_log_dir/gpu{i}.power.csv", begin_time - 4, begin_time + elapsed_time - 4)
            for i in range(num_gpus) if old_arch_flags[i]
        ])
        energy_mock.reset_mock()
        pynvml_mock.nvmlDeviceGetTotalEnergyConsumption.assert_has_calls([
            call(f"handle{i}") for i in range(num_gpus) if not old_arch_flags[i]
        ])
        pynvml_mock.nvmlDeviceGetTotalEnergyConsumption.reset_mock()
        for i in range(num_gpus):
            if not old_arch_flags[i]:
                # The energy counter increments with step size 3.
                assert measurement.energy[i] == pytest.approx(elapsed_time * 3 / 1000.0)


    # Serial non-overlapping windows.
    monitor.begin_window("window1", sync_cuda=False)
    # assert monitor.measurement_states["window1"] == (5, {i: pytest.approx(1000 / 1000.0) for i in range(num_gpus) if not old_arch_flags[i]})
    assert_window_begin("window1", 5)

    tick()

    # Calling `begin_window` again with the same name should raise an error.
    with pytest.raises(ValueError, match="already exists"):
        monitor.begin_window("window1", sync_cuda=False)

    measurement = monitor.end_window("window1", sync_cuda=False)
    assert_measurement("window1", measurement, begin_time=5, elapsed_time=2)

    tick(); tick()

    monitor.begin_window("window2", sync_cuda=False)
    assert_window_begin("window2", 10)

    tick(); tick(); tick()

    measurement = monitor.end_window("window2", sync_cuda=False)
    assert_measurement("window2", measurement, begin_time=10, elapsed_time=4)


    # Overlapping windows.
    monitor.begin_window("window3", sync_cuda=False)
    assert_window_begin("window3", 15)

    tick()

    monitor.begin_window("window4", sync_cuda=False)
    assert_window_begin("window4", 17)

    tick(); tick();

    measurement = monitor.end_window("window3", sync_cuda=False)
    assert_measurement("window3", measurement, begin_time=15, elapsed_time=5)

    tick(); tick(); tick();

    measurement = monitor.end_window("window4", sync_cuda=False)
    assert_measurement("window4", measurement, begin_time=17, elapsed_time=7)


    # Nested windows.
    monitor.begin_window("window5", sync_cuda=False)
    assert_window_begin("window5", 25)

    monitor.begin_window("window6", sync_cuda=False)
    assert_window_begin("window6", 26)

    tick(); tick();

    measurement = monitor.end_window("window6", sync_cuda=False)
    assert_measurement("window6", measurement, begin_time=26, elapsed_time=3)

    tick(); tick(); tick();

    measurement = monitor.end_window("window5", sync_cuda=False)
    assert_measurement("window5", measurement, begin_time=25, elapsed_time=8)