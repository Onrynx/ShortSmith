#!/usr/bin/env bash
# Set up PewDiePie's Odysseus — a self-hosted AI workspace — as StoryForge's
# preferred "brain" (own your AI instead of renting it).
#
# Odysseus runs at http://localhost:7000 and exposes an OpenAI-compatible /v1
# gateway that fronts your local model backends (Ollama / vLLM / llama.cpp) or
# OpenRouter. StoryForge auto-detects it and routes the Scout/Scribe/Critic crew
# through it. If Odysseus is down, StoryForge falls back to raw Ollama, then
# Gemini, then the canned mock generator — a run never hard-fails.
#
# Repo: https://github.com/pewdiepie-archdaemon/odysseus
#
# Usage (Git Bash / WSL / Linux / macOS):
#   bash scripts/setup_odysseus.sh
set -euo pipefail

ODY_DIR="${ODY_DIR:-$HOME/odysseus}"
ODY_PORT="${ODY_PORT:-7000}"

echo "==> Cloning Odysseus into $ODY_DIR"
if [ ! -d "$ODY_DIR/.git" ]; then
  git clone https://github.com/pewdiepie-archdaemon/odysseus "$ODY_DIR"
else
  echo "    already cloned; pulling latest"
  git -C "$ODY_DIR" pull --ff-only || true
fi

cd "$ODY_DIR"
if [ -f .env.example ] && [ ! -f .env ]; then
  cp .env.example .env
  echo "==> Created $ODY_DIR/.env — review it (model backend, keys) before launch."
fi

echo "==> Starting Odysseus with Docker Compose (http://localhost:${ODY_PORT})"
docker compose up -d --build

echo "==> Waiting for Odysseus to answer on :${ODY_PORT} ..."
for i in $(seq 1 60); do
  if curl -fsS "http://localhost:${ODY_PORT}" >/dev/null 2>&1; then
    echo "    Odysseus is up."
    break
  fi
  sleep 3
done

cat <<EOF

============================================================
Odysseus is running: http://localhost:${ODY_PORT}
  1) Open it and connect a model backend (e.g. your local Ollama):
       Settings → Models → add Ollama (StoryForge already has qwen2.5:14b).
  2) Point StoryForge at it — in the StoryForge .env set:
       ODYSSEUS_URL=http://localhost:${ODY_PORT}
       ODYSSEUS_API_PATH=/v1
       ODYSSEUS_MODEL=<the model id Odysseus exposes, e.g. qwen2.5:14b>
       ODYSSEUS_API_KEY=<key if your Odysseus requires one, else leave default>
  3) StoryForge auto-detects Odysseus and runs the creative crew through it.
     Verify:  python -c "from storyforge.llm import select_model; print(select_model())"
============================================================
EOF
