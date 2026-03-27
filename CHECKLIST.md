# 🐺 BEROUN SNIPER — Change Checklist

When adding a **new feature/field**, update ALL of these:

## New mmap Field (EngineState)
- [ ] `src/types.rs` — struct definition (field + type)
- [ ] `src/types.rs` — Default impl (initial value)
- [ ] `src/main.rs` — Read/write logic in fire loop
- [ ] `scripts/macro_monitor.py` — OFF_* offset constant
- [ ] `scripts/tg_listener.py` — OFF_* offset constant
- [ ] `scripts/l1_shield.py` — offset if L1 reads/writes it
- [ ] `scripts/sniper_orchestrator.py` — offset if L2 tunes it
- [ ] `src/dashboard.rs` — Display in SSE if visible
- [ ] `README.md` — mmap layout table
- [ ] `ARCHITECTURE.md` — if architectural change

## New Telegram Command
- [ ] `scripts/tg_listener.py` — `@bot.message_handler` function
- [ ] `scripts/tg_listener.py` — `BotCommand()` in menu registration list
- [ ] `scripts/tg_listener.py` — `/help` text block
- [ ] `README.md` — Telegram commands table

## New Python Sidecar / Script
- [ ] `beroun-start.sh` — `python3 ... &` launch line
- [ ] `beroun-start.sh` — `pkill -9 -f ...` cleanup line
- [ ] `beroun-sniper.service` — `ExecStartPre=-/usr/bin/pkill -9 -f ...`
- [ ] `watchdog.sh` — heartbeat check if critical

## Rust Binary Change
- [ ] `cargo build --release` — rebuild
- [ ] `sudo systemctl restart beroun-sniper.service` — deploy
- [ ] Verify: `journalctl -u beroun-sniper.service --since "30 sec ago" | grep panic`
- [ ] Verify: mmap size matches new struct (`stat /dev/shm/beroun/engine_state.bin`)

## Version Bump
- [ ] `Cargo.toml` — version field
- [ ] `beroun-start.sh` — header comment + echo
- [ ] `beroun-sniper.service` — Description
- [ ] `watchdog.sh` — version tag
- [ ] `src/dashboard.rs` — version in println!
- [ ] `src/main.rs` — version string in system_start event
- [ ] `scripts/tg_listener.py` — /help header
- [ ] `README.md` — title
- [ ] `ARCHITECTURE.md` — version history table

## Safety: .clamp() Usage
⚠ **NEVER use `.clamp(min, max)` where max can be 0 and min > 0!**
Use `.max(min).min(max.max(min))` instead.

## Deploy Sequence
```bash
cargo build --release
sudo systemctl restart beroun-sniper.service
sleep 15
# Verify:
systemctl is-active beroun-sniper.service
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
journalctl -u beroun-sniper.service --since "10 sec ago" | grep panic
```
