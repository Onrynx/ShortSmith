"""StoryForge control app — FastAPI backend serving the installable PWA.

Single user. Auth: one bearer token (APP_TOKEN in .env, else auto-generated and
printed). Every /api route + push subscribe checks it (header `Authorization:
Bearer <t>` or `?token=<t>` for media/SW-fetchable URLs).

If FastAPI/uvicorn aren't installed, this module transparently launches the
stdlib fallback server (app/lite_server.py) so the control app runs with zero
pip installs. Reach it from anywhere via Cloudflare Tunnel (scripts/setup_tunnel.sh).
"""
from __future__ import annotations

from pathlib import Path

from storyforge.utils import env

from . import store

PWA_DIR = Path(__file__).resolve().parent / "pwa"
HOST = env("APP_HOST", "0.0.0.0")
PORT = int(env("APP_PORT", "8787") or 8787)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json",
    ".webmanifest": "application/manifest+json", ".png": "image/png",
    ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".mp4": "video/mp4", ".txt": "text/plain; charset=utf-8",
}


def _banner() -> None:
    tok = store.app_token()
    print("=" * 64)
    print("  StoryForge control app")
    print("=" * 64)
    print(f"  Local URL : http://localhost:{PORT}/")
    print(f"  Bearer token (APP_TOKEN): {tok}")
    print("  → open the URL, paste the token, then 'Add to Home Screen'.")
    print("  → expose publicly with scripts/setup_tunnel.sh (Cloudflare Tunnel).")
    if not store.vapid_public_key():
        print("  note: VAPID keys not set — run scripts/gen_vapid.py to enable Web Push.")
    print("=" * 64)


def build_fastapi():
    # NOTE: we intentionally use Header/Query/Body parameter declarations rather
    # than injecting `Request`. Some FastAPI/Starlette builds in the wild mis-read
    # a `request: Request` dependency param as a required query field; Header/Query/
    # Body are unambiguous and portable across versions.
    from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
    from fastapi.responses import FileResponse

    app = FastAPI(title="StoryForge", docs_url=None, redoc_url=None)

    def auth(authorization: str = Header(default=""), token: str = Query(default="")):
        given = authorization[7:] if authorization.lower().startswith("bearer ") else token
        if given != store.app_token():
            raise HTTPException(status_code=401, detail="bad or missing token")
        return True

    # ---- API ---------------------------------------------------------- #
    @app.get("/api/config")
    def api_config(_=Depends(auth)):
        return store.config_info()

    @app.get("/api/status")
    def api_status(_=Depends(auth)):
        return store.status()

    @app.get("/api/pending")
    def api_pending(_=Depends(auth)):
        return store.list_pending()

    @app.get("/api/pending/{item_id}")
    def api_pending_item(item_id: str, _=Depends(auth)):
        m = store.get_pending(item_id)
        if not m:
            raise HTTPException(404, "not found")
        return m

    @app.post("/api/pending/{item_id}/approve")
    def api_approve(item_id: str, _=Depends(auth)):
        return store.approve(item_id)

    @app.post("/api/pending/{item_id}/reject")
    def api_reject(item_id: str, _=Depends(auth)):
        return store.reject(item_id)

    @app.get("/api/history")
    def api_history(_=Depends(auth)):
        return store.history()

    @app.get("/api/events")
    def api_events(limit: int = 100, _=Depends(auth)):
        return store.events(limit)

    @app.get("/api/assignment")
    def api_assignment(_=Depends(auth)):
        return store.get_assignment_public()

    @app.post("/api/assignment")
    def api_set_assignment(payload: dict = Body(default={}), _=Depends(auth)):
        return store.set_lanes(payload.get("night"), payload.get("day"))

    @app.post("/api/run")
    def api_run(payload: dict = Body(default={}), _=Depends(auth)):
        return store.run_now(payload.get("channel"), payload.get("lane", "night"),
                             bool(payload.get("mock", False)))

    @app.post("/api/push/subscribe")
    def api_subscribe(payload: dict = Body(default={}), _=Depends(auth)):
        return store.subscribe_push(payload.get("subscription") or payload)

    @app.get("/api/media/{item_id}/{kind}")
    def api_media(item_id: str, kind: str, _=Depends(auth)):
        p = store.media_path(item_id, kind)
        if not p:
            raise HTTPException(404, "no media")
        return FileResponse(str(p))

    # ---- Static PWA (defined last; catch-all) ------------------------- #
    @app.get("/")
    def index():
        return FileResponse(str(PWA_DIR / "index.html"))

    @app.get("/{path:path}")
    def static_files(path: str):
        target = (PWA_DIR / path).resolve()
        if not str(target).startswith(str(PWA_DIR.resolve())) or not target.is_file():
            raise HTTPException(404, "not found")
        ctype = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        return FileResponse(str(target), media_type=ctype)

    return app


def main() -> int:
    _banner()
    try:
        import uvicorn  # type: ignore
        app = build_fastapi()
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
        return 0
    except ImportError:
        print("[i] FastAPI/uvicorn not installed — starting stdlib fallback server.")
        print("    (pip install -r requirements-app.txt for the full FastAPI backend)")
        from .lite_server import serve
        serve(HOST, PORT)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
