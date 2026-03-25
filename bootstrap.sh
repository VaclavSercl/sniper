#!/bin/bash
# ==============================================================================
# HFT-SNIPER ZERO-TOUCH BOOTSTRAP (v5.1 - 2026 Edition)
# ==============================================================================
# Tento hloupý skript pouze připraví půdu pro Gemini AI. O zbytek instalace
# se už postará AI jakožto autonomní systémový administrátor.

echo "============================================================"
echo "🚀 INICIALIZACE AUTONOMNÍHO AI INSTALÁTORU (SNIPER)..."
echo "============================================================"

# 1. Update základních balíčků a instalace Node.js (nutné pro gemini-cli)
echo "[1/4] Příprava základního prostředí (curl, git, nodejs)..."
sudo apt-get update -y -qq
sudo apt-get install -y -qq curl git
if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y -qq nodejs
fi

# 2. Instalace Gemini CLI
echo "[2/4] Instalace Gemini CLI Mozku..."
if ! command -v gemini &> /dev/null; then
    sudo npm install -g @google/gemini-cli
fi

# 3. Stažení Autonomního Instalačního Promptu z GitHubu
echo "[3/4] Stahování direktiv pro umělou inteligenci..."
PROMPT_FILE="/tmp/AI_SERVER_SETUP_PROMPT.md"
curl -fsSL "https://raw.githubusercontent.com/VaclavSercl/HFT-Sniper/main/AI_SERVER_SETUP_PROMPT.md" -o $PROMPT_FILE

# 4. Spuštění AI Administrátora v YOLO režimu
echo "[4/4] Předávám kontrolu nad serverem Sovereign AI..."
echo "============================================================"
echo "🤖 AI PREBÍRÁ KONTROLU NAD TERMINÁLEM..."
echo "============================================================"

# Spustíme gemini s promptem. AI se zeptá na prostředí a začne pracovat.
gemini -p "$(cat $PROMPT_FILE)" --yolo