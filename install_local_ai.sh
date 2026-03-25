#!/bin/bash
# ==============================================================================
# BEROUN LOCAL AI INSTALLER (GPU DETECTION & OLLAMA DEPLOYMENT)
# ==============================================================================
# This script automatically detects the presence of an NVIDIA GPU with at least
# 6GB VRAM. If successful, it installs Ollama and downloads a highly optimized
# 4-bit quantized model (Phi-3 Mini) intended for low-latency HFT pre-filtering.

echo "============================================================"
echo "🐺 BEROUN HFT: Local AI (GPU) Diagnostics & Installer"
echo "============================================================"

# 1. Check for NVIDIA GPU first (easiest detection)
if command -v nvidia-smi &> /dev/null; then
    VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n 1)
    echo "-> Detected NVIDIA GPU via nvidia-smi."
else
    # 2. Fallback: Generic Linux DRM detection (Works for many AMD/Intel cards)
    # Total VRAM is usually exposed in /sys/class/drm/card0/device/mem_info_vram_total (in bytes)
    if [ -f /sys/class/drm/card0/device/mem_info_vram_total ]; then
        VRAM_BYTES=$(cat /sys/class/drm/card0/device/mem_info_vram_total)
        VRAM_MB=$((VRAM_BYTES / 1024 / 1024))
        echo "-> Detected GPU via generic DRM interface."
    else
        # 3. Last resort: lspci check
        GPU_CHECK=$(lspci | grep -E "VGA|3D")
        echo "[!] No high-level VRAM telemetry found (NVIDIA/DRM)."
        echo "[!] Hardware: $GPU_CHECK"
        echo "[!] Skipping automatic installation to prevent system instability."
        exit 0
    fi
fi

echo "-> Detected GPU VRAM: ${VRAM_MB} MB."

# 3. Verify VRAM requirement (We need at least ~5800 MB for a 6GB card)
if [ "$VRAM_MB" -lt 5800 ]; then
    echo "[!] Insufficient VRAM (${VRAM_MB}MB < 6000MB). Local AI requires at least 6GB."
    exit 0
fi

echo "-> GPU meets the 6GB VRAM requirement for 2026 Local LLM Best Practices."
echo "-> Proceeding with Local AI setup..."

# 4. Install Ollama if not present
if ! command -v ollama &> /dev/null; then
    echo "-> Installing Ollama (Systemd Background Service)..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "-> Ollama is already installed."
fi

# 5. Ensure Ollama service is running
echo "-> Starting Ollama service..."
sudo systemctl enable ollama
sudo systemctl start ollama
sleep 5 # Wait for the API to boot

# 6. Pull the optimal model for 6GB VRAM (Phi-3 Mini 4-bit quantized)
# Best Practice: Phi-3 3.8B at Q4_K_M uses ~2.3GB of RAM, leaving plenty of room for Context/KV cache.
MODEL_NAME="phi3:mini"
echo "-> Downloading optimized HFT Local Model: $MODEL_NAME (This may take a few minutes)..."
ollama pull $MODEL_NAME

echo "============================================================"
echo "✅ LOCAL AI SUCCESSFULLY INSTALLED & READY!"
echo "-> Model: $MODEL_NAME"
echo "-> API Endpoint: http://localhost:11434/api/generate"
echo "-> The Rust engines (hft-moonshot/hft-sniper) can now query this endpoint."
echo "============================================================"
