#!/bin/bash
set -euo pipefail
umask 077
BACKUP_DIR=/mnt/data/beroun/backups
COPY_DIR=/opt/beroun/state/backups
exec 9>"$BACKUP_DIR/.backup.lock"
flock -n 9
mountpoint -q /mnt/data
TS=$(date -u +%Y%m%d_%H%M%S)
DUMP="$BACKUP_DIR/beroun_${TS}.dump"
trap 'rm -f "$DUMP.partial" "$COPY_DIR/beroun_${TS}.dump.partial"' EXIT
pg_dump -Fc -U beroun beroun > "$DUMP.partial"
test -s "$DUMP.partial"
pg_restore --list "$DUMP.partial" > /dev/null
mv "$DUMP.partial" "$DUMP"
cp "$DUMP" "$COPY_DIR/beroun_${TS}.dump.partial"
cmp "$DUMP" "$COPY_DIR/beroun_${TS}.dump.partial"
mv "$COPY_DIR/beroun_${TS}.dump.partial" "$COPY_DIR/beroun_${TS}.dump"
ls -1t "$BACKUP_DIR"/beroun_*.dump | tail -n +31 | xargs -r rm -f
ls -1t "$COPY_DIR"/beroun_*.dump | tail -n +8 | xargs -r rm -f
echo "backup ok: $DUMP; verified second local disk copy (not offsite)"
