#!/usr/bin/env bash
# Set up Wan2GP (deepbeepmeep/Wan2GP) — the NIGHT lane's local AI video engine.
# Generates Wan 2.2 hero clips at 480p, offloading to system RAM so it fits a 6GB
# GPU. StoryForge submits hero prompts to its batch queue and polls for the files;
# if it's down or OOMs, the night lane falls back to stock/motion (never fails).
#
# Repo: https://github.com/deepbeepmeep/Wan2GP
#
# Usage (Git Bash / WSL / Linux):  bash scripts/setup_wan2gp.sh
set -euo pipefail

WAN_DIR="${WAN_DIR:-$HOME/Wan2GP}"
WAN_PORT="${WAN_PORT:-7860}"

echo "==> Cloning Wan2GP into $WAN_DIR"
if [ ! -d "$WAN_DIR/.git" ]; then
  git clone https://github.com/deepbeepmeep/Wan2GP "$WAN_DIR"
else
  git -C "$WAN_DIR" pull --ff-only || true
fi
cd "$WAN_DIR"

echo "==> Creating virtualenv"
python -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate 2>/dev/null || source venv/Scripts/activate

echo "==> Installing PyTorch (CUDA 12.1 build — change index-url for your CUDA)"
pip install --upgrade pip
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

echo "==> Installing Wan2GP requirements"
pip install -r requirements.txt

cat <<EOF

============================================================
Wan2GP installed in: $WAN_DIR

LAUNCH IT (low-VRAM profile for a ~6GB GPU + 32GB RAM, 480p):
  cd "$WAN_DIR" && source venv/bin/activate
  python wgp.py \\
      --listen --server-port ${WAN_PORT} \\
      --profile 4 \\           # profile 4/5 = offload model to system RAM (low VRAM)
      --attention sdpa \\      # memory-friendly attention
      --vae-tiling \\          # tile the VAE so decoding fits in VRAM
      --fp16                   # half precision

  Use the 480p Wan 2.2 14B GGUF Q4 model in the UI/queue. Keep resolution 480p and
  clip length short (~5s) so overnight batches finish.

POINT STORYFORGE AT IT — in the StoryForge .env:
  WAN2GP_URL=http://localhost:${WAN_PORT}
  # If your Wan2GP build's API paths differ, override:
  #   WAN2GP_SUBMIT_PATH=/api/queue   WAN2GP_STATUS_PATH=/api/status
  #   WAN2GP_RESULT_PATH=/api/result  WAN2GP_MODEL=wan2.2-14B-i2v-GGUF-Q4
  # Pre-queue overnight, assemble in the morning:
  #   WAN2GP_ASYNC=true   (then re-run the same run to collect + assemble)

Then run the night lane:
  python run.py --channel history_mysteries --lane night
============================================================
EOF
