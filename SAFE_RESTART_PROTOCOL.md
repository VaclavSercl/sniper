# SAFE RESTART PROTOCOL

**CRITICAL WARNING:** This protocol must be executed strictly as written. We have performed a forensic refactor of the underlying L0 Memory Architecture. The `mmap` IPC structs have been physically mutated (`VecDeque` removed, `Mutex` destroyed in favor of lock-free Atomics, padding dimensions altered). 

Attempting to boot the new binaries against the old memory files will trigger an immediate **Segmentation Fault** due to struct alignment and offset corruption. You must annihilate the shared memory region before starting the Sovereign Boot Protocol.

## Execution Sequence

Paste the following block into your terminal on the production server:

```bash
# 1. Engage complete shutdown of the entire Sniper Armada fleet and L2 Intelligence routines
echo "🛑 Initiating Global Fleet Shutdown..."
sudo systemctl stop beroun-sniper.service || true
pkill -9 -f "hydra-core"
pkill -9 -f "moonshot-core"
pkill -9 -f "grid-core"
pkill -9 -f "trigon-core"
pkill -9 -f "nexus-core"
pkill -9 -f "sovereign-cortex"
pkill -f "watchdog.sh"
pkill -f "python.*l2_oracle.py"
pkill -f "python.*dashboard_server.py"

# 2. PURGE THE IPC MEMORY (CRITICAL)
# This destroys all legacy structs mapping to obsolete layouts
echo "🔥 Purging Lock-Contended IPC Memory (/dev/shm/beroun)..."
rm -f /dev/shm/beroun/*

# 3. Clean Build to verify strict zero-warning artifacts
echo "⚙️ Compiling V20 Sovereign Architecture..."
cd /home/wwwenda/sniper
cargo clean -p hydra -p sovereign-cortex -p sniper-shared
cargo build --release

# 4. Initiate the Sovereign Boot Protocol
echo "🚀 Booting Apex Zero-Latency System via SBP..."
sudo systemctl start beroun-sniper.service
```

### Verification Checklist:
1. Confirm zero panics in `journalctl -fu beroun-sniper`.
2. Monitor the Dashboard for instant hardware telemetry reads (Powered by the new Python asynchronous subprocess loop).
3. Confirm in the database that all 0-warning Rust binaries have successfully reconnected to the zero-copy lock-free `mmap` instances.
