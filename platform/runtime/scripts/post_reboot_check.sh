#!/bin/bash
set -euo pipefail
exec > >(tee -a /var/log/beroun-maintenance/post-reboot-check.log) 2>&1
date -u
cat /proc/sys/kernel/random/boot_id
cat /sys/devices/system/cpu/vulnerabilities/gather_data_sampling
grep -q 'Mitigation' /sys/devices/system/cpu/vulnerabilities/gather_data_sampling
systemctl is-active ssh tailscaled smartmontools postgresql@18-main beroun-agent beroun-gateway beroun-dashboard beroun-ingest beroun-risk_kernel beroun-watchdog
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d beroun -c 'SELECT 1 AS database_ok;'
systemctl start beroun-reconcile.service
echo 'POST REBOOT CHECK: PASS'
