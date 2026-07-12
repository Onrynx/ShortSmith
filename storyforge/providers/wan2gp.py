"""LANE A (night / local) hero-clip provider: a client for the local WanGP API.

Wan2GP (github.com/deepbeepmeep/Wan2GP) runs Wan 2.2 locally and exposes a
headless API. We submit each hero prompt to its BATCH QUEUE at 480p and poll for
the rendered file. Because the night lane is meant to "queue before you sleep,
assemble in the morning," submitted job ids are PERSISTED per run+scene: the
evening run enqueues, and when the morning cron re-runs the same run_id LangGraph
resumes at canvas and this provider COLLECTS the finished clips instead of
resubmitting.

The exact WanGP HTTP surface varies by build/version, so endpoints are fully
configurable via env and every failure is recoverable — a hero clip that OOMs or
never finishes simply falls back to stock (and fires a 🟡 notification upstream).

Env:
  WAN2GP_URL            base url (default http://localhost:7860)
  WAN2GP_SUBMIT_PATH    default /api/queue
  WAN2GP_STATUS_PATH    default /api/status         (append ?id=<job>)
  WAN2GP_RESULT_PATH    default /api/result         (append ?id=<job>)
  WAN2GP_WIDTH/HEIGHT   default 832x480 (480p landscape)
  WAN2GP_FRAMES         default 81  (~5s @ 16fps)
  WAN2GP_POLL_TIMEOUT   seconds to wait when collecting (default 2400)
  WAN2GP_ASYNC          "true" => submit-only, raise Deferred so a later cron collects
"""
from __future__ import annotations

import time
from pathlib import Path

from ..utils import (env, env_bool, host_reachable, http_download, http_get_json,
                     http_post_json, log, read_json, write_json)
from .base import GEN_NEGATIVE, ProviderError, VisualProvider


class DeferredRender(ProviderError):
    """Raised in async mode after enqueue — the morning cron will collect."""


class Wan2GPProvider(VisualProvider):
    name = "wan2gp"

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}
        self.base = (env("WAN2GP_URL", "http://localhost:7860") or "").rstrip("/")
        self.submit_path = env("WAN2GP_SUBMIT_PATH", "/api/queue")
        self.status_path = env("WAN2GP_STATUS_PATH", "/api/status")
        self.result_path = env("WAN2GP_RESULT_PATH", "/api/result")
        self.width = int(env("WAN2GP_WIDTH", "832") or 832)
        self.height = int(env("WAN2GP_HEIGHT", "480") or 480)
        self.frames = int(env("WAN2GP_FRAMES", "81") or 81)
        self.poll_timeout = float(env("WAN2GP_POLL_TIMEOUT", "2400") or 2400)
        self.async_mode = env_bool("WAN2GP_ASYNC", False)

    def available(self) -> bool:
        return bool(self.base) and host_reachable(self.base, timeout=4)

    # -- job bookkeeping (persist across evening enqueue / morning collect) -- #
    def _jobs_file(self, workdir: str) -> Path:
        return Path(workdir) / "wan_jobs.json"

    def _get_job(self, workdir: str, idx: int) -> str | None:
        return (read_json(self._jobs_file(workdir), {}) or {}).get(str(idx))

    def _set_job(self, workdir: str, idx: int, job_id: str) -> None:
        jobs = read_json(self._jobs_file(workdir), {}) or {}
        jobs[str(idx)] = job_id
        write_json(self._jobs_file(workdir), jobs)

    # -- API calls (best-effort; adjust paths to your WanGP build) --------- #
    def _submit(self, prompt: str) -> str:
        payload = {
            "prompt": prompt,
            "negative_prompt": "text, watermark, logo, blurry, deformed",
            "width": self.width, "height": self.height,
            "num_frames": self.frames, "fps": 16,
            "model": env("WAN2GP_MODEL", "wan2.2-14B-i2v-GGUF-Q4"),
        }
        data = http_post_json(self.base + self.submit_path, payload, timeout=30)
        if isinstance(data, dict):
            job = data.get("id") or data.get("job_id") or data.get("task_id")
            if job:
                return str(job)
        raise ProviderError(f"WanGP submit returned no job id: {str(data)[:200]}")

    def _poll_and_download(self, job_id: str, dest: Path) -> str:
        deadline = time.time() + self.poll_timeout
        while time.time() < deadline:
            try:
                status = http_get_json(f"{self.base}{self.status_path}?id={job_id}", timeout=15)
            except Exception as exc:  # noqa: BLE001
                log("warn", f"WanGP status poll error: {exc}")
                time.sleep(10)
                continue
            state = str(status.get("status", status.get("state", ""))).lower()
            if state in ("done", "completed", "success", "finished"):
                url = status.get("output") or status.get("file") or \
                    f"{self.base}{self.result_path}?id={job_id}"
                http_download(url, dest, timeout=180)
                return str(dest)
            if state in ("error", "failed", "oom"):
                raise ProviderError(f"WanGP job {job_id} failed: {state}")
            time.sleep(12)
        raise ProviderError(f"WanGP job {job_id} timed out after {self.poll_timeout}s")

    def generate(self, scene: dict, workdir: str, seconds: float) -> str:
        if not self.available():
            raise ProviderError(f"WanGP not reachable at {self.base}")
        idx = int(scene.get("_index", 0))
        dest = Path(workdir) / f"wan_{idx:02d}.mp4"
        prompt = (scene.get("image_prompt", "") + GEN_NEGATIVE).strip()

        job = self._get_job(workdir, idx)
        if job is None:
            job = self._submit(prompt)
            self._set_job(workdir, idx, job)
            log("info", f"WanGP queued hero scene {idx+1} -> job {job}")
            if self.async_mode:
                raise DeferredRender(f"scene {idx+1} enqueued (job {job}); collect on next run")
        # collect (blocking) — resumes cleanly for already-queued jobs
        return self._poll_and_download(job, dest)
