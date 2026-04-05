//! Standalone test binary for Candle L1 Brain.
//! Run: cargo run --bin candle-brain-test --release

use candle_brain::CandleL1Brain;
use std::time::Instant;

fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt::init();

    println!("=== Candle L1 Brain Test ===");
    println!("Booting (first run downloads ~2.4GB)...");

    let t0 = Instant::now();
    let mut brain = CandleL1Brain::boot_default()?;
    println!("Boot time: {:.1}s", t0.elapsed().as_secs_f64());

    // Run 10 inference cycles and measure latency
    println!("\n=== Inference Benchmark (10 cycles) ===");
    let test_cases = [
        (0.8,  2.0,  0.5,  0.01),  // Strong buy signal
        (-0.7, 3.0, -0.3,  0.02),  // Sell signal
        (0.1,  5.0,  0.0,  0.005), // Neutral
        (0.9,  1.0,  0.8,  0.003), // Very strong buy
        (-0.9, 1.5, -0.9,  0.04),  // Very strong sell
    ];

    for (i, &(obi, spread, delta, vol)) in test_cases.iter().enumerate() {
        brain.reset_cache(); // Reset KV cache per call

        let t = Instant::now();
        let (action, confidence) = brain.reflex_action(obi, spread, delta, vol)?;
        let elapsed_us = t.elapsed().as_micros();

        println!(
            "  [{}/5] OBI={:+.1} SPR={:.1} DLT={:+.1} VOL={:.3} => {:?} ({:.1}%) [{:.1}ms]",
            i + 1, obi, spread, delta, vol,
            action, confidence * 100.0,
            elapsed_us as f64 / 1000.0
        );
    }

    println!("\n=== Test Complete ===");
    Ok(())
}
