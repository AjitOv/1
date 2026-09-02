"""Turn the channel/topic config into concrete jobs for a date.

For each channel and each posting slot we choose a (topic, format) pair that the channel has
not used yet (or the least recently used one once everything has been used), so the 4 daily
videos on one channel are different from each other and from other channels."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Settings
from .scripts import FORMATS, generate
from .state import Job, Store


def job_id(day: str, channel: str, slot: str) -> str:
    return f"{day}_{channel}_{slot.replace(':', '')}"


def publish_at(day: date, slot: str, tz: str) -> str:
    hh, mm = (int(x) for x in slot.split(":"))
    local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZoneInfo(tz))
    return local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def choose_pairs(n_topics: int, used: dict[tuple[int, str], str], count: int, seed: int) -> list[tuple[int, str]]:
    """Pick `count` (topic_idx, format) pairs for one channel-day.
    Priority: never-used pair with a topic not yet used today > never-used pair (any topic)
    > least-recently-used pair with a fresh topic > least-recently-used pair."""
    import random
    rng = random.Random(seed)
    all_pairs = [(t, f) for t in range(n_topics) for f in FORMATS]
    rng.shuffle(all_pairs)
    all_pairs.sort(key=lambda p: used.get(p, ""))  # "" (never used) sorts first
    chosen: list[tuple[int, str]] = []
    seen_topics: set[int] = set()
    passes = (
        lambda p: p not in used and p[0] not in seen_topics,
        lambda p: p not in used,
        lambda p: p[0] not in seen_topics,
        lambda p: True,
    )
    for ok in passes:
        for pair in all_pairs:
            if len(chosen) == count:
                return chosen
            if pair in chosen or not ok(pair):
                continue
            chosen.append(pair)
            seen_topics.add(pair[0])
    return chosen


def plan_day(settings: Settings, store: Store, day: date, write_scripts: bool = True) -> list[Job]:
    day_s = day.isoformat()
    created: list[Job] = []
    for ch in settings.channels:
        topics = settings.topics[ch.pillar]
        pairs = choose_pairs(len(topics), store.used_pairs(ch.key), len(ch.slots), _seed(day_s, ch.key))
        for slot, (t_idx, fmt) in zip(ch.slots, pairs):
            jid = job_id(day_s, ch.key, slot)
            if store.get(jid):
                continue
            script = None
            status = "planned"
            if write_scripts:
                script = generate(topics[t_idx], fmt, ch, settings, _seed(day_s, ch.key, slot))
                status = "scripted"
            job = Job(id=jid, date=day_s, channel=ch.key, slot=slot,
                      publish_at=publish_at(day, slot, settings.timezone),
                      topic_idx=t_idx, format=fmt, script=script, heygen_video_id=None,
                      video_path=None, youtube_video_id=None, status=status, error=None, updated_at="")
            store.upsert_planned(job)
            created.append(job)
    return created


def plan_range(settings: Settings, store: Store, start: date, days: int) -> list[Job]:
    out = []
    for i in range(days):
        out.extend(plan_day(settings, store, start + timedelta(days=i)))
    return out
