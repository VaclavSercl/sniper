#!/bin/bash
set -euo pipefail
printf '%s FAILURE %s\n' "$(date -u +%FT%TZ)" "$1" >> /var/log/beroun/alerts.log
logger -p daemon.err -t beroun-alert -- "FAILURE $1"
