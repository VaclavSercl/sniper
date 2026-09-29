#!/usr/bin/env python3
"""
Autonomous 4-Tier AI Cascade Runner for BEROUN
Order of Execution:
  1. Codex CLI (--yolo / --dangerously-bypass-approvals-and-sandbox)
  2. Claude Code CLI (--yolo / --dangerously-skip-permissions)
  3. AGY CLI (--dangerously-skip-permissions)
  4. Hermes Agent (--yolo)
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("cascade_runner")

DEFAULT_CWD = "/opt/sniper/current/platform"
BIN_DIR = "/home/wwwenda/.local/bin"

# Patterns indicating quota, authentication, or rate limit failures
CODEX_FAIL_PATTERNS = [
    "hit your usage limit",
    "quota exceeded",
    "insufficient credits",
    "exceeded your current quota",
    "rate limit reached",
    "error: you've hit your usage limit",
    "billing",
]

CLAUDE_FAIL_PATTERNS = [
    "not logged in",
    "please run /login",
    "429",
    "rate limit",
    "overloaded",
    "credit balance is too low",
    "insufficient_quota",
]

AGY_FAIL_PATTERNS = [
    "resource_exhausted",
    "quota exceeded",
    "rate limit",
    "503",
    "429",
]

HERMES_STRIP_PATTERNS = [
    r"^⚠ A previous `hermes update`.*?Run `hermes update` or `hermes gateway restart`\.\s*",
]


def _get_clean_env() -> Dict[str, str]:
    """Ensure PATH has local bin and load relevant environment files if present."""
    env = os.environ.copy()
    current_path = env.get("PATH", "")
    if BIN_DIR not in current_path.split(":"):
        env["PATH"] = f"{BIN_DIR}:{current_path}"
    return env


def run_tier1_codex(
    prompt: str, cwd: str = DEFAULT_CWD, timeout: int = 60
) -> Tuple[bool, str, str]:
    """1. Tier: Codex CLI (--yolo / --dangerously-bypass-approvals-and-sandbox)
    Příkaz: codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -o <temp_file> "<prompt>"
    """
    codex_bin = os.path.join(BIN_DIR, "codex")
    if not os.path.exists(codex_bin):
        codex_bin = "codex"

    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".codex_out.txt") as tf:
        temp_file = tf.name

    try:
        cmd = [
            codex_bin,
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-o",
            temp_file,
            prompt,
        ]
        logger.debug("Executing Tier 1 (Codex): %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=_get_clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )

        output = ""
        if os.path.exists(temp_file):
            with open(temp_file, "r", encoding="utf-8", errors="replace") as f:
                output = f.read().strip()

        combined_err = f"{proc.stdout}\n{proc.stderr}".lower()

        # Check for failure patterns
        has_fail_pattern = any(pat in combined_err for pat in CODEX_FAIL_PATTERNS)
        if proc.returncode != 0 or not output or has_fail_pattern:
            reason = (
                "Usage limit or quota exceeded"
                if has_fail_pattern
                else f"Exited with code {proc.returncode} (empty={not output})"
            )
            return False, "", f"Codex error: {reason}"

        return True, output, "Codex executed successfully"

    except subprocess.TimeoutExpired:
        return False, "", f"Codex timed out after {timeout}s"
    except Exception as e:
        return False, "", f"Codex execution failed: {e}"
    finally:
        if os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except OSError:
                pass


def run_tier2_claude(
    prompt: str, cwd: str = DEFAULT_CWD, timeout: int = 60
) -> Tuple[bool, str, str]:
    """2. Tier: Claude Code CLI (--yolo)
    Příkaz: claude -p "<prompt>" --dangerously-skip-permissions
    """
    claude_bin = os.path.join(BIN_DIR, "claude")
    if not os.path.exists(claude_bin):
        claude_bin = "claude"

    try:
        cmd = [
            claude_bin,
            "-p",
            prompt,
            "--dangerously-skip-permissions",
        ]
        logger.debug("Executing Tier 2 (Claude): %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=_get_clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )

        output = proc.stdout.strip()
        combined = f"{proc.stdout}\n{proc.stderr}".lower()

        has_fail_pattern = any(pat in combined for pat in CLAUDE_FAIL_PATTERNS)
        if proc.returncode != 0 or not output or has_fail_pattern:
            reason = (
                "Missing login / auth or rate limit"
                if has_fail_pattern
                else f"Exited with code {proc.returncode} (empty={not output})"
            )
            return False, "", f"Claude error: {reason}"

        return True, output, "Claude executed successfully"

    except subprocess.TimeoutExpired:
        return False, "", f"Claude timed out after {timeout}s"
    except Exception as e:
        return False, "", f"Claude execution failed: {e}"


def run_tier3_agy(
    prompt: str, cwd: str = DEFAULT_CWD, timeout: int = 45
) -> Tuple[bool, str, str]:
    """3. Tier: AGY CLI (--dangerously-skip-permissions)
    Příkaz: agy --dangerously-skip-permissions --print "<prompt>"
    """
    agy_bin = os.path.join(BIN_DIR, "agy")
    if not os.path.exists(agy_bin):
        agy_bin = "agy"

    try:
        cmd = [
            agy_bin,
            "--dangerously-skip-permissions",
            "--print",
            prompt,
        ]
        logger.debug("Executing Tier 3 (AGY): %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=_get_clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )

        output = proc.stdout.strip()
        combined = f"{proc.stdout}\n{proc.stderr}".lower()

        has_fail_pattern = any(pat in combined for pat in AGY_FAIL_PATTERNS)
        if proc.returncode != 0 or not output or has_fail_pattern:
            reason = (
                "Rate limit / API error"
                if has_fail_pattern
                else f"Exited with code {proc.returncode} (empty={not output})"
            )
            return False, "", f"AGY error: {reason}"

        return True, output, "AGY executed successfully"

    except subprocess.TimeoutExpired:
        return False, "", f"AGY timed out after {timeout}s"
    except Exception as e:
        return False, "", f"AGY execution failed: {e}"


def run_tier4_hermes(
    prompt: str, cwd: str = DEFAULT_CWD, timeout: int = 90
) -> Tuple[bool, str, str]:
    """4. Tier: Hermes Agent (--yolo)
    Příkaz: hermes -z "<prompt>" --yolo
    """
    hermes_bin = os.path.join(BIN_DIR, "hermes")
    if not os.path.exists(hermes_bin):
        hermes_bin = "hermes"

    try:
        cmd = [
            hermes_bin,
            "-z",
            prompt,
            "--yolo",
        ]
        logger.debug("Executing Tier 4 (Hermes): %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=_get_clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )

        output = proc.stdout
        # Strip warning banner if present
        for pat in HERMES_STRIP_PATTERNS:
            output = re.sub(pat, "", output, flags=re.DOTALL)
        output = output.strip()

        if proc.returncode != 0 or not output:
            return False, "", f"Hermes error: Exited with code {proc.returncode} (empty={not output})"

        return True, output, "Hermes executed successfully"

    except subprocess.TimeoutExpired:
        return False, "", f"Hermes timed out after {timeout}s"
    except Exception as e:
        return False, "", f"Hermes execution failed: {e}"


def execute_cascade(
    prompt: str,
    cwd: str = DEFAULT_CWD,
    show_tier_badge: bool = False,
    timeout_codex: int = 60,
    timeout_claude: int = 60,
    timeout_agy: int = 45,
    timeout_hermes: int = 90,
) -> Tuple[str, str, Dict[str, Any]]:
    """Executes prompt across the 4 tiers in exact priority order:
    1. Codex -> 2. Claude -> 3. AGY -> 4. Hermes
    Returns (response_text, winning_tier, metadata).
    """
    metadata: Dict[str, Any] = {
        "start_time": time.time(),
        "prompt_len": len(prompt),
        "tiers_attempted": [],
    }

    tiers = [
        ("Codex CLI", lambda: run_tier1_codex(prompt, cwd=cwd, timeout=timeout_codex)),
        ("Claude Code CLI", lambda: run_tier2_claude(prompt, cwd=cwd, timeout=timeout_claude)),
        ("AGY CLI", lambda: run_tier3_agy(prompt, cwd=cwd, timeout=timeout_agy)),
        ("Hermes Agent", lambda: run_tier4_hermes(prompt, cwd=cwd, timeout=timeout_hermes)),
    ]

    for tier_name, tier_fn in tiers:
        t0 = time.time()
        logger.info("Attempting cascade tier: %s", tier_name)
        success, response, detail = tier_fn()
        elapsed = round(time.time() - t0, 3)

        metadata["tiers_attempted"].append({
            "tier": tier_name,
            "success": success,
            "elapsed_seconds": elapsed,
            "detail": detail,
        })

        if success and response:
            metadata["winning_tier"] = tier_name
            metadata["total_elapsed_seconds"] = round(time.time() - metadata["start_time"], 3)
            logger.info("Cascade succeeded on tier: %s in %ss", tier_name, elapsed)

            if show_tier_badge:
                response = f"[{tier_name}]\n{response}"

            return response, tier_name, metadata
        else:
            logger.warning("Cascade tier %s failed (%ss): %s", tier_name, elapsed, detail)

    metadata["winning_tier"] = None
    metadata["total_elapsed_seconds"] = round(time.time() - metadata["start_time"], 3)
    error_summary = "\n".join(
        f"- {t['tier']}: {t['detail']} ({t['elapsed_seconds']}s)"
        for t in metadata["tiers_attempted"]
    )
    fallback_response = (
        f"⚠️ Všechny 4 stupně AI kaskády selhaly:\n{error_summary}\n"
        "Prosím zkontrolujte přihlášení a API kvóty."
    )
    return fallback_response, "NONE", metadata


def main():
    parser = argparse.ArgumentParser(description="Autonomous 4-Tier Cascade Runner")
    parser.add_argument("prompt", nargs="*", help="Prompt to execute across the cascade")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging to stderr")
    parser.add_argument("--cwd", default=DEFAULT_CWD, help="Working directory for commands")
    parser.add_argument("--badge", action="store_true", help="Prepend winning tier badge")
    parser.add_argument("--json", action="store_true", help="Output full JSON result")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)

    if args.prompt:
        prompt_text = " ".join(args.prompt)
    else:
        # Read from stdin
        if not sys.stdin.isatty():
            prompt_text = sys.stdin.read().strip()
        else:
            parser.print_help(sys.stderr)
            sys.exit(1)

    if not prompt_text:
        print("Error: Empty prompt provided", file=sys.stderr)
        sys.exit(1)

    resp, winning_tier, meta = execute_cascade(
        prompt=prompt_text,
        cwd=args.cwd,
        show_tier_badge=args.badge,
    )

    if args.json:
        print(json.dumps({"response": resp, "winning_tier": winning_tier, "metadata": meta}, indent=2, ensure_ascii=False))
    else:
        print(resp)

    if winning_tier == "NONE":
        sys.exit(1)


if __name__ == "__main__":
    main()
