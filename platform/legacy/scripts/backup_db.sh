#!/bin/bash
# BEROUN backup (§10) — dle envelope: backup.pg_dump_format=custom,
# daily 06:30 UTC, retention 30, offsite 7, stale alert 25 h
set -euo pipefail
BACKUP_DIR=/mnt/data/beroun/backups
OFFSITE_DIR=/opt/beroun/state/backups
TS=$(date -u +%Y%m%d_%H%M%S)
DUMP="$BACKUP_DIR/beroun_${TS}.dump"
mkdir -p "$BACKUP_DIR" "$OFFSITE_DIR"
# 1. dump (custom format)
sudo -u postgres pg_dump -Fc beroun > "$DUMP" 2>/dev/null || \
  pg_dump -Fc -U beroun beroun > "$DUMP"
# 2. sanity: soubor existuje a je >0 B
[ -s "$DUMP" ] || { echo "BACKUP FAILED: empty dump"; exit 1; }
# 3. retence na DATA (30)
ls -1t "$BACKUP_DIR"/beroun_*.dump | tail -n +31 | xargs -r rm -f
# 4. offsite kopie na SSD (jiný disk), retence 7
cp "$DUMP" "$OFFSITE_DIR/"
ls -1t "$OFFSITE_DIR"/beroun_*.dump | tail -n +8 | xargs -r rm -f
# 5. staleness kontrola (poslední dump nesmí být starší než 25 h)
LATEST=$(ls -1t "$BACKUP_DIR"/beroun_*.dump | head -1)
AGE_H=$(( ($(date +%s) - $(stat -c %Y "$LATEST")) / 3600 ))
if [ "$AGE_H" -ge 25 ]; then echo "ALERT: newest backup is ${AGE_H}h old"; exit 2; fi
echo "backup ok: $DUMP ($(stat -c %s "$DUMP") B), offsite copied"
