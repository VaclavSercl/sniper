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

        # 🌙 Moonshot EMA Volatility Engine (O(1) memory)
        self.ema_price = None
        self.ema_var = 0.0
        self.ema_alpha = 0.05  # ~20 period smoothing

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

        # Portfolio Exposure from CL5 (mmap)
        portfolio_section = ""
        try:
            import struct as _st
            PRICE_SCALE = 100_000_000
            with open("/dev/shm/beroun/l2_command.bin", "rb") as f:
                mm_data = f.read(320)  # CL1-5
            if len(mm_data) >= 320:
                CL4, CL5 = 192, 256
                vpin_raw = _st.unpack_from('<q', mm_data, CL4 + 8)[0] / PRICE_SCALE
                aegis_tgt = _st.unpack_from('<q', mm_data, CL4 + 16)[0] / PRICE_SCALE
                urgency = _st.unpack_from('<q', mm_data, CL4 + 24)[0]
                hedged = _st.unpack_from('<q', mm_data, CL4 + 32)[0]
                h_inv = _st.unpack_from('<q', mm_data, CL5 + 0)[0] / PRICE_SCALE
                g_inv = _st.unpack_from('<q', mm_data, CL5 + 8)[0] / PRICE_SCALE
                m_inv = _st.unpack_from('<q', mm_data, CL5 + 16)[0] / PRICE_SCALE
                a_delta = _st.unpack_from('<q', mm_data, CL5 + 24)[0] / PRICE_SCALE
                total_spot = h_inv + g_inv + m_inv
                net = total_spot + a_delta
                portfolio_section = (
                    f"\n═══ PORTFOLIO EXPOSURE (Phase 3 mmap) ═══\n"
                    f"Spot: Hydra={h_inv:.4f} Grid={g_inv:.4f} Moon={m_inv:.4f}\n"
                    f"Total Spot: {total_spot:.4f} BTC\n"
                    f"Aegis Hedge: {a_delta:.4f} BTC (urgency={'EMERGENCY' if urgency else 'passive'})\n"
                    f"Net Exposure: {net:.4f} BTC\n"
                    f"Shield: {'ACTIVE — Grid bids BLOCKED' if hedged else 'OFF'}\n"
                    f"Last VPIN: {vpin_raw:+.2f}\n"
                )
        except Exception:
            portfolio_section = "\n═══ PORTFOLIO EXPOSURE ═══\nUnavailable (mmap not ready)\n"

        # Brain Context (permanent memory)
        brain_section = ""
        try:
            result = subprocess.run(
                ["./target/release/hydra-brain", "context", "--cycles", "3"],
                capture_output=True, text=True, timeout=5,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            if result.returncode == 0 and result.stdout.strip():
                brain_section = f"\n═══ PERMANENT MEMORY (hydra-brain) ═══\n{result.stdout.strip()}\n"
        except Exception:
            pass

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
═══ ARMADA STATE ═══{bot_states}{portfolio_section}{brain_section}
═══ RESPOND WITH THIS JSON ═══
{{"global_reasoning": "Analyze macro + cross-bot correlations + fees + GPU telemetry here FIRST...",
  "global_regime": "BEARISH_SHOCK|BULLISH_TREND|CHOPPING_RANGE",
  "vpin_toxicity": float,
  \"hydra\": {{
    \"recommended_grid_step\": float,
    \"max_position_limit\": float,
    \"pause_trading\": boolean,
    \"bid_fade_bps\": int,
    \"ask_fade_bps\": int,
    \"avellaneda_stoikov\": {{
      \"target_inventory_btc\": float,
      \"rolling_volatility_bps\": float,
      \"gamma\": float
    }}
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
    \"order_qty\": float_or_null,
    \"gaussian_warp\": {{
      \"base_step_bps\": int,
      \"warp_factor\": int
    }}
  }},
  \"trigon\": {{
    \"pause_trading\": boolean,
    \"min_profit_bps\": float,
    \"max_order_usd\": float,
    \"latency_padding_bps\": int,
    \"latency_killswitch\": int
  }},
  \"l1_tuning\": {{\"skew_max_usd\": float, \"obi_threshold\": float, \"inference_interval_ms\": int}}}}

PARAMETER CONSTRAINTS:
  vpin_toxicity: -1.0 to +1.0 (-1=massive dump detected, +1=massive buy, 0=neutral. From order flow imbalance)
  hydra.grid_step: {GRID_FLOOR}-{GRID_CEIL} USD
  hydra.max_position: {MAX_POS_FLOOR}-{MAX_POS_CEIL} BTC
  hydra.bid_fade_bps: 0-20 (0=no fade, 10=defensive, 20=maximum retreat)
  hydra.ask_fade_bps: 0-20 (asymmetric: set different vs bid for directional)
  hydra.avellaneda_stoikov.target_inventory_btc: -1.0 to +1.0 (0=neutral, +0.3=bull ride, -0.1=bear hedge)
  hydra.avellaneda_stoikov.rolling_volatility_bps: 10-200 (from recent price variance)
  hydra.avellaneda_stoikov.gamma: 0.01-0.5 (risk aversion: 0.05=normal, 0.2=aggressive rebalancing)
  moonshot.order_usd: 0-100 USD (0=scanner only)
  moonshot.trigger_price: absolute USD (pre-compute: current_price - 3*sigma)
  moonshot.armed: true only if OI/volume conditions indicate real crash
  grid.grid_spacing: 5-200 USD
  grid.order_qty: 0.0001-0.01 BTC
  trigon.min_profit_bps: 5-50 bps (after 3×taker fee)
  trigon.max_order_usd: 0-50 USD (0=scanner only)
  trigon.latency_padding_bps: 0-30 (added to min_profit as slippage buffer)
  trigon.latency_killswitch: 0 or 1 (L2 auto-sets from p95 > 250ms)
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
            L2_CMD_SIZE = 896  # CL1-5(320) + Ring(576)

            os.makedirs(os.path.dirname(L2_CMD_PATH), exist_ok=True)
            fd = os.open(L2_CMD_PATH, os.O_RDWR | os.O_CREAT)
            os.ftruncate(fd, L2_CMD_SIZE)
            import mmap
            mm = mmap.mmap(fd, L2_CMD_SIZE)
            os.close(fd)

            # Read current version
            cur_ver = _st.unpack_from('<Q', mm, 0)[0]
            next_ver = cur_ver + 1

            # Step 1: Write ODD version (= "writing in progress", L1 will spin)
            _st.pack_into('<Q', mm, 0, next_ver)
            mm.flush()

            # Step 2: Write control fields (Cache Line 1: L2 → L1)
            # Layout: ver(8) + bid_fade(8) + ask_fade(8) + lat_pad(8) + killswitch(8) + pad(24)
            hydra = decision.get("hydra", {})
            bid_fade = int(hydra.get("bid_fade_bps", 0))
            ask_fade = int(hydra.get("ask_fade_bps", 0))
            _st.pack_into('<q', mm, 8, bid_fade)
            _st.pack_into('<q', mm, 16, ask_fade)

            # Trigon latency — L2 reads ring buffer, computes p95, writes padding + killswitch
            trigon = decision.get("trigon", {})
            lat_pad = int(trigon.get("latency_padding_bps", 0))

            # Compute killswitch from ring buffer p95 (Cache Line 2+)
            kill = 0
            try:
                import numpy as np
                head_offset = 320  # L1TelemetryRing at byte 320 (CL1-5 = 5×64)
                head_val = _st.unpack_from('<Q', mm, head_offset)[0]
                if head_val > 0:
                    ring_offset = head_offset + 64  # ring data at byte 384 (after head + pad)
                    count = min(head_val, 64)
                    latencies = []
                    for i in range(count):
                        idx = (head_val - count + i) & 63
                        val = _st.unpack_from('<Q', mm, ring_offset + idx * 8)[0]
                        if val > 0:
                            latencies.append(val / 1000.0)  # µs → ms
                    if latencies:
                        p95 = np.percentile(latencies, 95)
                        if p95 > 250.0:
                            kill = 1  # Exchange overloaded
                            lat_pad = max(lat_pad, 30)
                        elif p95 > 20.0:
                            # +1 bps padding per 10ms over 20ms baseline
                            lat_pad = max(lat_pad, int((p95 - 20) / 10))
                        log.info(f"  📊 Tick-to-Trade p95: {p95:.1f}ms → pad={lat_pad}bps kill={kill}")
            except Exception as e:
                log.debug(f"Ring buffer read skipped: {e}")

            _st.pack_into('<q', mm, 24, lat_pad)
            _st.pack_into('<q', mm, 32, kill)

            # ═══ 🌙 MOONSHOT: EMA Volatility → Trigger Price ═══
            # L2 pre-computes: trigger = ema_price - max(3.5σ, 1.5%)
            # L1 just does: if price < trigger → CAS fire
            moonshot = decision.get("moonshot", {})
            PRICE_SCALE = 100_000_000.0
            Z_TARGET = 3.5

            # Get current BTC price from bots snapshot
            current_price = 0.0
            try:
                snap = self.cortex.get_snapshot()
                if snap.get("ok"):
                    for b in snap["data"].get("bots", []):
                        if b.get("price", 0) > 0:
                            current_price = b["price"]
                            break
            except Exception:
                pass

            trigger_price_scaled = 0
            if current_price > 0:
                import math
                if self.ema_price is None:
                    self.ema_price = current_price

                # EMA variance (Welford online, O(1) memory)
                delta = current_price - self.ema_price
                self.ema_price += self.ema_alpha * delta
                self.ema_var = (1 - self.ema_alpha) * (self.ema_var + self.ema_alpha * delta**2)
                sigma = math.sqrt(self.ema_var)

                # Trigger: anchor to slow EMA, NOT current price (prevents moving target)
                min_drop = self.ema_price * 0.015  # 1.5% minimum absolute drop
                effective_drop = max(Z_TARGET * sigma, min_drop)
                trigger_float = self.ema_price - effective_drop
                trigger_price_scaled = int(trigger_float * PRICE_SCALE)

                log.info(f"  🌙 EMA=${self.ema_price:.0f} σ=${sigma:.1f} "
                         f"trigger=${trigger_float:.0f} ({effective_drop/self.ema_price*100:.2f}% below EMA)")

            _st.pack_into('<q', mm, 40, trigger_price_scaled)

            # Armed flag: L2 sets based on Gemini decision (OI/volume/leverage flush)
            armed = 1 if moonshot.get("armed") else 0
            _st.pack_into('<q', mm, 48, armed)

            # ═══ CACHE LINE 2: A-S Structural Offense (Phase 2) ═══
            # Layout at offset 64: target_inv(8) + skew(8) + half_spread(8) + current_inv(8) + pad(32)
            CL2 = 64  # Cache line 2 starts at byte 64

            hydra_as = hydra.get("avellaneda_stoikov", {})

            # Read L1's current inventory report (written by Rust L1 at CL2+24)
            l1_inventory = _st.unpack_from('<q', mm, CL2 + 24)[0]
            l1_inv_btc = l1_inventory / 100_000_000.0

            # Regime-Aware Target Inventory
            regime = decision.get("global_regime", "CHOPPING_RANGE")
            # Gemini can override, otherwise compute from regime
            target_btc = float(hydra_as.get("target_inventory_btc", 0.0))
            if target_btc == 0.0:
                if "BULL" in regime:
                    target_btc = 0.3   # Ride the wave
                elif "BEAR" in regime:
                    target_btc = -0.1  # Slight short bias
                # CHOPPING_RANGE = 0.0 (delta neutral)

            target_scaled = int(target_btc * 100_000_000)
            _st.pack_into('<q', mm, CL2 + 0, target_scaled)

            # Dynamic Gamma × Variance → Skew Factor
            import math
            rolling_vol_bps = float(hydra_as.get("rolling_volatility_bps", 30.0))
            base_gamma = float(hydra_as.get("gamma", 0.05))

            # Higher regime confidence → higher gamma → more aggressive rebalancing
            regime_strength = 1.0
            if "SHOCK" in regime or "TREND" in regime:
                regime_strength = 3.0  # Extreme: 3× gamma multiplier

            dynamic_gamma = base_gamma * (1.0 + regime_strength)
            variance = rolling_vol_bps ** 2
            skew_factor = max(1, int(dynamic_gamma * variance * 0.1))
            _st.pack_into('<q', mm, CL2 + 8, skew_factor)

            # Optimal Half Spread
            half_spread = max(2, int(math.sqrt(variance) * dynamic_gamma * 2.0))
            _st.pack_into('<q', mm, CL2 + 16, half_spread)

            # Step 3: Write EVEN version for CL1+CL2 (= "data consistent", L1 can read)
            _st.pack_into('<Q', mm, 0, next_ver + 1)

            # ═══ CACHE LINE 3: Grid Gaussian Warp (own SeqLock at offset 128) ═══
            CL3 = 128  # CL1(64) + CL2(64)
            grid_cfg = decision.get("grid", {})
            grid_warp_data = grid_cfg.get("gaussian_warp", {})

            # Grid SeqLock: independent from Hydra's
            grid_ver = _st.unpack_from('<Q', mm, CL3)[0]
            grid_next = grid_ver + 1
            _st.pack_into('<Q', mm, CL3, grid_next)  # ODD = writing

            # Dynamic anchor: use current BTC price as POC (Kalman-filtered)
            anchor_scaled = 0
            if current_price > 0:
                anchor_scaled = int(current_price * PRICE_SCALE)
            _st.pack_into('<q', mm, CL3 + 8, anchor_scaled)

            # Base step (bps) — Gemini can override
            base_step = int(grid_warp_data.get("base_step_bps", 10))
            _st.pack_into('<q', mm, CL3 + 16, base_step)

            # Warp factor: 0 = linear, higher = more quadratic expansion
            # Auto-compute from volatility if not set by Gemini
            warp = int(grid_warp_data.get("warp_factor", 0))
            if warp == 0 and rolling_vol_bps > 0:
                # +1 warp per 20 bps of vol above baseline 20
                warp = max(0, int((rolling_vol_bps - 20) / 20) * 2)
            _st.pack_into('<q', mm, CL3 + 24, warp)

            # Regime-based asymmetric levels
            base_levels = 15
            if "BULL" in regime:
                max_bid_lvl = base_levels + 10  # Deep buy safety net
                max_ask_lvl = 3                 # Don't sell the rocket
            elif "BEAR" in regime:
                max_bid_lvl = 3                 # Don't catch falling knives
                max_ask_lvl = base_levels + 10  # Wait for dead cat bounce
            else:
                max_bid_lvl = base_levels
                max_ask_lvl = base_levels
            _st.pack_into('<q', mm, CL3 + 32, max_bid_lvl)
            _st.pack_into('<q', mm, CL3 + 40, max_ask_lvl)

            _st.pack_into('<Q', mm, CL3, grid_next + 1)  # EVEN = consistent

            # ═══ CACHE LINE 4: Global Risk & VPIN (own SeqLock at offset 192) ═══
            CL4 = 192  # CL1(64) + CL2(64) + CL3(64)
            CL5 = 256  # CL4(64) + CL5 portfolio telemetry

            risk_ver = _st.unpack_from('<Q', mm, CL4)[0]
            risk_next = risk_ver + 1
            _st.pack_into('<Q', mm, CL4, risk_next)  # ODD = writing

            # Read portfolio telemetry from L1 bots (CL5, lock-free reads)
            hydra_inv = _st.unpack_from('<q', mm, CL5 + 0)[0] / PRICE_SCALE
            grid_inv = _st.unpack_from('<q', mm, CL5 + 8)[0] / PRICE_SCALE
            moonshot_inv = _st.unpack_from('<q', mm, CL5 + 16)[0] / PRICE_SCALE
            aegis_delta = _st.unpack_from('<q', mm, CL5 + 24)[0] / PRICE_SCALE

            total_spot = hydra_inv + grid_inv + moonshot_inv
            net_exposure = total_spot + aegis_delta  # Hedged = near 0

            # VPIN-based toxicity (computed from Gemini's assessment or defaults)
            vpin_score = float(decision.get("vpin_toxicity", 0.0))  # -1.0 to +1.0
            vpin_scaled = int(max(-1.0, min(1.0, vpin_score)) * PRICE_SCALE)
            _st.pack_into('<q', mm, CL4 + 8, vpin_scaled)

            # Cross-Bot Hedging Logic
            max_unhedged = 1.0  # Max 1 BTC unhedged spot exposure
            is_crisis = vpin_score < -0.75
            portfolio_hedged = 0
            aegis_target = 0
            urgency = 0

            if is_crisis and total_spot > max_unhedged:
                # SHIELD ACTIVE: short perps to neutralize spot
                aegis_target = int(-total_spot * PRICE_SCALE)
                urgency = 1
                portfolio_hedged = 1
                log.warning(f"  🚨 AEGIS SHIELD! Short {total_spot:.2f} BTC perps (VPIN={vpin_score:.2f})")
            elif not is_crisis and vpin_score > -0.2:
                # All clear: unwind hedge
                aegis_target = 0
                urgency = 0
                portfolio_hedged = 0

            _st.pack_into('<q', mm, CL4 + 16, aegis_target)
            _st.pack_into('<q', mm, CL4 + 24, urgency)
            _st.pack_into('<q', mm, CL4 + 32, portfolio_hedged)

            _st.pack_into('<Q', mm, CL4, risk_next + 1)  # EVEN = consistent

            mm.flush()
            mm.close()

            log.info(f"  📡 L2Cmd: ver={next_ver+1} bid_fade={bid_fade}bps ask_fade={ask_fade}bps "
                     f"lat_pad={lat_pad}bps armed={armed} trig=${trigger_price_scaled / PRICE_SCALE:.0f}")
            log.info(f"  📐 A-S: target={target_btc:.2f}BTC skew={skew_factor}bps/BTC "
                     f"half_spread={half_spread}bps γ={dynamic_gamma:.3f} σ={rolling_vol_bps:.0f}bps "
                     f"L1_inv={l1_inv_btc:.4f}BTC")
            log.info(f"  📐 Grid: anchor=${current_price:.0f} base={base_step}bps warp={warp} "
                     f"bid_lvl={max_bid_lvl} ask_lvl={max_ask_lvl}")
            log.info(f"  👁️ Risk: VPIN={vpin_score:.2f} spot={total_spot:.3f}BTC "
                     f"net={net_exposure:.3f}BTC hedged={'YES' if portfolio_hedged else 'NO'}")

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
