// ═════════════════════════════════════════════════════════════
// 📚 L2 Orderbook Management — OBI + Checksum
// ═════════════════════════════════════════════════════════════

use std::sync::atomic::{fence, Ordering};
use crc32fast::Hasher;
use tracing::info;

#[inline]
fn write_bfx(w: &mut impl std::fmt::Write, val: f64) -> std::fmt::Result {
    if val == val.trunc() {
        write!(w, "{:.0}", val)
    } else {
        let mut buf = [0u8; 32];
        let n = {
            use std::io::Write;
            let mut cursor = std::io::Cursor::new(&mut buf[..]);
            let _ = write!(cursor, "{:.12}", val);
            cursor.position() as usize
        };
        let s = unsafe { std::str::from_utf8_unchecked(&buf[..n]) };
        let trimmed = s.trim_end_matches('0').trim_end_matches('.');
        w.write_str(trimmed)
    }
}

pub fn update_book(levels: *mut [sniper_types::OrderBookLevel; sniper_types::BOOK_LEVELS], price: u64, amount: i64, count: u64) {
    let levels = unsafe { &*levels };
    if count > 0 {
        let mut found = false;
        for lvl in levels.iter() {
            if lvl.price.load(Ordering::SeqCst) == price {
                lvl.amount.store(amount, Ordering::SeqCst);
                lvl.count.store(count, Ordering::SeqCst);
                found = true;
                break;
            }
        }
        if !found {
            for lvl in levels.iter() {
                if lvl.count.load(Ordering::SeqCst) == 0 {
                    lvl.price.store(price, Ordering::SeqCst);
                    lvl.amount.store(amount, Ordering::SeqCst);
                    lvl.count.store(count, Ordering::SeqCst);
                    break;
                }
            }
        }
    } else {
        for lvl in levels.iter() {
            if lvl.price.load(Ordering::SeqCst) == price {
                lvl.price.store(0, Ordering::SeqCst);
                lvl.amount.store(0, Ordering::SeqCst);
                lvl.count.store(0, Ordering::SeqCst);
                break;
            }
        }
    }
}

pub fn sort_book(levels: *mut [sniper_types::OrderBookLevel; sniper_types::BOOK_LEVELS], is_bid: bool) {
    let levels = unsafe { &mut *levels };
    levels.sort_unstable_by(|a, b| {
        let pa = a.price.load(Ordering::Acquire);
        let pb = b.price.load(Ordering::Acquire);
        if pa == 0 && pb == 0 { return std::cmp::Ordering::Equal; }
        if pa == 0 { return std::cmp::Ordering::Greater; }
        if pb == 0 { return std::cmp::Ordering::Less; }
        if is_bid { pb.cmp(&pa) } else { pa.cmp(&pb) }
    });
}

pub fn calculate_checksum(engine: &sniper_types::EngineState, debug: bool) -> i32 {
    fence(Ordering::SeqCst);
    let mut hasher = Hasher::new();
    let mut levels_found: u32 = 0;
    // Stack-only scratch buffer for write_bfx formatting (no heap)
    let mut fmt_buf = arrayvec::ArrayString::<64>::new();

    for i in 0..25 {
        let bid = &engine.bids[i];
        let ask = &engine.asks[i];
        let bp = bid.price.load(Ordering::SeqCst);
        let bc = bid.count.load(Ordering::SeqCst);
        let ap = ask.price.load(Ordering::SeqCst);
        let ac = ask.count.load(Ordering::SeqCst);

        if bc > 0 && bp > 0 {
            if levels_found > 0 { hasher.update(b":"); }
            levels_found += 1;
            let p = bp as f64 / sniper_types::PRICE_SCALE;
            let a = bid.amount.load(Ordering::SeqCst) as f64 / sniper_types::PRICE_SCALE;
            fmt_buf.clear();
            let _ = write_bfx(&mut fmt_buf, p);
            hasher.update(fmt_buf.as_bytes());
            hasher.update(b":");
            fmt_buf.clear();
            let _ = write_bfx(&mut fmt_buf, a);
            hasher.update(fmt_buf.as_bytes());
        }
        if ac > 0 && ap > 0 {
            if levels_found > 0 { hasher.update(b":"); }
            levels_found += 1;
            let p = ap as f64 / sniper_types::PRICE_SCALE;
            let a = ask.amount.load(Ordering::SeqCst) as f64 / sniper_types::PRICE_SCALE;
            fmt_buf.clear();
            let _ = write_bfx(&mut fmt_buf, p);
            hasher.update(fmt_buf.as_bytes());
            hasher.update(b":");
            fmt_buf.clear();
            let _ = write_bfx(&mut fmt_buf, a);
            hasher.update(fmt_buf.as_bytes());
        }
    }

    if debug || levels_found == 0 {
        info!(event = "checksum_debug", levels = levels_found,
              bids0_p = engine.bids[0].price.load(Ordering::SeqCst),
              bids0_c = engine.bids[0].count.load(Ordering::SeqCst),
              asks0_p = engine.asks[0].price.load(Ordering::SeqCst),
              asks0_c = engine.asks[0].count.load(Ordering::SeqCst));
    }

    hasher.finalize() as i32
}
