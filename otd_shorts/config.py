from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _env_path(name: str, default: str) -> Path:
    raw = os.environ.get(name, default)
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


@dataclass
class Channel:
    key: str
    name: str
    pillar: str
    persona: str
    avatar_id: str
    voice_id: str
    token_file: str
    youtube_channel_id: str = ""
    avatar_type: str = "talking_photo"  # or "avatar" for a HeyGen digital twin
    slots: list[str] = field(default_factory=list)
    oauth_client: str = "client_secret.json"
    engine: str = "avatar_iv"
    resolution: str = "720p"
    captions: bool = True


@dataclass
class Settings:
    brand: dict[str, Any]
    channels: list[Channel]
    topics: dict[str, list[dict[str, str]]]
    data_dir: Path
    secrets_dir: Path

    @property
    def timezone(self) -> str:
        return self.brand.get("timezone", "UTC")

    def channel(self, key: str) -> Channel:
        for c in self.channels:
            if c.key == key:
                return c
        raise KeyError(f"unknown channel {key!r}")


def load_settings() -> Settings:
    brand = yaml.safe_load((CONFIG_DIR / "brand.yaml").read_text())
    raw = yaml.safe_load((CONFIG_DIR / "channels.yaml").read_text())
    topics = yaml.safe_load((CONFIG_DIR / "topics.yaml").read_text())
    defaults = raw.get("defaults", {})
    channels = []
    for item in raw["channels"]:
        merged = {**defaults, **item}
        channels.append(Channel(**merged))
    keys = [c.key for c in channels]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate channel keys in channels.yaml")
    for c in channels:
        if c.pillar not in topics:
            raise ValueError(f"channel {c.key} uses pillar {c.pillar!r} which has no topics")
    data_dir = _env_path("OTD_DATA_DIR", "data")
    secrets_dir = _env_path("OTD_SECRETS_DIR", "secrets")
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(brand=brand, channels=channels, topics=topics, data_dir=data_dir, secrets_dir=secrets_dir)
