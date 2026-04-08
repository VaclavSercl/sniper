#!/bin/bash
# 🔧 SNIPER ARMADA — Service Installer
# Installs the systemd service, cleans old issues, and sets up the production environment.
# Usage: sudo ./infra/install_service.sh

set -euo pipefail

ARMADA_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_SRC="$ARMADA_ROOT/infra/sniper-armada.service"
SERVICE_DST="/etc/systemd/system/sniper-armada.service"

echo "🔧 ═══════════════════════════════════════════"
echo "   SNIPER ARMADA v19.0 — Service Installer"
echo "═══════════════════════════════════════════════"

# 1. Check root
if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Must run as root: sudo $0"
    exit 1
fi

# 2. Stop old service if running
echo "[1/6] Stopping old service..."
systemctl stop beroun-sniper.service 2>/dev/null || true
systemctl disable beroun-sniper.service 2>/dev/null || true

# 3. Install new service
echo "[2/6] Installing sniper-armada.service..."
cp "$SERVICE_SRC" "$SERVICE_DST"
chmod 644 "$SERVICE_DST"

# 4. Make scripts executable
echo "[3/6] Setting permissions..."
chmod +x "$ARMADA_ROOT/deploy_armada.sh"
chmod +x "$ARMADA_ROOT/infra/stop_armada.sh"
chmod +x "$ARMADA_ROOT/watchdog.sh" 2>/dev/null || true

# 5. Create /dev/shm/sniper with correct permissions
echo "[4/6] Preparing shared memory..."
mkdir -p /dev/shm/sniper
chown wwwenda:wwwenda /dev/shm/sniper
chmod 700 /dev/shm/sniper

# 6. Enable and configure
echo "[5/6] Enabling service..."
systemctl daemon-reload
systemctl enable sniper-armada.service

# 7. System tuning
echo "[6/6] Applying system tuning..."

# Increase mmap allocation limit for HFT
if ! grep -q "vm.max_map_count=1048576" /etc/sysctl.d/99-sniper.conf 2>/dev/null; then
    cat > /etc/sysctl.d/99-sniper.conf << 'SYSCTL'
# Sniper Armada HFT Tuning
vm.max_map_count=1048576
vm.swappiness=1
net.core.somaxconn=4096
net.ipv4.tcp_fastopen=3
net.ipv4.tcp_tw_reuse=1
SYSCTL
    sysctl -p /etc/sysctl.d/99-sniper.conf 2>/dev/null || true
fi

echo ""
echo "✅ Installation complete!"
echo "   Start:   sudo systemctl start sniper-armada"
echo "   Status:  sudo systemctl status sniper-armada"
echo "   Logs:    journalctl -u sniper-armada -f"
echo "   Old svc: beroun-sniper.service disabled (still available)"
