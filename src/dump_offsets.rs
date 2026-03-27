use std::sync::atomic::AtomicU64;
use beroun_types::EngineState;

fn main() {
    println!("# Auto-generated mmap offsets for EngineState");
    println!("# Total size: {} bytes", std::mem::size_of::<EngineState>());
    println!("# Align: {} bytes", std::mem::align_of::<EngineState>());
    println!();

    // Use memoffset-style calculation via raw pointer arithmetic
    let base: *const EngineState = std::ptr::null();
    unsafe {
        let f = |name: &str, ptr: *const AtomicU64| {
            println!("{} = {}", name, ptr as usize);
        };
        let fi = |name: &str, ptr: *const std::sync::atomic::AtomicI64| {
            println!("{} = {}", name, ptr as usize);
        };

        f("OFF_BEST_BID", &(*base).best_bid);
        f("OFF_BEST_ASK", &(*base).best_ask);
        println!("OFF_BIDS = {}", &(*base).bids[0] as *const _ as usize);
        println!("OFF_ASKS = {}", &(*base).asks[0] as *const _ as usize);
        f("OFF_MICRO", &(*base).micro_price);
        fi("OFF_OBI", &(*base).l2_imbalance);
        f("OFF_TOXIC", &(*base).toxic_flow_hits);
        f("OFF_SWEEP_FREEZE", &(*base).sweep_freeze_until);
        fi("OFF_L1_SKEW", &(*base).l1_skew_adjustment);
        f("OFF_SESSION_FILLS", &(*base).session_fill_count);
        fi("OFF_NET_POSITION", &(*base).net_position);
        f("OFF_WALLET_BTC", &(*base).wallet_btc);
        f("OFF_WALLET_USD", &(*base).wallet_usd);
    }
}
