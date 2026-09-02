import os
import tempfile
from datetime import date
from pathlib import Path

from otd_shorts.config import load_settings
from otd_shorts.planner import choose_pairs, plan_day, plan_range, publish_at
from otd_shorts.scripts import FORMATS, full_text, lint, template_script
from otd_shorts.state import Store


def _settings(tmp):
    os.environ["OTD_DATA_DIR"] = tmp
    os.environ.pop("ANTHROPIC_API_KEY", None)
    return load_settings()


def test_every_topic_and_format_passes_lint():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(tmp)
        for ch in s.channels:
            for topic in s.topics[ch.pillar]:
                for fmt in FORMATS:
                    sc = template_script(topic, fmt, ch, s, seed=1)
                    assert lint(sc, s.brand["banned_phrases"]) == [], (ch.key, topic["title"], fmt)
                    assert 55 <= len(full_text(sc).split()) <= 160
                    assert "otdsystem" in full_text(sc).lower() or "OTD System" in full_text(sc)
                    assert s.brand["url"] in sc["description"]
                    assert "Not financial" in sc["description"]


def test_lint_catches_banned():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(tmp)
        sc = {"hook": "Guaranteed profits", "body": "x " * 90, "cta": "", "title": "t", "description": ""}
        assert any("banned" in p for p in lint(sc, s.brand["banned_phrases"]))


def test_plan_day_makes_44_unique_jobs():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(tmp)
        store = Store(Path(tmp) / "jobs.db")
        jobs = plan_day(s, store, date(2026, 9, 3))
        assert len(jobs) == 44
        # 4 different topics per channel per day
        for ch in s.channels:
            mine = [j for j in jobs if j.channel == ch.key]
            assert len(mine) == 4
            assert len({j.topic_idx for j in mine}) == 4
        # re-planning the same day is a no-op
        assert plan_day(s, store, date(2026, 9, 3)) == []


def test_no_repeats_until_bank_exhausted():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(tmp)
        store = Store(Path(tmp) / "jobs.db")
        jobs = plan_range(s, store, date(2026, 9, 1), 18)  # 18 days x 4 = 72 = 12 topics x 6 formats
        for ch in s.channels:
            pairs = [(j.topic_idx, j.format) for j in jobs if j.channel == ch.key]
            assert len(pairs) == 72 and len(set(pairs)) == 72


def test_choose_pairs_prefers_unused():
    used = {(0, f): "2026-01-01" for f in FORMATS}
    chosen = choose_pairs(3, used, 2, seed=7)
    assert all(p[0] != 0 for p in chosen)


def test_publish_at_converts_timezone():
    assert publish_at(date(2026, 9, 3), "08:00", "Asia/Kolkata") == "2026-09-03T02:30:00Z"
