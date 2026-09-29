#!/bin/bash
set -euo pipefail
# Restore an existing archive to an isolated unique database. No production writes.
DUMP=$(find /mnt/data/beroun/backups -maxdepth 1 -name 'beroun_*.dump' -size +0c -printf '%f\n' | sort | tail -1)
test -n "$DUMP"
DUMP="/mnt/data/beroun/backups/$DUMP"
TESTDB="beroun_restore_test_$(date +%s)_$$"
cleanup() { runuser -u postgres -- dropdb --if-exists "$TESTDB"; }
trap cleanup EXIT
runuser -u postgres -- createdb -T template0 "$TESTDB"
# Root reads private archive; PostgreSQL restores using its own OS identity.
runuser -u postgres -- pg_restore --exit-on-error --single-transaction -d "$TESTDB" < "$DUMP"
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d "$TESTDB" -c 'SELECT count(*) AS restored_orders FROM orders; SELECT count(*) AS restored_fills FROM fills;'
echo "RESTORE TEST: PASS archive=$DUMP"
