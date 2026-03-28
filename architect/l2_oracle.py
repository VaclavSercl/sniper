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

        # Log summary
        for b in bots:
            status = "🟢" if b.get("online") else "🔴"
            log.info(f"  {status} {b['name']} ${b['price']:.2f} "
                     f"pos={b['position']:.6f} pnl=${b['pnl']:.4f} "
                     f"grid=${b['grid_step']:.2f} fills={b['fills']}")

        # 2. Build Gemini prompt
        prompt = self._build_prompt(bots)

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

    def _build_prompt(self, bots):
        """Build the Gemini prompt with macro context and feedback loop."""
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

        return f"""You are SNIPER, the Sovereign Oracle Cortex managing an automated BTC trading Armada.
Analyze the global macro environment and the exact state of all sub-bots.
Make highly coordinated, multi-bot strategic decisions to maximize PnL and survive flash crashes.

RULES:
- Grid step MUST be between ${GRID_FLOOR} and ${GRID_CEIL}
- Max position MUST be between {MAX_POS_FLOOR} and {MAX_POS_CEIL} BTC
- If F&G < 20: prefer DEFENSIVE posture (wider grids, lower exposure)
- If toxic > 500: consider pausing or widening grid significantly
- Always fill "global_reasoning" FIRST to establish logic BEFORE setting parameters
- Write global_reasoning in CZECH language (cesky)
- Respond ONLY in valid JSON. No markdown, no prose outside JSON.

═══ MACRO INTELLIGENCE ═══
Fear & Greed Index: {fg} ({fg_text})
News Sentiment: {bias:+.4f} ({bias_label})
Cycle: #{self.cycle} (every 5 min)
{feedback}
═══ ARMADA STATE ═══{bot_states}
═══ RESPOND WITH THIS JSON ═══
{{"global_reasoning": "Analyze macro + cross-bot correlations here FIRST...",
  "global_regime": "BEARISH_SHOCK|BULLISH_TREND|CHOPPING_RANGE",
  "hydra": {{"recommended_grid_step": float, "max_position_limit": float, "pause_trading": boolean}},
  "moonshot": {{"opportunity_bias": "LONG|SHORT|NEUTRAL"}},
  "grid": {{"action": "KEEP_PAUSED|RESUME"}}}}"""

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
        """Apply L2 decisions to Cortex via UDS."""
        hydra = decision.get("hydra", {})

        # Pause/unpause
        if hydra.get("pause_trading") is True:
            r = self.cortex.pause("hydra")
            log.info(f"  ⏸️ HYDRA PAUSED: {r}")
        elif hydra.get("pause_trading") is False:
            self.cortex.unpause("hydra")

        # Grid step
        grid = hydra.get("recommended_grid_step") or hydra.get("grid_step")
        if grid is not None:
            r = self.cortex.set_grid(float(grid))
            prev = r.get("prev", "?")
            log.info(f"  📐 Grid: ${prev} → ${grid}")

        # Max position
        maxp = hydra.get("max_position_limit") or hydra.get("max_position")
        if maxp is not None:
            r = self.cortex.set_maxpos(float(maxp))
            prev = r.get("prev", "?")
            log.info(f"  📦 MaxPos: {prev} → {maxp}")

        # Regime
        regime = decision.get("global_regime") or decision.get("regime")
        if regime:
            self.cortex.set_regime(regime)
            log.info(f"  📈 Regime: {regime}")

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
