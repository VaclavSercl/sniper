use std::sync::atomic::Ordering;

pub fn update_book(levels: &mut [sniper_types::OrderBookLevel; 25], price: u64, amount: i64, count: u64) {
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

pub fn sort_book(levels: &mut [sniper_types::OrderBookLevel; 25], is_bid: bool) {
    levels.sort_unstable_by(|a, b| {
        let pa = a.price.load(Ordering::Acquire);
        let pb = b.price.load(Ordering::Acquire);
        if pa == 0 && pb == 0 { return std::cmp::Ordering::Equal; }
        if pa == 0 { return std::cmp::Ordering::Greater; }
        if pb == 0 { return std::cmp::Ordering::Less; }
        if is_bid { pb.cmp(&pa) } else { pa.cmp(&pb) }
    });
}
