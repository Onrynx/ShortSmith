#!/usr/bin/env bash
# StoryForge launcher for macOS / Linux.
#   chmod +x scripts/launch_all.sh && ./scripts/launch_all.sh
# Starts the Discord bot (and Odysseus if you've installed it). Ctrl+C to stop.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v ffmpeg >/dev/null || { echo "❌ ffmpeg missing:  brew install ffmpeg"; exit 1; }
[ -f .env ] || { echo "❌ no .env — copy it first:  cp .env.example .env"; exit 1; }
grep -q "^DISCORD_BOT_TOKEN=." .env || echo "⚠️  DISCORD_BOT_TOKEN is empty in .env — the bot won't start."

if ! curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "⚠️  Ollama isn't running. Start it (open the Ollama app, or 'ollama serve')."
  echo "   Without it, scripts fall back to the built-in canned story."
fi

ODY_DIR="${ODY_DIR:-$HOME/odysseus}"
if [ -d "$ODY_DIR" ] && ! curl -fsS http://localhost:7000 >/dev/null 2>&1; then
  echo "▶ starting Odysseus (optional)…"
  (cd "$ODY_DIR" && docker compose up -d >/dev/null 2>&1 || true)
fi

echo "▶ starting StoryForge Discord bot…  (talk to it in Discord)"
exec python3 -m storyforge.discord_bot
