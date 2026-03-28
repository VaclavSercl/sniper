"""
🌐 L2 Strategic Oracle — Left Hemisphere (Python)

5-minute cycle:
  1. GET_SNAPSHOT from Cortex via UDS
  2. Build Gemini prompt with feedback loop
  3. Call gemini CLI
  4. Parse JSON decision
  5. Apply decisions via UDS (SET_GRID, SET_MAXPOS, SET_REGIME, PAUSE)
  6. Send Telegram report

Replaces l2.rs in Cortex — all Gemini calls now in one place.
"""

import json
import subprocess
import logging
import time
import os
from datetime import datetime, timezone, timedelta

log = logging.getLogger("l2_oracle")

# Safety clamps (must match Cortex UDS server)
GRID_FLOOR = 2.0
GRID_CEIL = 50.0
MAX_POS_FLOOR = 0.001
MAX_POS_CEIL = 0.02

L2_INTERVAL = 300  # 5 minutes
GEMINI_TIMEOUT = 60  # seconds


class L2Oracle:
    """Strategic Oracle — the 'frontal lobe' of the Armada."""

    def __init__(self, cortex_client, telegram_send_fn):
        self.cortex = cortex_client
        self.send_telegram = telegram_send_fn
        self.cycle = 0
        self.prev_pnl = 0.0
        self.prev_fills = 0
        self.prev_toxic = 0
        self.prev_decision = None

    def run_cycle(self):
        """Execute one L2 Oracle cycle. Called every 5 min."""
        self.cycle += 1
        log.info(f"═══ L2 ORACLE CYCLE #{self.cycle} ═══")

        # 1. Get live snapshot from Cortex (via UDS → mmap)
        snap_resp = self.cortex.get_snapshot()
        if not snap_resp.get("ok"):
            log.error(f"Cortex offline: {snap_resp.get('error')}")
            self._send_fallback_report("Cortex nedostupny")
            return

        data = snap_resp["data"]
        bots = data.get("bots", [])

        # 1b. Get GPU telemetry (Phi-3.5 performance)
        gpu_resp = self.cortex.get_gpu_stats()
        gpu_data = gpu_resp.get("data", {}) if gpu_resp.get("ok") else {}

        # Log summary
        for b in bots:
            status = "🟢" if b.get("online") else "🔴"
            log.info(f"  {status} {b['name']} ${b['price']:.2f} "
                     f"pos={b['position']:.6f} pnl=${b['pnl']:.4f} "
                     f"grid=${b['grid_step']:.2f} fills={b['fills']}")

        # 2. Build Gemini prompt
        prompt = self._build_prompt(bots, gpu_data)

        # 3. Call Gemini CLI
        log.info("  🤖 Calling Gemini CLI...")
        try:
            result = subprocess.run(
                ["gemini", "-p", prompt],
                capture_output=True, text=True, timeout=GEMINI_TIMEOUT,
            )
            if result.returncode != 0:
                log.error(f"Gemini failed: {result.stderr[:200]}")
                self._send_fallback_report("Gemini chyba")
                return
            raw = result.stdout.strip()
            log.info(f"  ✅ Gemini responded ({len(raw)} bytes)")
        except subprocess.TimeoutExpired:
            log.error("Gemini timeout!")
            self._send_fallback_report("Gemini timeout")
            return
        except Exception as e:
            log.error(f"Gemini error: {e}")
            self._send_fallback_report(str(e))
            return

        # 4. Parse decision
        decision = self._parse_decision(raw)

        # 5. Apply decisions via UDS
        if decision:
            self._apply_decision(decision)
            # Save reasoning to file (for other consumers)
            reasoning = decision.get("global_reasoning", "")
            regime = decision.get("global_regime", "UNKNOWN")
            if reasoning:
                try:
                    with open("/dev/shm/beroun/l2_reasoning.txt", "w") as f:
                        f.write(f"{regime}\n{reasoning}")
                except Exception:
                    pass

        # 6. Send Telegram report
        report = self._build_report(bots, decision)
        try:
            self.send_telegram(report)
        except Exception as e:
            log.error(f"Telegram send failed: {e}")

        # 7. Update feedback history
        hydra = next((b for b in bots if b["name"] == "hydra"), None)
        if hydra:
            self.prev_pnl = hydra["pnl"]
            self.prev_fills = hydra["fills"]
            self.prev_toxic = hydra["toxic"]
        self.prev_decision = decision

        log.info(f"═══ L2 CYCLE #{self.cycle} COMPLETE ═══")

    def _build_prompt(self, bots, gpu_data=None):
        """Build the Gemini prompt with macro context, feedback loop, and GPU telemetry."""
        hydra = next((b for b in bots if b["name"] == "hydra"), None)
        fg = hydra.get("fear_greed", 50) if hydra else 50
        bias = hydra.get("macro_bias", 0.0) if hydra else 0.0

        fg_label = {
            range(0, 25): "Extreme Fear",
            range(25, 50): "Fear",
            range(50, 75): "Greed",
        }
        fg_text = "Extreme Greed"
        for r, label in fg_label.items():
            if fg in r:
                fg_text = label
                break

        bias_label = "Bearish" if bias < -0.3 else ("Bullish" if bias > 0.3 else "Neutral")

        # Feedback loop
        if self.cycle > 1 and self.prev_decision:
            d = self.prev_decision
            prev_regime = d.get("global_regime", d.get("regime", "?"))
            h = d.get("hydra", {})
            prev_grid = h.get("recommended_grid_step", h.get("grid_step", "unchanged"))
            prev_maxp = h.get("max_position_limit", h.get("max_position", "unchanged"))
            current_pnl = hydra["pnl"] if hydra else 0
            pnl_delta = current_pnl - self.prev_pnl
            fills_delta = (hydra["fills"] if hydra else 0) - self.prev_fills
            toxic_delta = (hydra["toxic"] if hydra else 0) - self.prev_toxic

            if pnl_delta > 0:
                assessment = "IMPROVED ✅"
            elif pnl_delta < -0.5:
                assessment = "DEGRADED ❌"
            else:
                assessment = "STABLE"

            feedback = (
                f"\n═══ PREVIOUS CYCLE FEEDBACK ═══\n"
                f"Cycle #{self.cycle - 1}: You set regime={prev_regime}, "
                f"grid={prev_grid}, max_pos={prev_maxp}\n"
                f"Result: PnL ${self.prev_pnl:.4f} → ${current_pnl:.4f} "
                f"({assessment}, delta: ${pnl_delta:+.4f})\n"
                f"New fills: +{fills_delta} | New toxic: +{toxic_delta}\n"
            )
        else:
            feedback = "\n═══ PREVIOUS CYCLE FEEDBACK ═══\nFirst cycle — no prior data available.\n"

        # Bot states
        bot_states = ""
        for b in bots:
            status = "ONLINE" if b.get("online") else "OFFLINE"
            bot_states += (
                f"\n[{b['emoji']} {b['name'].upper()}] {status}\n"
                f"Price=${b['price']:.0f} Spread=${b.get('spread', 0):.2f} "
                f"Pos={b['position']:.6f}BTC PnL=${b['pnl']:.4f}\n"
                f"Grid=${b['grid_step']:.2f}({b.get('grid_levels', 0)}L) "
                f"MaxPos={b.get('max_position', 0):.4f} Fills={b['fills']} Toxic={b['toxic']}\n"
            )

        # Read current fee state for prompt
        fee_info = ""
        try:
            import struct as _st
            with open("/dev/shm/beroun/fee_state.bin", "rb") as f:
                fd = f.read(64)
            maker = _st.unpack_from('<Q', fd, 0)[0] / 100.0
            taker = _st.unpack_from('<Q', fd, 8)[0] / 100.0
            fee_info = f"\nExchange Fees: Maker={maker:.1f}bps Taker={taker:.1f}bps (live from API)\n"
        except Exception:
            fee_info = "\nExchange Fees: ~10bps maker / ~20bps taker (default)\n"

        return f"""You are SNIPER, the Sovereign AI Oracle managing an automated multi-bot trading Armada.
You have FULL AUTHORITY over ALL bots. Analyze macro, fees, and each bot's state.
Make coordinated, profit-maximizing decisions across the entire system.

RULES:
- Grid step MUST be between ${GRID_FLOOR} and ${GRID_CEIL}
- Max position MUST be between {MAX_POS_FLOOR} and {MAX_POS_CEIL} BTC
- If F&G < 20: prefer DEFENSIVE posture (wider grids, lower exposure)
- If toxic > 500: consider pausing or widening grid significantly
- Always fill "global_reasoning" FIRST to establish logic BEFORE setting parameters
- Write global_reasoning in CZECH language (cesky)
- Respond ONLY in valid JSON. No markdown, no prose outside JSON.
- ALL bots share one API key — coordinate to avoid conflicting orders
- Consider fees in ALL profitability calculations
- If GPU total_inferences < 20: keep current l1_tuning defaults (insufficient data)
- If GPU toxic_rate > 20%: reduce skew_max_usd and increase obi_threshold

═══ MACRO INTELLIGENCE ═══
Fear & Greed Index: {fg} ({fg_text})
News Sentiment: {bias:+.4f} ({bias_label})
Cycle: #{self.cycle} (every 5 min)
{fee_info}{feedback}
═══ PHI-3.5 GPU INTELLIGENCE ═══{self._format_gpu_section(gpu_data)}
═══ ARMADA STATE ═══{bot_states}
═══ RESPOND WITH THIS JSON ═══
{{"global_reasoning": "Analyze macro + cross-bot correlations + fees + GPU telemetry here FIRST...",
  "global_regime": "BEARISH_SHOCK|BULLISH_TREND|CHOPPING_RANGE",
  \"hydra\": {{
    \"recommended_grid_step\": float,
    \"max_position_limit\": float,
    \"pause_trading\": boolean,
    \"bid_fade_bps\": int,
    \"ask_fade_bps\": int
  }},
  \"moonshot\": {{
    \"pause_trading\": boolean,
    \"order_usd\": float,
    \"trigger_price\": float_or_null,
    \"armed\": boolean,
    \"drop_pct_override\": float_or_null,
    \"tp_pct_override\": float_or_null
  }},
  \"grid\": {{
    \"pause_trading\": boolean,
    \"grid_spacing\": float_or_null,
    \"order_qty\": float_or_null
  }},
  \"trigon\": {{
    \"pause_trading\": boolean,
    \"min_profit_bps\": float,
    \"max_order_usd\": float,
    \"latency_padding_bps\": int,
    \"latency_killswitch_ms\": int
  }},
  \"l1_tuning\": {{\"skew_max_usd\": float, \"obi_threshold\": float, \"inference_interval_ms\": int}}}}

PARAMETER CONSTRAINTS:
  hydra.grid_step: {GRID_FLOOR}-{GRID_CEIL} USD
  hydra.max_position: {MAX_POS_FLOOR}-{MAX_POS_CEIL} BTC
  hydra.bid_fade_bps: 0-20 (0=no fade, 10=defensive, 20=maximum retreat)
  hydra.ask_fade_bps: 0-20 (asymmetric: set different vs bid for directional)
  moonshot.order_usd: 0-100 USD (0=scanner only)
  moonshot.trigger_price: absolute USD (pre-compute: current_price - 3*sigma)
  moonshot.armed: true only if OI/volume conditions indicate real crash
  grid.grid_spacing: 5-200 USD
  grid.order_qty: 0.0001-0.01 BTC
  trigon.min_profit_bps: 5-50 bps (after 3×taker fee)
  trigon.max_order_usd: 0-50 USD (0=scanner only)
  trigon.latency_padding_bps: 0-30 (added to min_profit as slippage buffer)
  trigon.latency_killswitch_ms: 50-2000 (stop arb if latency exceeds this)
  l1_tuning.skew_max_usd: 0.5-5.0
  l1_tuning.obi_threshold: 0.0-0.8
  l1_tuning.inference_interval_ms: 500-10000"""

    def _format_gpu_section(self, gpu_data):
        """Format GPU telemetry for Gemini prompt."""
        if not gpu_data or gpu_data.get("total_inferences", 0) == 0:
            return "\nGPU offline or no data yet.\n"

        sb = gpu_data.get("skew_bid", {})
        sa = gpu_data.get("skew_ask", {})
        pa = gpu_data.get("pause", {})
        ho = gpu_data.get("hold", {})
        tuning = gpu_data.get("l1_tuning", {})

        return (
            f"\nTotal inferences: {gpu_data.get('total_inferences', 0)}\n"
            f"SKEW_BID: {sb.get('win_rate', 0):.1f}% win ({sb.get('total', 0)}x, {sb.get('toxic', 0)} toxic)\n"
            f"SKEW_ASK: {sa.get('win_rate', 0):.1f}% win ({sa.get('total', 0)}x, {sa.get('toxic', 0)} toxic)\n"
            f"PAUSE:    {pa.get('accuracy', 0):.1f}% accuracy ({pa.get('total', 0)}x)\n"
            f"HOLD:     {ho.get('total', 0)}x\n"
            f"Net PnL impact: ${gpu_data.get('net_pnl_impact_usd', 0):.4f}\n"
            f"Toxic rate: {gpu_data.get('toxic_rate_pct', 0):.1f}%\n"
            f"Current L1 tuning: skew_max=${tuning.get('skew_max_usd', 3.0):.1f} "
            f"obi_thr={tuning.get('obi_threshold', 0.0):.2f} "
            f"interval={tuning.get('inference_interval_ms', 2000)}ms\n"
        )

    def _parse_decision(self, raw):
        """Parse Gemini JSON response with sanitizer."""
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            pass

        # Sanitizer: find first { and last }
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass

        log.warning("Could not parse Gemini JSON")
        return None

    def _apply_decision(self, decision):
        """Apply L2 decisions to ALL bots via UDS + mmap."""

        # ═══ HYDRA ═══
        hydra = decision.get("hydra", {})
        if hydra.get("pause_trading") is True:
            r = self.cortex.pause("hydra")
            log.info(f"  ⏸️ HYDRA PAUSED: {r}")
        elif hydra.get("pause_trading") is False:
            self.cortex.unpause("hydra")

        grid = hydra.get("recommended_grid_step") or hydra.get("grid_step")
        if grid is not None:
            r = self.cortex.set_grid(float(grid))
            log.info(f"  📐 Hydra Grid: ${r.get('prev', '?')} → ${grid}")

        maxp = hydra.get("max_position_limit") or hydra.get("max_position")
        if maxp is not None:
            r = self.cortex.set_maxpos(float(maxp))
            log.info(f"  📦 Hydra MaxPos: {r.get('prev', '?')} → {maxp}")

        # Regime (global)
        regime = decision.get("global_regime") or decision.get("regime")
        if regime:
            self.cortex.set_regime(regime)
            log.info(f"  📈 Regime: {regime}")

        # L1 tuning (Phi-3.5)
        l1 = decision.get("l1_tuning", {})
        if l1:
            skew = float(l1.get("skew_max_usd", 3.0))
            obi = float(l1.get("obi_threshold", 0.0))
            interval = int(l1.get("inference_interval_ms", 2000))
            self.cortex.set_l1_tuning(skew, obi, interval)
            log.info(f"  🤖 L1: skew=${skew:.1f} obi={obi:.2f} int={interval}ms")

        # ═══ MOONSHOT ═══
        moonshot = decision.get("moonshot", {})
        self._apply_moonshot(moonshot)

        # ═══ GRID ═══
        grid_bot = decision.get("grid", {})
        self._apply_grid(grid_bot)

        # ═══ TRIGON ═══
        trigon = decision.get("trigon", {})
        self._apply_trigon(trigon)

        # ═══ L2 COMMAND MATRIX (Issue #18 Quick Wins) ═══
        self._write_l2_command(decision, bots=None)

    def _write_l2_command(self, decision, bots=None):
        """Write L2CommandMatrix to shared mmap with SeqLock protection.

        Layout (64 bytes, matches Rust L2CommandMatrix):
          offset 0:  config_version    (u64) — odd=writing, even=consistent
          offset 8:  bid_fade_bps      (i64) — Hydra bid fade
          offset 16: ask_fade_bps      (i64) — Hydra ask fade
          offset 24: moonshot_trigger   (i64) — pre-computed trigger price
          offset 32: moonshot_armed     (u64) — 0/1
          offset 40: latency_padding    (i64) — Trigon latency pad
          offset 48: latency_killswitch (u64) — ms threshold
          offset 56: l2_heartbeat_ms    (u64)
        """
        try:
            import struct as _st
            L2_CMD_PATH = "/dev/shm/beroun/l2_command.bin"
            L2_CMD_SIZE = 64

            os.makedirs(os.path.dirname(L2_CMD_PATH), exist_ok=True)
            fd = os.open(L2_CMD_PATH, os.O_RDWR | os.O_CREAT)
            os.ftruncate(fd, L2_CMD_SIZE)
            import mmap
            mm = mmap.mmap(fd, L2_CMD_SIZE)
            os.close(fd)

            # Read current version
            cur_ver = _st.unpack_from('<Q', mm, 0)[0]
            next_ver = cur_ver + 1

            # Step 1: Write ODD version (= "writing in progress")
            _st.pack_into('<Q', mm, 0, next_ver)
            mm.flush()

            # Step 2: Write all fields
            hydra = decision.get("hydra", {})
            bid_fade = int(hydra.get("bid_fade_bps", 0))
            ask_fade = int(hydra.get("ask_fade_bps", 0))
            _st.pack_into('<q', mm, 8, bid_fade)
            _st.pack_into('<q', mm, 16, ask_fade)

            # Moonshot trigger price (L2 pre-computes: price - N*sigma)
            moonshot = decision.get("moonshot", {})
            trigger = moonshot.get("trigger_price")
            if trigger is not None:
                PRICE_SCALE = 100_000_000.0
                _st.pack_into('<q', mm, 24, int(float(trigger) * PRICE_SCALE))
            armed = 1 if moonshot.get("armed") else 0
            _st.pack_into('<Q', mm, 32, armed)

            # Trigon latency padding
            trigon = decision.get("trigon", {})
            lat_pad = int(trigon.get("latency_padding_bps", 0))
            lat_kill = int(trigon.get("latency_killswitch_ms", 500))
            _st.pack_into('<q', mm, 40, lat_pad)
            _st.pack_into('<Q', mm, 48, lat_kill)

            # Heartbeat
            import time
            _st.pack_into('<Q', mm, 56, int(time.time() * 1000))

            # Step 3: Write EVEN version (= "data consistent")
            _st.pack_into('<Q', mm, 0, next_ver + 1)
            mm.flush()
            mm.close()

            log.info(f"  📡 L2Cmd: ver={next_ver+1} bid_fade={bid_fade}bps ask_fade={ask_fade}bps "
                     f"trig={'$'+str(trigger) if trigger else 'N/A'} lat_pad={lat_pad}bps")

        except Exception as e:
            log.error(f"L2CommandMatrix write failed: {e}")

    def _apply_moonshot(self, cfg):
        """Apply AI decisions to Moonshot risk mmap."""
        if not cfg:
            return
        try:
            import struct as _st
            MOONSHOT_RISK_PATH = "/dev/shm/beroun/moonshot_risk.bin"
            PAIR_SIZE = 128
            MAX_PAIRS = 20
            GLOBAL_OFF = MAX_PAIRS * PAIR_SIZE
            SCALE = 100_000_000.0

            fd = os.open(MOONSHOT_RISK_PATH, os.O_RDWR)
            import mmap
            mm = mmap.mmap(fd, 0)
            os.close(fd)

            # Pause/unpause
            if cfg.get("pause_trading") is True:
                _st.pack_into('<Q', mm, GLOBAL_OFF, 1)
                log.info("  ⏸️ Moonshot PAUSED")
            elif cfg.get("pause_trading") is False:
                _st.pack_into('<Q', mm, GLOBAL_OFF, 0)
                log.info("  ▶️ Moonshot UNPAUSED")

            # Per-pair order_usd override (all pairs)
            order_usd = cfg.get("order_usd")
            if order_usd is not None:
                for i in range(MAX_PAIRS):
                    sym = _st.unpack_from('<Q', mm, i * PAIR_SIZE)[0]
                    if sym != 0:  # active pair
                        _st.pack_into('<Q', mm, i * PAIR_SIZE + 56, int(float(order_usd) * SCALE))
                log.info(f"  🌙 Moonshot order_usd: ${order_usd}")

            # Drop% and TP% overrides
            drop = cfg.get("drop_pct_override")
            tp = cfg.get("tp_pct_override")
            if drop is not None or tp is not None:
                for i in range(MAX_PAIRS):
                    sym = _st.unpack_from('<Q', mm, i * PAIR_SIZE)[0]
                    if sym != 0:
                        if drop is not None:
                            _st.pack_into('<Q', mm, i * PAIR_SIZE + 8, int(float(drop) * SCALE))
                        if tp is not None:
                            _st.pack_into('<Q', mm, i * PAIR_SIZE + 40, int(float(tp) * SCALE))
                if drop: log.info(f"  🌙 Moonshot drop%: {drop}")
                if tp: log.info(f"  🌙 Moonshot tp%: {tp}")

            # AI heartbeat
            import time
            _st.pack_into('<Q', mm, GLOBAL_OFF + 24, int(time.time() * 1000))

            mm.flush()
            mm.close()
        except Exception as e:
            log.error(f"Moonshot mmap write failed: {e}")

    def _apply_grid(self, cfg):
        """Apply AI decisions to Grid via Cortex UDS."""
        if not cfg:
            return
        if cfg.get("pause_trading") is True:
            self.cortex.pause("grid")
            log.info("  ⏸️ Grid PAUSED")
        elif cfg.get("pause_trading") is False:
            self.cortex.unpause("grid")
            log.info("  ▶️ Grid UNPAUSED")

        spacing = cfg.get("grid_spacing")
        if spacing is not None:
            self.cortex.set_grid(float(spacing), bot="grid")
            log.info(f"  📐 Grid spacing: ${spacing}")

    def _apply_trigon(self, cfg):
        """Apply AI decisions to Trigon risk mmap."""
        if not cfg:
            return
        try:
            import struct as _st
            TRIGON_RISK_PATH = "/dev/shm/beroun/trigon_risk.bin"

            fd = os.open(TRIGON_RISK_PATH, os.O_RDWR)
            import mmap
            mm = mmap.mmap(fd, 0)
            os.close(fd)

            SCALE = 100_000_000.0

            # global_paused at offset 0
            if cfg.get("pause_trading") is True:
                _st.pack_into('<Q', mm, 0, 1)
                log.info("  ⏸️ Trigon PAUSED")
            elif cfg.get("pause_trading") is False:
                _st.pack_into('<Q', mm, 0, 0)
                log.info("  ▶️ Trigon UNPAUSED")

            # min_profit_bps at offset 8
            mpb = cfg.get("min_profit_bps")
            if mpb is not None:
                _st.pack_into('<Q', mm, 8, int(float(mpb) * 100))  # stored as bps×100
                log.info(f"  🔺 Trigon min_profit: {mpb} bps")

            # max_order_usd at offset 16
            mou = cfg.get("max_order_usd")
            if mou is not None:
                _st.pack_into('<Q', mm, 16, int(float(mou) * SCALE))
                log.info(f"  🔺 Trigon max_order: ${mou}")

            mm.flush()
            mm.close()
        except Exception as e:
            log.error(f"Trigon mmap write failed: {e}")

    def _build_report(self, bots, decision):
        """Build Telegram report from snapshot + decision."""
        now = datetime.now(timezone(timedelta(hours=1))).strftime("%H:%M")

        # Health indicator
        hydra = next((b for b in bots if b["name"] == "hydra"), None)
        if hydra:
            toxic = hydra.get("toxic", 0)
            if toxic < 5 and hydra.get("fills", 0) > 0:
                health = "🟢"
            elif toxic < 20:
                health = "🟡"
            else:
                health = "🔴"
        else:
            health = "❓"

        lines = [f"{health} 🧠 L2 ORACLE #{self.cycle} | {now}", "━━━━━━━━━━━━━━━━━━━━━"]

        total_1h = total_24h = total_7d = 0.0
        total_fills = 0

        for b in bots:
            icon = "🟢" if b.get("online") else "🔴"
            lines.append(
                f"\n{icon} {b['emoji']} {b['name'].upper()}\n"
                f"💲 ${b['price']:.2f} | 📦 {b['position']:.5f} BTC | 💰 ${b['pnl']:.4f}\n"
                f"📐 Grid ${b['grid_step']:.2f} | Fills {b['fills']} | Toxic {b['toxic']}"
            )

            # PnL from FIFO
            p1h = b.get("pnl_1h", 0.0)
            p24h = b.get("pnl_24h", 0.0)
            p7d = b.get("pnl_7d", 0.0)
            f24 = b.get("fills_24h", 0)
            ct = b.get("closed_trades_24h", 0)

            if f24 > 0 or abs(p7d) > 0.0001:
                buys = f24 - ct
                lines.append(
                    f"📈 Obchodu: {f24} ({buys} nakup / {ct} prodej)\n"
                    f"💰 PnL: {_fmt_pnl(p1h)} 1h | {_fmt_pnl(p24h)} 24h | {_fmt_pnl(p7d)} 7d"
                )

            total_1h += p1h
            total_24h += p24h
            total_7d += p7d
            total_fills += f24

        if total_fills > 0:
            lines.append(
                f"\n━━━━━━━━━━━━━━━━━━━\n"
                f"Σ PnL: {_fmt_pnl(total_1h)} 1h | {_fmt_pnl(total_24h)} 24h | "
                f"{_fmt_pnl(total_7d)} 7d\n"
                f"Fills 24h: {total_fills}"
            )

        # AI decision
        if decision:
            regime = decision.get("global_regime", decision.get("regime", "?"))
            regime_upper = regime.upper() if regime else ""
            if "BULL" in regime_upper or regime_upper == "TRENDING":
                ri = "📈"
            elif "BEAR" in regime_upper or regime_upper == "CHAOS":
                ri = "🌪️"
            else:
                ri = "↔️"

            lines.append(f"\n{ri} Rezim: {regime}")

            h = decision.get("hydra", {})
            grid = h.get("recommended_grid_step") or h.get("grid_step")
            maxp = h.get("max_position_limit") or h.get("max_position")
            paused = "⏸️ YES" if h.get("pause_trading") else "▶️ NO"
            if grid or maxp:
                lines.append(
                    f"📐 Grid: ${grid or '?'} | MaxPos: {maxp or '?'} | Paused: {paused}"
                )

            reasoning = decision.get("global_reasoning", decision.get("reasoning", ""))
            if reasoning:
                lines.append(f"\n🧠 {reasoning}")

        return "\n".join(lines)

    def _send_fallback_report(self, error_msg):
        """Send minimal report when Gemini is unavailable."""
        now = datetime.now(timezone(timedelta(hours=1))).strftime("%H:%M")
        snap = self.cortex.get_snapshot()

        lines = [
            f"🟡 🧠 L2 ORACLE #{self.cycle} | {now}",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"⚠️ {error_msg}",
        ]

        if snap.get("ok"):
            for b in snap["data"].get("bots", []):
                icon = "🟢" if b.get("online") else "🔴"
                lines.append(
                    f"\n{icon} {b['emoji']} {b['name'].upper()}\n"
                    f"💲 ${b['price']:.2f} | 📦 {b['position']:.5f} BTC | "
                    f"💰 ${b['pnl']:.4f}"
                )

        try:
            self.send_telegram("\n".join(lines))
        except Exception:
            pass


def _fmt_pnl(v):
    """Format PnL value with sign."""
    sign = "+" if v >= 0 else ""
    if abs(v) >= 1000:
        return f"{sign}${v:.0f}"
    elif abs(v) >= 1.0:
        return f"{sign}${v:.2f}"
    else:
        return f"{sign}${v:.4f}"
