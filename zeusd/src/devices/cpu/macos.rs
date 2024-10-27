//! Fake `RaplCpu` implementation to allow development and testing on MacOS.
use std::path::PathBuf;
use std::sync::RwLock;

use crate::devices::cpu::{CpuManager, PackageInfo};
use crate::error::ZeusdError;

pub struct RaplCpu {
    cpu: PackageInfo,
    dram: Option<PackageInfo>
}

impl RaplCpu {
    pub fn init(_index: u32) -> Result<Self, ZeusdError> {
        let fields = RaplCpu::get_available_fields(_index)?;
        Ok(Self {
            cpu: fields.0,
            dram: fields.1
        })
    }
}

impl CpuManager for RaplCpu {
    fn device_count() -> Result<u32, ZeusdError> {
        Ok(1)
    }

    fn get_available_fields(_index: u32) -> Result<(PackageInfo, Option<PackageInfo>), ZeusdError> {
        Ok(
            (
                PackageInfo{
                    index: _index,
                    name: "package-0".to_string(),
                    energy_uj_path: PathBuf::from("/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj"),
                    max_energy_uj: 1000000,
                    num_wraparounds: RwLock::new(0)
                },
                Some(
                    PackageInfo{
                        index: _index,
                        name: "dram".to_string(),
                        energy_uj_path: PathBuf::from("/sys/class/powercap/intel-rapl/intel-rapl:0/intel-rapl:0:0/energy_uj"),
                        max_energy_uj: 1000000,
                        num_wraparounds: RwLock::new(0)
                    }
                )
            )
        )
    }

    fn get_cpu_energy(&self) -> Result<u64, ZeusdError> {
        Ok(10001)
    }

    fn get_dram_energy(&self) -> Result<Option<u64>, ZeusdError> {
        Ok(Some(1001))
    }

    fn stop_monitoring(&mut self) {

    }
}
