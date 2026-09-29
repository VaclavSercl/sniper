#!/usr/bin/env bash
# BEROUN notifikace → Telegram (§14: messaging je jen výstup).
# Použití: notify_telegram.sh "text hlášení"
# Kanál používá gateway credentials (hermes send), žádný LLM běh.
set -euo pipefail
exec /home/beroun/.local/bin/hermes send --to telegram -q "$1"
