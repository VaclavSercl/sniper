fn main() {
    println!("Hydra: {}", std::mem::offset_of!(sniper_types::EngineState, virtual_realized_pnl));
    println!("Grid: {}", std::mem::offset_of!(sniper_types::grid_types::GridEngineState, virtual_realized_pnl));
    println!("Moonshot: {}", std::mem::offset_of!(sniper_types::moonshot_types::MoonshotEngineState, virtual_realized_pnl));
    println!("Trigon: {}", std::mem::offset_of!(sniper_types::trigon_types::TrigonEngineState, virtual_realized_pnl));
    println!("Nexus: {}", std::mem::offset_of!(sniper_types::exchange::cross_types::CrossExchangeState, virtual_realized_pnl));
}
