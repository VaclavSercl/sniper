# 🐺 SNIPER ARMADA — Change Checklist

When adding a **new feature/field**, update ALL of these:

## New mmap Field (EngineState)
- [ ] `shared/src/types.rs` — struct definition (field + type)
- [ ] `shared/src/types.rs` — Default impl (initial value)
- [ ] `hydra/src/main.rs` — Read/write logic in fire loop
- [ ] `hydra/scripts/macro_monitor.py` — OFF_* offset constant
- [ ] `hydra/scripts/tg_listener.py` — OFF_* offset constant
- [ ] `hydra/scripts/l1_shield.py` — offset if L1 reads/writes it
- [ ] `hydra/scripts/sniper_orchestrator.py` — offset if L2 tunes it
- [ ] `hydra/src/dashboard.rs` — Display in SSE if visible
- [ ] `README.md` — mmap layout table
- [ ] `ARCHITECTURE.md` — if architectural change

## New Telegram Command
- [ ] `hydra/scripts/tg_listener.py` — `@bot.message_handler` function
- [ ] `hydra/scripts/tg_listener.py` — `BotCommand()` in menu registration
- [ ] `hydra/scripts/tg_listener.py` — `/help` text block
- [ ] `README.md` — Telegram commands table

## New Python Sidecar / Script
- [ ] `hydra/hydra-start.sh` — `python3 ... &` launch line
- [ ] `hydra/hydra-start.sh` — `pkill -9 -f ...` cleanup line
- [ ] `deploy_armada.sh` — if cross-bot script
- [ ] `watchdog.sh` — heartbeat check if critical

## Rust Binary Change
- [ ] `cargo build --release --workspace` — rebuild
- [ ] `sudo systemctl restart sniper-armada.service` — deploy
- [ ] Verify: `journalctl -u sniper-armada --since "30 sec ago" | grep panic`
- [ ] Verify: mmap size matches struct (`stat /dev/shm/beroun/engine_state.bin`)

## Version Bump
- [ ] `hydra/Cargo.toml` — version field
- [ ] `shared/Cargo.toml` — version field (if types changed)
- [ ] `hydra/hydra-start.sh` — header comment + echo
- [ ] `sniper-armada.service` — Description
- [ ] `hydra/src/dashboard.rs` — version in title
- [ ] `hydra/src/main.rs` — version string in system_start event
- [ ] `hydra/scripts/tg_listener.py` — /help header
- [ ] `README.md` — title
- [ ] `ARCHITECTURE.md` — version history table

## Safety: .clamp() Usage
⚠ **NEVER use `.clamp(min, max)` where max can be 0 and min > 0!**
Use `.max(min).min(max.max(min))` instead.

## Deploy Sequence
```bash
cargo build --release --workspace
sudo systemctl restart sniper-armada.service
sleep 15
# Verify:
systemctl is-active sniper-armada
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
journalctl -u sniper-armada --since "10 sec ago" | grep panic
```
