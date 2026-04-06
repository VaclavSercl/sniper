use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};

/// Lock-free sdílená paměť pro ML váhy (Hot-Swap z Pythonu)
/// Namapováno na: /dev/shm/beroun/ml_weights.bin
#[repr(C, align(64))]
pub struct MlWeightsState {
    pub version: AtomicU64,            // Inkrementováno pythonem po nahrání nového modelu
    pub w_fast: [AtomicU32; 11],       // f32 reprezentovaný jako bity
    pub w_slow: [AtomicU32; 11],
    pub running_mean: [AtomicU32; 10],
    pub running_var: [AtomicU32; 10],
}

impl MlWeightsState {
    /// Přečte verzi modelu. 
    /// Používá Ordering::Acquire k vytvoření paměťové bariéry.
    #[inline(always)]
    pub fn get_version(&self) -> u64 {
        self.version.load(Ordering::Acquire)
    }

    /// Bezpečné vytažení f32 váhy pro rychlou síť
    #[inline(always)]
    pub fn get_w_fast(&self, index: usize) -> f32 {
        f32::from_bits(self.w_fast[index].load(Ordering::Relaxed))
    }

    /// Bezpečné vytažení f32 váhy pro pomalou síť
    #[inline(always)]
    pub fn get_w_slow(&self, index: usize) -> f32 {
        f32::from_bits(self.w_slow[index].load(Ordering::Relaxed))
    }

    /// Bezpečné vytažení průměru pro normalizaci (Z-score)
    #[inline(always)]
    pub fn get_mean(&self, index: usize) -> f32 {
        f32::from_bits(self.running_mean[index].load(Ordering::Relaxed))
    }

    /// Bezpečné vytažení variance pro normalizaci (Z-score)
    #[inline(always)]
    pub fn get_var(&self, index: usize) -> f32 {
        f32::from_bits(self.running_var[index].load(Ordering::Relaxed))
    }
}
