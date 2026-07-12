#!/usr/bin/env bash
# Expose the StoryForge control app to your phone from anywhere via a free
# Cloudflare Tunnel (cloudflared). Prints the public HTTPS URL — open it on your
# phone, paste your APP_TOKEN, and "Add to Home Screen" to install the PWA.
#
# Usage:  bash scripts/setup_tunnel.sh          (uses APP_PORT or 8787)
set -euo pipefail

PORT="${APP_PORT:-8787}"

install_cloudflared() {
  if command -v cloudflared >/dev/null 2>&1; then return; fi
  echo "==> Installing cloudflared"
  if command -v brew >/dev/null 2>&1; then
    brew install cloudflared
  elif command -v winget >/dev/null 2>&1; then
    winget install --id Cloudflare.cloudflared -e || true
  elif command -v apt-get >/dev/null 2>&1; then
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
    sudo apt-get update && sudo apt-get install -y cloudflared
  else
    echo "Please install cloudflared manually: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
  fi
}

install_cloudflared

echo "==> Starting a quick tunnel to http://localhost:${PORT}"
echo "    (Look for the https://<random>.trycloudflare.com URL below — open it on your phone.)"
exec cloudflared tunnel --url "http://localhost:${PORT}"
