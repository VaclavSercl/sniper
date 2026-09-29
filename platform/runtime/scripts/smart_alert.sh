#!/bin/bash
set -euo pipefail
runuser -u beroun -- /opt/sniper/current/platform/runtime/scripts/alert.sh "SMART:${SMARTD_DEVICE:-unknown}:${SMARTD_FAILTYPE:-unknown}"
logger -p daemon.err -t beroun-smart -- "${SMARTD_MESSAGE:-SMART failure}"
