# Analyze_L2_Oracle_Sync_Trap.md

## 1. Bug Hunting & Security
- **Synchronní AI Polling (GIL Lock)**: Modul `L2Oracle` spouští Gemini CLI pomocí blokovacího callu `subprocess.run(["gemini", ...], timeout=60)`. Počkat celou minutu na odpověď LLM na hlavním Theadu naprosto zastaví L2. Žádné jiné ticky, analýzy, ani Telegram reporty se během generování textu nemohou vykonávat. Tímto designem Oracle zamrzá a stává se single-threaded mrtvolou. 
- **Destruktivní Mmap Cyklení**: Metoda `_write_l2_command` otevírá soubor, volá kernel level `os.ftruncate()`, ihned vytvoří kernel space paměť přes `mmap.mmap`, zapíše byty a obratem ho zavře a zahodí. Otevírání a neustálé formátování stávajících souborů ve složce `/dev/shm` je nebezpečné, protože může pre-emptnout Rust L0 bota v nesprávný čas (`ftruncate` maže size mapping).

## 2. Odstranění Zombie
Mrtvý kód závislý na lokálních IO a blokování je potřeba seřezat na kost.
```python
# ZOMBIE 1: Blokující subprocess 
result = subprocess.run(["gemini", ...])

# ZOMBIE 2: Vytváření mmap na zelené louce každých 5 minut
fd = os.open(L2_CMD_PATH, os.O_RDWR | os.O_CREAT)
os.ftruncate(fd, L2_CMD_SIZE) # NIKDY nedělat ftruncate na soubor, který zrovna čte L0 Rust!!!
mm = mmap.mmap(fd, L2_CMD_SIZE)
os.close(fd)
```

## 3. HFT Optimalizace podle vrstvy (L2 Python)
- **Asynchronní L2 Smyčka**: Celý `run_cycle` se musí přepsat na `async def run_cycle(self)`. Komunikace s `gemini` modelem bude realizována přes neblokující rouru `await asyncio.create_subprocess_exec(...)`. Tím může Python dýchat a dál obsluhovat UDS packety.
- **Bezpečný Mmap Persistence Pattern**: Sdílený stav L2 Commandu musí být nahozen pouze v init fázi `__init__ `. L2 nesmí na souboru dělat truncate. Jen jej `mmap`ne s flagem pro zápis a v každém cyklu mění data v již nabindovaných offsetech na paměťovém ukazateli.
- **Ostranění Regex Parsingu**: Místo manuálního hledání `{` pro čištění LLM halucinací by L2 měla předávat prompty modelem vynuceným `--format=json` nebo používat `gemini-api` pro garantovaný structured output, namísto stříkání outputu do stdoutu.

## 4. Refaktorovaný kód (Návrh Asynchronního L2 Oracla)
```python
import asyncio
import os
import mmap

class L2OracleAsync:
    def __init__(self):
        # 1. Optimalizace: Mapujeme paměť permanentně POUZE pro čtení a zápis
        fd = os.open("/dev/shm/beroun/l2_command.bin", os.O_RDWR)
        # NEPOUŽÍVAT ftruncate uvnitř loopu, zabije záchyt v Rustu L0
        self.cmd_mmap = mmap.mmap(fd, 896)
        
    async def run_cycle(self):
        # ... budování promptu ...
        
        # 2. Optimalizace: Async Process (žádný GIL lock)
        process = await asyncio.create_subprocess_exec(
            "gemini", "-m", "gemini-3.1-pro-preview", "-p", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # S čekáním na AI můžeme provádět paralelní UDS operace
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            return
            
        decision = json.loads(stdout)
        
        # Zápis přímo do keep-alive socketu paměti bez vytváření fd descriptorů
        self._write_l2_command(decision)
        
    def _write_l2_command(self, decision):
        # Zápis SeqLock offsetu (Atomic Operation viz L0 types.rs)
        struct.pack_into('<Q', self.cmd_mmap, 0, next_version)
```
