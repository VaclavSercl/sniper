use sniper_types::EngineState;
fn main() {
    println!("ai_heartbeat_ms = {}", std::mem::offset_of!(EngineState, ai_heartbeat_ms));
    println!("l1_skew_adjustment = {}", std::mem::offset_of!(EngineState, l1_skew_adjustment));
    println!("current_ai_bias = {}", std::mem::offset_of!(EngineState, current_ai_bias));
    println!("macro_bias = {}", std::mem::offset_of!(EngineState, macro_bias));
}
