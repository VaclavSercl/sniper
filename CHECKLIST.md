# 🐺 SNIPER ARMADA — Change Checklist

When adding a **new feature/field**, update ALL of these:

## New mmap Field (Hydra — EngineState)
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

## New mmap Field (Moonshot — MoonshotState)
- [ ] `shared/src/moonshot_types.rs` — struct definition
- [ ] `shared/src/moonshot_types.rs` — Default impl
- [ ] `moonshot/src/main.rs` — Read/write in engine loop
- [ ] `moonshot/scripts/sniper_orchestrator.py` — OFF_* offset
- [ ] `moonshot/scripts/l1_shield.py` — offset if L1 reads
- [ ] `moonshot/scripts/tg_listener.py` — offset constant
- [ ] `moonshot/src/dashboard.rs` — SSE display
- [ ] `moonshot/src/brain.rs` — SQLite snapshot
- [ ] `README.md` — mmap layout table

## New Telegram Command
- [ ] `hydra/scripts/tg_listener.py` OR `moonshot/scripts/tg_listener.py`
- [ ] `@bot.message_handler` + `BotCommand()` in menu
- [ ] `/help` text block
- [ ] `README.md` — Telegram commands table

## New Python Sidecar / Script
- [ ] `hydra/hydra-start.sh` OR `moonshot/moonshot-start.sh` — launch line
- [ ] Same start script — `pkill -9 -f ...` cleanup line
- [ ] `deploy_armada.sh` — if cross-bot script
- [ ] `watchdog.sh` — heartbeat check if critical

## Rust Binary Change
- [ ] `cargo build --release --workspace` — rebuild
- [ ] `sudo systemctl restart sniper-armada.service` — deploy
- [ ] Verify: `journalctl -u sniper-armada --since "30 sec ago" | grep panic`
- [ ] Verify Hydra mmap: `stat /dev/shm/beroun/engine_state.bin`
- [ ] Verify Moonshot mmap: `stat /dev/shm/beroun/moonshot_engine.bin`

## Version Bump
- [ ] `hydra/Cargo.toml` — version field
- [ ] `moonshot/Cargo.toml` — version field
- [ ] `shared/Cargo.toml` — version field (if types changed)
- [ ] `hydra/hydra-start.sh` — header comment + echo
- [ ] `moonshot/moonshot-start.sh` — header comment + echo
- [ ] `sniper-armada.service` — Description
- [ ] `hydra/src/dashboard.rs` — version in title
- [ ] `moonshot/src/dashboard.rs` — version in title
- [ ] `hydra/src/main.rs` — version string
- [ ] `moonshot/src/main.rs` — VERSION const
- [ ] `README.md` — title + version history
- [ ] `ARCHITECTURE.md` — version history table

## New Bot (Trigon etc.)
- [ ] Create `bot/` directory with standard template
- [ ] `Cargo.toml` (workspace root) — add to members
- [ ] `bot/Cargo.toml` — 4 binaries
- [ ] `bot/src/{main, dashboard, brain, config_cli}.rs`
- [ ] `bot/scripts/{l1_shield, sniper_orchestrator, tg_listener, macro_monitor}.py`
- [ ] `bot/dashboard.html`
- [ ] `bot/bot-start.sh`
- [ ] `deploy_armada.sh` — add launch
- [ ] `README.md / ARCHITECTURE.md / INSTALL.md` — update

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
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000  # Hydra
curl -s -o /dev/null -w "%{http_code}" http://localhost:3001  # Moonshot
journalctl -u sniper-armada --since "10 sec ago" | grep panic
```
