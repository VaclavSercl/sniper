use memmap2::MmapOptions;
use crate::ml_types::MlWeightsState; // Tvá struktura z Kroku 1

pub fn load_ml_weights_ro() -> &'static MlWeightsState {
    let path = "/dev/shm/beroun/ml_weights.bin";
    let file = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(_) => {
            eprintln!("⚠️ [ML Shield] ml_weights.bin not found — creating zero-initialized placeholder");
            let size = std::mem::size_of::<MlWeightsState>();
            if let Ok(mut f) = std::fs::File::create(path) {
                use std::io::Write;
                let _ = f.write_all(&vec![0u8; size]);
            }
            std::fs::File::open(path)
                .expect("Cannot open ml_weights.bin even after creating it")
        }
    };
    
    // Otevřeme POUZE PRO ČTENÍ
    let mmap = unsafe { MmapOptions::new().map(&file).unwrap() };
    
    // Leakneme MMap do statické paměti programu (nikdy se neuvolní, což u /dev/shm chceme)
    let mmap_ref = Box::leak(Box::new(mmap));
    
    let state_ptr = mmap_ref.as_ptr() as *const MlWeightsState;
    unsafe { &*state_ptr }
}

pub struct MlShield {
    shared_mmap: &'static MlWeightsState,
    
    // L1 Cache: Boti počítají inferenci z těchto lokálních proměnných!
    local_version: u64,
    cached_w_fast: [f32; 11],
    cached_w_slow: [f32; 11],
    // Můžeme snadno dopsat i mean a var případně kdybychom je v RUSTu potřebovali pro Z-score.
    // Pro ukázku zkopírujeme i mean a var
    cached_running_mean: [f32; 10],
    cached_running_var:  [f32; 10],
}

impl MlShield {
    pub fn new(shared_mmap: &'static MlWeightsState) -> Self {
        let mut shield = Self {
            shared_mmap,
            local_version: 0, // Začínáme na 0, hned na prvním ticku se to updatne
            cached_w_fast: [0.0; 11],
            cached_w_slow: [0.0; 11],
            cached_running_mean: [0.0; 10],
            cached_running_var: [1.0; 10],
        };
        shield.sync_weights_if_needed();
        shield
    }

    /// Zavolá se na ÚPLNÉM ZAČÁTKU každého ticku (L2 snapshotu)
    #[inline(always)]
    pub fn sync_weights_if_needed(&mut self) {
        let current_version = self.shared_mmap.get_version(); // Ordering::Acquire
        
        if current_version > self.local_version {
            // DETEKOVÁNA NOVÁ VERZE Z PYTHONU -> Spouštíme Hot-Swap!
            
            for i in 0..11 {
                self.cached_w_fast[i] = self.shared_mmap.get_w_fast(i);
                self.cached_w_slow[i] = self.shared_mmap.get_w_slow(i);
            }
            
            for i in 0..10 {
                self.cached_running_mean[i] = self.shared_mmap.get_mean(i);
                self.cached_running_var[i] = self.shared_mmap.get_var(i);
            }
            
            self.local_version = current_version;
            println!("[ML Shield] 🧬 Hot-Swap úspěšný! Mozek aktualizován na verzi: {}", current_version);
        }
    }

    /// Vlastní vyhodnocení toxicity
    #[inline(always)]
    pub fn evaluate_toxicity(&self, features: &[f32; 11]) -> f32 {
        let mut score = 0.0;
        // Zde inference probíhá brutálně rychle čistě nad `self.cached_w_fast`
        for i in 0..11 {
            score += features[i] * self.cached_w_fast[i]; 
        }
        score // Toto je zjednodušený dot product, v případě potřeby lze snadno rozšířit o bias/sigmoid/atd.
    }
}
