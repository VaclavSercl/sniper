#!/bin/bash
# BEROUN monthly restore test (§10) — do test DB, nikdy produkční
set -euo pipefail
BACKUP_DIR=/mnt/data/beroun/backups
TESTDB=beroun_restore_test
TS=$(date -u +%Y%m%d_%H%M%S)
PROBE="restore-probe-$(date +%s)"
# 1. testovací data do produkční DB
sudo -u postgres psql -d beroun -q -c "INSERT INTO orders (client_order_id, venue, symbol, side, type, qty, price, status) VALUES ('${PROBE}', 'TEST', 'BTC/USD', 'buy', 'limit', 0.001, 50000, 'INTENT');"
# 2. dump produkční
sudo -u postgres pg_dump -Fc beroun > "$BACKUP_DIR/beroun_${TS}.dump"
# 3. restore do TESTDB
sudo -u postgres dropdb --if-exists $TESTDB
sudo -u postgres createdb $TESTDB
sudo -u postgres pg_restore -d $TESTDB "$BACKUP_DIR/beroun_${TS}.dump"
# 4. porovnání
CNT_PROD=$(sudo -u postgres psql -d beroun -t -A -c "SELECT count(*) FROM orders;")
CNT_TEST=$(sudo -u postgres psql -d $TESTDB -t -A -c "SELECT count(*) FROM orders;")
PROBE_PROD=$(sudo -u postgres psql -d beroun -t -A -c "SELECT count(*) FROM orders WHERE client_order_id LIKE 'restore-probe-%';")
PROBE_TEST=$(sudo -u postgres psql -d $TESTDB -t -A -c "SELECT count(*) FROM orders WHERE client_order_id LIKE 'restore-probe-%';")
echo "orders prod=$CNT_PROD test=$CNT_TEST probe prod=$PROBE_PROD test=$PROBE_TEST"
if [ "$CNT_PROD" = "$CNT_TEST" ] && [ "$PROBE_PROD" = "$PROBE_TEST" ] && [ "$PROBE_PROD" -gt 0 ]; then RESULT=PASS; else RESULT=FAIL; fi
# 5. výsledek do outbox
sudo -u postgres psql -d beroun -q -c "INSERT INTO reports (kind, payload) VALUES ('restore_test', '{\"result\": \"${RESULT}\"}');"
# 6. úklid
sudo -u postgres dropdb $TESTDB
echo "RESTORE TEST: $RESULT"
[ "$RESULT" = "PASS" ] || exit 1
