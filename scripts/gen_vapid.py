#!/usr/bin/env python
"""One-time VAPID keypair generator for self-hosted Web Push (no 3rd-party service).

Writes the private key PEM to .storyforge_state/vapid_private.pem and prints the
three lines to add to your .env. StoryForge's push.py signs Web Push with the
private key; the PWA subscribes with the public applicationServerKey.

    python scripts/gen_vapid.py

Requires `cryptography` (pip install cryptography). This is the only place it's
needed; the core mock pipeline does not require it.
"""
from __future__ import annotations

import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".storyforge_state"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main() -> int:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except Exception:
        print("[!] Missing dependency. Run:  pip install cryptography")
        return 1

    STATE.mkdir(parents=True, exist_ok=True)
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key()

    raw_pub = pub.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )  # 65 bytes -> browser applicationServerKey
    app_server_key = b64url(raw_pub)

    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pem_path = STATE / "vapid_private.pem"
    pem_path.write_bytes(pem)

    print("=" * 64)
    print("VAPID keypair generated. Add these to your .env:")
    print("=" * 64)
    print(f"VAPID_PUBLIC_KEY={app_server_key}")
    print(f"VAPID_PRIVATE_KEY={pem_path}")
    print("VAPID_SUBJECT=mailto:you@example.com   # your contact email")
    print("=" * 64)
    print(f"(private key PEM written to {pem_path} — keep it secret; it's gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
