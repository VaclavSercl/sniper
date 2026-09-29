#!/bin/bash
# P009/P010 deploy — spustit z owner root shellu (sudo z agenta nefunguje, NoNewPrivileges)
# Usage: sudo bash /opt/sniper/current/platform/legacy/scripts/deploy_p009_p010.sh
set -euo pipefail

UNITS=/opt/sniper/current/platform/legacy/scripts/systemd-units

install -m 644 "$UNITS/beroun-klines.service"      /etc/systemd/system/
install -m 644 "$UNITS/beroun-klines.timer"        /etc/systemd/system/
install -m 644 "$UNITS/beroun-paper-tick.service"  /etc/systemd/system/
install -m 644 "$UNITS/beroun-paper-tick.timer"    /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now beroun-klines.timer
systemctl enable --now beroun-paper-tick.timer

echo "== verify =="
systemctl list-timers 'beroun-klines*' 'beroun-paper*' --no-pager
systemctl start beroun-klines.service
journalctl -u beroun-klines.service -n 3 --no-pager
systemctl start beroun-paper-tick.service
journalctl -u beroun-paper-tick.service -n 3 --no-pager
sudo -u beroun psql -d beroun -Atc "SELECT max(open_time) FROM market_klines;"
echo "DEPLOY OK"
