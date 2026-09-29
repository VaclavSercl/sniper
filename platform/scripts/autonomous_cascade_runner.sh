#!/usr/bin/env bash
# ==============================================================================
# BEROUN Autonomous 4-Tier Cascade Runner Wrapper
# 1. Codex CLI (--yolo / --dangerously-bypass-approvals-and-sandbox)
# 2. Claude Code CLI (--yolo / --dangerously-skip-permissions)
# 3. AGY CLI (--dangerously-skip-permissions)
# 4. Hermes Agent (--yolo)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_RUNNER="${SCRIPT_DIR}/autonomous_cascade_runner.py"

export PATH="/home/wwwenda/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

if [ -f "/etc/beroun/beroun.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source /etc/beroun/beroun.env 2>/dev/null || true
    set +a
fi

if [ -f "/home/wwwenda/.hermes/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source /home/wwwenda/.hermes/.env 2>/dev/null || true
    set +a
fi

exec python3 "${PYTHON_RUNNER}" "$@"
