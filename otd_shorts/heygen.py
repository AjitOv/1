"""HeyGen v2 avatar video rendering (9:16, captions burned in).

Uses the REST API directly with HEYGEN_API_KEY. Endpoints:
  POST https://api.heygen.com/v2/video/generate
  GET  https://api.heygen.com/v1/video_status.get?video_id=...
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from .config import Channel

API = "https://api.heygen.com"
DIMENSIONS = {"720p": (720, 1280), "1080p": (1080, 1920)}
BACKGROUND = "#0b1220"


class HeyGenError(RuntimeError):
    pass


class HeyGen:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.key = api_key or os.environ.get("HEYGEN_API_KEY")
        if not self.key:
            raise HeyGenError("HEYGEN_API_KEY is not set")
        self.s = session or requests.Session()
        self.s.headers.update({"X-Api-Key": self.key, "Content-Type": "application/json"})

    def submit(self, channel: Channel, script_text: str, title: str) -> str:
        w, h = DIMENSIONS[channel.resolution]
        if channel.avatar_type == "avatar":
            character = {"type": "avatar", "avatar_id": channel.avatar_id, "avatar_style": "normal"}
        else:
            character = {"type": "talking_photo", "talking_photo_id": channel.avatar_id,
                         "talking_photo_style": "square", "talking_style": "expressive"}
        body = {
            "title": title,
            "caption": bool(channel.captions),
            "dimension": {"width": w, "height": h},
            "video_inputs": [{
                "character": character,
                "voice": {"type": "text", "input_text": script_text, "voice_id": channel.voice_id, "speed": 1.05},
                "background": {"type": "color", "value": BACKGROUND},
            }],
        }
        r = self.s.post(f"{API}/v2/video/generate", json=body, timeout=60)
        data = self._check(r)
        vid = (data.get("data") or {}).get("video_id")
        if not vid:
            raise HeyGenError(f"no video_id in response: {data}")
        return vid

    def status(self, video_id: str) -> dict:
        r = self.s.get(f"{API}/v1/video_status.get", params={"video_id": video_id}, timeout=30)
        return self._check(r).get("data") or {}

    def wait(self, video_id: str, timeout_s: int = 1800, poll_s: int = 20) -> str:
        """Block until the video completes. Returns the download URL."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            d = self.status(video_id)
            st = d.get("status")
            if st == "completed":
                return d["video_url"]
            if st == "failed":
                raise HeyGenError(f"render failed: {d.get('error')}")
            time.sleep(poll_s)
        raise HeyGenError(f"timed out waiting for {video_id}")

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
            raise HeyGenError(f"HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code >= 400 or data.get("error"):
            raise HeyGenError(f"HTTP {r.status_code}: {data.get('error') or data}")
        return data
