use sysinfo::{System, Disks};
use std::process::Command;
use std::time::Instant;
use std::os::unix::fs::PermissionsExt;
use std::fs;

fn main() {
    let mut sys = System::new_all();
    sys.refresh_all();
    let disks = Disks::new_with_refreshed_list();

    println!("--- BEROUN HEALTH CHECK v1.0 (Rust Engine) ---");

    // 1. Kontrola disku
    let mut disk_ok = false;
    for disk in disks.list() {
        let usage = 1.0 - (disk.available_space() as f64 / disk.total_space() as f64);
        println!("[DISK] {} Usage: {:.2}%", disk.mount_point().display(), usage * 100.0);
        if usage < 0.85 { disk_ok = true; }
    }
    if !disk_ok { panic!("DISK SPACE CRITICAL: Over 85% used!"); }

    // 2. Kontrola RAM
    let ram_usage = sys.used_memory() as f64 / sys.total_memory() as f64;
    println!("[RAM] Usage: {:.2}%", ram_usage * 100.0);
    if ram_usage > 0.90 { panic!("RAM CRITICAL: Over 90% used!"); }

    // 3. Kontrola síťové latence (Bitfinex)
    let start = Instant::now();
    let output = Command::new("curl")
        .arg("-I")
        .arg("--silent")
        .arg("https://api.bitfinex.com/v2/platform/status")
        .output();
    
    match output {
        Ok(res) if res.status.success() => {
            println!("[NET] Bitfinex Latency: {:?} (OK)", start.elapsed());
        }
        _ => panic!("NETWORK CRITICAL: Bitfinex API unreachable!"),
    }

    // 4. Kontrola oprávnění .env
    if let Ok(meta) = fs::metadata(".env") {
        let mode = meta.permissions().mode();
        let octal = format!("{:o}", mode & 0o777);
        println!("[AUTH] .env permissions: {} (Required: 600 or 644/660 limit)", octal);
        // Varování při příliš volných právech
        if (mode & 0o007) != 0 {
            println!("! WARNING: .env is world-readable! Consider chmod 600 .env");
        }
    } else {
        println!("! WARNING: .env file missing - ensure environment variables are set!");
    }

    // 5. Kontrola Dockeru (pro MCP servery)
    let docker_status = Command::new("systemctl")
        .arg("is-active")
        .arg("docker")
        .output();
    
    match docker_status {
        Ok(res) if res.status.success() => println!("[SYST] Docker service: ACTIVE"),
        _ => println!("[SYST] Docker service: INACTIVE (MCP might be limited)"),
    }

    println!(">>> DIAGNOSTICS SUCCESSFUL: BEROUN READY TO TRADE <<<");
}
