import os
import tempfile
from pathlib import Path

import pytest

from otd_shorts.local_render import Word, build_ass, chunk_words, _estimate_words, ffmpeg_exe


def _words(text, step=0.3):
    out, t = [], 0.0
    for w in text.split():
        out.append(Word(t, t + step, w))
        t += step
    return out


def test_chunk_words_breaks_on_sentence_and_length():
    chunks = chunk_words(_words("Rule number one. Never move a stop wider, ever."))
    texts = [" ".join(w.text for w in c) for c in chunks]
    assert texts[0] == "Rule number one."
    assert all(len(t) <= 16 or len(t.split()) == 1 for t in texts)
    assert all(len(c) <= 3 for c in chunks)


def test_build_ass_has_styles_and_karaoke():
    ass = build_ass(_words("Stop doing this. It costs money."), "Daily loss limit", "otdsystem.com",
                    {"colors": ["#000", "#111", "#000"], "accent": "#FF6B6B", "text": "#FFFFFF"}, 5.0, "DejaVu Sans")
    assert "Style: Cap," in ass and "Style: Title," in ass and "Style: Brand," in ass
    assert "DAILY LOSS LIMIT" in ass and "otdsystem.com" in ass
    assert "\\k" in ass
    assert ass.count("Dialogue:") == 2 + len(chunk_words(_words("Stop doing this. It costs money.")))


def test_estimate_words_spreads_by_length():
    ws = _estimate_words([("ab cdef", 0.0, 3.0)])
    assert [w.text for w in ws] == ["ab", "cdef"]
    assert abs(ws[0].end - 1.0) < 1e-6 and abs(ws[1].end - 3.0) < 1e-6


@pytest.mark.skipif(os.environ.get("OTD_SKIP_FFMPEG") == "1", reason="ffmpeg e2e disabled")
def test_fake_render_end_to_end():
    from otd_shorts.config import load_settings
    from otd_shorts.local_render import LocalRenderer
    from otd_shorts.scripts import template_script
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["OTD_DATA_DIR"] = tmp
        os.environ["OTD_TTS"] = "fake"
        os.environ.pop("PEXELS_API_KEY", None)
        s = load_settings()
        ch = s.channels[0]
        sc = template_script(s.topics[ch.pillar][0], "rule", ch, s, seed=3)
        out = LocalRenderer(s).render(ch, sc, "test_job", Path(tmp) / "out.mp4")
        assert out.exists() and out.stat().st_size > 100_000
        ffmpeg_exe()
