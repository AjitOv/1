"""Hedra v3 renderer: text-to-speech, then audio-driven avatar video from a portrait photo.

Two paid steps per Short (prices from the Hedra catalog, Sep 2026):
  * TTS   minimax-speech-25-turbo-preview  ~7¢ per 1,000 characters  (a script is ~600 chars)
  * Video hedra-avatar                     540p 2.5¢/s, 720p 5¢/s, 1080p 6.25¢/s
So a 40 s Short costs roughly $1.05 at 540p or $2.05 at 720p.

Endpoints (per the model schema): POST /v3/files, POST /v3/models/{model}, GET /v3/jobs/{job_id}.
Set HEDRA_API_KEY; override HEDRA_API_BASE / HEDRA_AUTH_HEADER if your account uses a different host.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from .config import Channel

TTS_MODEL = os.environ.get("HEDRA_TTS_MODEL", "minimax-speech-25-turbo-preview")
AVATAR_MODEL = os.environ.get("HEDRA_AVATAR_MODEL", "hedra-avatar")


class HedraError(RuntimeError):
    pass


class Hedra:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.key = api_key or os.environ.get("HEDRA_API_KEY")
        if not self.key:
            raise HedraError("HEDRA_API_KEY is not set")
        self.base = os.environ.get("HEDRA_API_BASE", "https://api.hedra.com/v3").rstrip("/")
        header = os.environ.get("HEDRA_AUTH_HEADER", "X-API-Key")
        self.s = session or requests.Session()
        self.s.headers.update({header: self.key})
        self._uploads: dict[str, str] = {}

    # -- files -------------------------------------------------------------
    def upload(self, path: Path) -> str:
        """Upload a local file once; returns the URL to reference it by."""
        key = str(path.resolve())
        if key in self._uploads:
            return self._uploads[key]
        with open(path, "rb") as f:
            r = self.s.post(f"{self.base}/files", files={"file": (path.name, f)}, timeout=120)
        data = self._check(r)
        url = data.get("url") or (data.get("file") or {}).get("url")
        if not url:
            raise HedraError(f"upload returned no url: {data}")
        self._uploads[key] = url
        return url

    # -- jobs --------------------------------------------------------------
    def submit(self, model: str, inp: dict, idempotency_key: str | None = None) -> str:
        body = {"input": inp}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        r = self.s.post(f"{self.base}/models/{model}", json=body, timeout=60)
        data = self._check(r)
        jid = data.get("job_id") or data.get("id")
        if not jid:
            raise HedraError(f"no job id in response: {data}")
        return jid

    def wait(self, job_id: str, timeout_s: int = 1800) -> dict:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            r = self.s.get(f"{self.base}/jobs/{job_id}", timeout=30)
            data = self._check(r)
            status = str(data.get("status", "")).upper()
            if status in ("COMPLETED", "SUCCEEDED"):
                outs = [o for o in data.get("outputs", []) if o.get("status", "COMPLETED") == "COMPLETED"]
                if not outs or not outs[0].get("url"):
                    raise HedraError(f"job {job_id} completed with no output url: {data}")
                return outs[0]
            if status in ("FAILED", "CANCELLED", "ERROR"):
                raise HedraError(f"job {job_id} failed: {data.get('error')}")
            time.sleep(int(data.get("poll_after_seconds") or 10))
        raise HedraError(f"timed out waiting for {job_id}")

    # -- pipeline ----------------------------------------------------------
    def tts(self, text: str, voice_id: str | None, job_key: str) -> str:
        inp: dict = {"prompt": text}
        if voice_id:
            inp["voice_id"] = voice_id
        out = self.wait(self.submit(TTS_MODEL, inp, idempotency_key=f"{job_key}-tts"))
        return out["asset_id"]

    def avatar(self, channel: Channel, portrait: Path, audio_asset_id: str, job_key: str) -> str:
        inp = {
            "prompt": f"{channel.persona}, speaking to camera, steady framing, natural gestures",
            "aspect_ratio": "9:16",
            "resolution": os.environ.get("HEDRA_RESOLUTION", "540p"),
            "start_image": {"source": "url", "url": self.upload(portrait)},
            "audio": {"source": "asset", "asset_id": audio_asset_id},
        }
        out = self.wait(self.submit(AVATAR_MODEL, inp, idempotency_key=f"{job_key}-avatar"))
        return out["url"]

    def render(self, channel: Channel, portrait: Path, script_text: str, job_key: str) -> str:
        """TTS then avatar. Returns the download URL of the finished 9:16 video."""
        if not portrait.exists():
            raise HedraError(f"portrait image missing: {portrait}")
        audio = self.tts(script_text, os.environ.get("HEDRA_VOICE_ID"), job_key)
        return self.avatar(channel, portrait, audio, job_key)

    def download(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        return dest

    @staticmethod
    def _check(r: requests.Response) -> dict:
        try:
            data = r.json()
        except ValueError:
            raise HedraError(f"HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code >= 400:
            raise HedraError(f"HTTP {r.status_code}: {data.get('error') or data}")
        return data
