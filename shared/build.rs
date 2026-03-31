// build.rs for sniper-shared
// Auto-generates architect/l2_offsets.py so Python L2 Oracle remains in perfectly synced with Rust structs.

fn main() {
    println!("cargo:rerun-if-changed=src/l2_command.rs");
    println!("cargo:rerun-if-changed=src/moonshot_types.rs");
    println!("cargo:rerun-if-changed=src/grid_types.rs");
    println!("cargo:rerun-if-changed=src/trigon_types.rs");
}
