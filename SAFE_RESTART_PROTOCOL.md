# Sovereign HFT 2026: Safe Restart Protocol

Tento dokument definuje bezpečný protokol pro sestřelení staré architektury (která spoléhá na f64 a heap-aligned struktury) a nahození nové `Zero-Allocation/Zero-f64` Sovereign Flotily. 

Vzhledem k masivním změnám v inicializaci Zero-Cost pointerů a MMap souborů hrozí při "hot-plug" restartu *Segmentation Faults* a *Memory Leaks*, pokud staré procesy neuvolní /dev/shm správně, nebo pokud staré memory mmap registry obsadí nové binárky bez přepsání default hodnot.

## Fáze 1: Graceful Fleet Shutdown (Sestřelení staré armády)
Zcela zásadní je neodstřelit procesy bez varování typu SIGKILL (`kill -9`), pokud nechceme zanechat otevřené handly vůči burze. 

```bash
# 1. Uzamčení HFT operací - Simulace Toxic Storm
echo -ne '\x01' > /dev/shm/beroun/toxic_storm.bin

# 2. Bezpečný SIGTERM pro všechny tradery (flotila je zamknuta v Toxic Storm)
pkill -15 -f hydra-core
pkill -15 -f moonshot-core
pkill -15 -f grid-core
pkill -15 -f trigon-core
pkill -15 -f nexus-core

# 3. Zastavení Sovereign Cortex a L2 Oracle (Zeroclaw)
systemctl stop beroun-sniper.service
pkill -15 -f zeroclaw
```

## Fáze 2: SHM State Purge (Očištění /dev/shm)
Staré binárky mohly definovat mmap s odlišnými typy alignmentu nebo chybějícími proměnnými, obzvláště u `L1` datových souborů. Restart musí probíhat čistě (Clear State).

```bash
# Smazání paměťového bloku mmap. Následná spuštění inicializují mmap čistě přes init_mmap pomocí T::default().
rm -rf /dev/shm/beroun/*

# Inicializace čistého Toxic Storm se staženou vlajkou (Zero lock byte)
mkdir -p /dev/shm/beroun
echo -ne '\x00' > /dev/shm/beroun/toxic_storm.bin
```

## Fáze 3: Sovereign Fleet Boot (Nahození čisté, vydestilované paměti)
Start musí proběhnout ve striktním pořadí za plného logování a v PAPER módu pro první validaci z Oracle.

```bash
# 1. Nahrazení binárek
export SNIPER_ROOT="/home/wwwenda/sniper"
cd $SNIPER_ROOT
cargo build --release

# 2. Spuštění L2 Orchestrace
systemctl start beroun-sniper.service

# 3. Aktivace Zeroclaw (pokud není pod systemctl)
# Zkontroluj .env, zda obsahuje správný GEMINI_API_KEY
nohup ./deploy_armada.sh > logs/deploy.log 2>&1 &

# 4. Sledování boot procesu (Sovereign Boot Phase)
tail -f /home/wwwenda/sniper/logs/zeroclaw.log
```

## Fáze 4: HFT Validation (Verifikace latence z HW)
Po spuštění nové infrastruktury v systému L2 musíš provést kontrolu zátěže `L2LatencyRing` - nové zero-f64 smyčky a přímý the pointer de-reference `std::ptr::read_volatile` na Toxic Storm by měly srazit idle L0 overhead do nižších jednotek microsekund.

Ve chvíli potvrzení čistých ticků zaslat PING do telegram bota pro přepsání PAPER do LIVE produkce přes Sovereign AI mechanism.
