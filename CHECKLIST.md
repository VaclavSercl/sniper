# 🐺 SNIPER ARMADA v12.0 — Change Checklist

When adding a **new feature/field**, update ALL of these:

## New mmap Field (Hydra — EngineState)
- [ ] `shared/src/types.rs` — struct definition (field + type)
- [ ] `shared/src/types.rs` — Default impl (initial value)
- [ ] `hydra/src/main.rs` — Read/write logic in fire loop
- [ ] `hydra/scripts/l1_shield.py` — OFF_* offset constant
- [ ] `hydra/scripts/sniper_orchestrator.py` — offset if L2 tunes it
- [ ] `hydra/src/dashboard.rs` — Display in SSE if visible
- [ ] `ARCHITECTURE.md` — if architectural change

## New mmap Field (Other Bots)
- [ ] `shared/src/{moonshot,grid,trigon}_types.rs` — struct
- [ ] `{bot}/src/main.rs` — Read/write in engine loop
- [ ] `{bot}/src/dashboard.rs` — SSE display
- [ ] `{bot}/src/brain.rs` — SQLite snapshot

## New Telegram Command
- [ ] `architect/tg_commander.py` — handler + Gemini intent
- [ ] Update `/help` text block
- [ ] README.md — Telegram commands table

## New PnL Integration
- [ ] `shared/pnl_engine.py` — BOT_INDEX entry
- [ ] `architect/pnl_daemon.py` — _get_symbols() mapping
- [ ] Verify FIFO queue serialization

## Rust Binary Change
- [ ] `cargo build --release --workspace` — rebuild
- [ ] `sudo systemctl restart sniper-armada.service` — deploy
- [ ] Verify: `journalctl -u sniper-armada --since "30 sec ago" | grep panic`

## Python Service Change
- [ ] Restart: `pkill -f {service}.py && nohup python3 architect/{service}.py &`
- [ ] Verify: `pgrep -af {service}`

## New Bot
- [ ] Create `bot/` directory with standard template
- [ ] `Cargo.toml` (workspace root) — add to members
- [ ] `bot/Cargo.toml` — 4 binaries
- [ ] `bot/src/{main, dashboard, brain, config_cli}.rs`
- [ ] `bot/dashboard.html`
- [ ] `bot/bot-start.sh`
- [ ] `shared/src/{bot}_types.rs` + register in `lib.rs`
- [ ] `architect/tg_commander.py` — BOTS dict entry
- [ ] `architect/pnl_daemon.py` — BOT_INDEX + _get_symbols
- [ ] `deploy_armada.sh` — optional auto-start
- [ ] `README.md / ARCHITECTURE.md / INSTALL.md`

## Version Bump
- [ ] `sniper-armada.service` — Description
- [ ] `README.md` — title + version history
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
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000  # 200
curl -s -o /dev/null -w "%{http_code}" http://localhost:3004  # 200
journalctl -u sniper-armada --since "10 sec ago" | grep panic
```
