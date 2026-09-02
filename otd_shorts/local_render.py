"""Free, key-less renderer: Edge TTS voice + karaoke captions + animated background, via ffmpeg.

Per Short this costs nothing. Pipeline:
  1. edge-tts  -> mp3 + word boundaries (Microsoft Edge neural voices, no account needed)
  2. .ass file -> word-highlight captions, topic title at the top, site at the bottom
  3. ffmpeg    -> 1080x1920 30fps H.264: animated gradient (or darkened Pexels b-roll if
                  PEXELS_API_KEY is set) + captions + voice (+ optional low music bed)

Set OTD_RENDERER=local. Optional: PEXELS_API_KEY, OTD_MUSIC_DIR (mp3s mixed at low volume),
OTD_CAPTION_FONT (default "DejaVu Sans"), SSL_CERT_FILE (extra CA for proxied networks).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Channel, Settings

W, H, FPS = 1080, 1920, 30
PAD_START, PAD_END = 0.4, 0.8
MAX_CHUNK_CHARS = 16   # ~3 words per caption line
MAX_CHUNK_WORDS = 3


class LocalRenderError(RuntimeError):
    pass


@dataclass
class Word:
    start: float
    end: float
    text: str


# ----------------------------------------------------------------------------- helpers
def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise LocalRenderError(f"ffmpeg not found (pip install imageio-ffmpeg): {exc}")


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> ASS '&HAABBGGRR'."""
    r, g, b = hex_rgb.lstrip("#")[0:2], hex_rgb.lstrip("#")[2:4], hex_rgb.lstrip("#")[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


# ----------------------------------------------------------------------------- TTS
async def _edge_tts(text: str, voice: str, out_mp3: Path, rate: str) -> list[Word]:
    import edge_tts
    extra = os.environ.get("SSL_CERT_FILE")
    if extra and Path(extra).exists():
        try:  # corporate/proxy CA: extend edge-tts' own certificate context
            from edge_tts import communicate as _c
            _c._SSL_CTX.load_verify_locations(extra)
        except Exception:  # noqa: BLE001
            pass
    words: list[Word] = []
    comm = edge_tts.Communicate(text, voice, rate=rate)
    with open(out_mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                s = chunk["offset"] / 1e7
                words.append(Word(s, s + chunk["duration"] / 1e7, chunk["text"]))
    if not words:
        raise LocalRenderError("edge-tts returned no word boundaries")
    return words


def _estimate_words(sentences: list[tuple[str, float, float]]) -> list[Word]:
    """Spread each sentence's [start, end) across its words proportionally to character length."""
    words: list[Word] = []
    for text, start, end in sentences:
        toks = text.split()
        total = sum(len(t) for t in toks) or 1
        t = start
        for tok in toks:
            d = (end - start) * len(tok) / total
            words.append(Word(t, t + d, tok))
            t += d
    return words


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]


def _wav_seconds(path: Path) -> float:
    import wave
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _piper_tts(text: str, model_path: str, out_wav: Path) -> list[Word]:
    """Fully offline TTS (pip install piper-tts + a .onnx voice from rhasspy/piper-voices).
    Piper gives no word timings, so each sentence is synthesised separately for exact
    sentence boundaries and words are spread proportionally inside them."""
    import wave
    from piper import PiperVoice
    voice = PiperVoice.load(model_path)
    parts, timeline, t = [], [], 0.0
    for i, sent in enumerate(_sentences(text)):
        p = out_wav.with_name(f"{out_wav.stem}_{i}.wav")
        with wave.open(str(p), "wb") as wf:
            voice.synthesize_wav(sent, wf)
        d = _wav_seconds(p)
        timeline.append((sent, t, t + d))
        t += d + 0.18
        parts.append(p)
    lst = out_wav.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    subprocess.run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-af", "apad=pad_dur=0.18", str(out_wav)], check=True)
    return _estimate_words(timeline)


def _fake_tts(text: str, out_wav: Path) -> list[Word]:
    """Test-only stand-in (OTD_TTS=fake): a quiet tone with estimated timings, no network."""
    timeline, t = [], 0.0
    for sent in _sentences(text):
        d = 0.36 * len(sent.split()) + 0.25
        timeline.append((sent, t, t + d))
        t += d + 0.15
    subprocess.run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency=220:duration={t:.2f}", "-af", "volume=0.05", str(out_wav)], check=True)
    return _estimate_words(timeline)


def tts(text: str, voice: str, out_dir: Path, rate: str = "+8%") -> tuple[Path, list[Word]]:
    """Synthesise `text`. Returns (audio_path, word timings). Backend from OTD_TTS: edge|piper|fake."""
    backend = os.environ.get("OTD_TTS", "edge").lower()
    try:
        if backend == "fake":
            out = out_dir / "voice.wav"
            return out, _fake_tts(text, out)
        if backend == "piper":
            model = os.environ.get("OTD_PIPER_MODEL")
            if not model or not Path(model).exists():
                raise LocalRenderError("OTD_TTS=piper needs OTD_PIPER_MODEL pointing at a .onnx voice")
            out = out_dir / "voice.wav"
            return out, _piper_tts(text, model, out)
        out = out_dir / "voice.mp3"
        return out, asyncio.run(_edge_tts(text, voice, out, rate))
    except LocalRenderError:
        raise
    except Exception as exc:  # noqa: BLE001 - network / handshake / decoder errors
        raise LocalRenderError(f"tts backend {backend} failed: {exc}")


# ----------------------------------------------------------------------------- captions
def chunk_words(words: list[Word], max_chars: int = MAX_CHUNK_CHARS, max_words: int = MAX_CHUNK_WORDS) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        proposed = " ".join(x.text for x in cur + [w])
        if cur and (len(proposed) > max_chars or len(cur) >= max_words):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w.text.rstrip()[-1:] in ".?!":  # break on sentence end
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[Word], title: str, site: str, theme: dict, duration: float, font: str) -> str:
    accent, text_col = theme["accent"], theme.get("text", "#FFFFFF")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},104,{_ass_color(accent)},{_ass_color(text_col)},{_ass_color('#000000')},{_ass_color('#000000', 128)},-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1
Style: Title,{font},54,{_ass_color(text_col)},{_ass_color(text_col)},{_ass_color('#000000')},{_ass_color('#000000', 128)},-1,0,0,0,100,100,0,0,1,4,2,8,80,80,250,1
Style: Brand,{font},46,{_ass_color(accent)},{_ass_color(accent)},{_ass_color('#000000')},{_ass_color('#000000', 128)},-1,0,0,0,100,100,2,0,1,3,1,2,80,80,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    lines.append(f"Dialogue: 0,{_ass_time(0)},{_ass_time(duration)},Title,,0,0,0,,{_esc(title.upper())}")
    lines.append(f"Dialogue: 0,{_ass_time(0)},{_ass_time(duration)},Brand,,0,0,0,,{_esc(site)}")
    chunks = chunk_words(words)
    for i, ch in enumerate(chunks):
        start = ch[0].start + PAD_START
        # hold the caption until the next chunk starts so there is no flicker between lines
        end = (chunks[i + 1][0].start + PAD_START) if i + 1 < len(chunks) else min(duration, ch[-1].end + PAD_START + 0.6)
        parts = []
        for w in ch:
            k = max(1, int(round((w.end - w.start) * 100)))
            parts.append(f"{{\\k{k}}}{_esc(w.text)}")
        # lead-in: the first word's highlight starts when the chunk appears
        lead = int(round((ch[0].start - (start - PAD_START)) * 100))
        text = (f"{{\\k{lead}}}" if lead > 0 else "") + " ".join(parts)
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{{\\fad(80,80)}}{text}")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- b-roll
def fetch_broll(query: str, cache_dir: Path, seed: int) -> Path | None:
    """Download one portrait stock clip from Pexels (free API key) into cache_dir. None if unavailable."""
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    import requests
    try:
        r = requests.get("https://api.pexels.com/videos/search",
                         params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 15},
                         headers={"Authorization": key}, timeout=30)
        r.raise_for_status()
        vids = r.json().get("videos", [])
        if not vids:
            return None
        v = random.Random(seed).choice(vids)
        files = sorted((f for f in v["video_files"] if f.get("width") and f.get("height") and f["height"] > f["width"]),
                       key=lambda f: abs(f["width"] - W))
        if not files:
            return None
        dest = cache_dir / f"pexels_{v['id']}.mp4"
        if not dest.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            with requests.get(files[0]["link"], stream=True, timeout=120) as d:
                d.raise_for_status()
                with open(dest, "wb") as f:
                    for c in d.iter_content(1 << 20):
                        f.write(c)
        return dest
    except Exception:  # noqa: BLE001 - b-roll is optional; fall back to the gradient
        return None


# ----------------------------------------------------------------------------- render
class LocalRenderer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ff = ffmpeg_exe()
        import sys
        default_font = "Helvetica Neue" if sys.platform == "darwin" else ("Arial" if sys.platform == "win32" else "DejaVu Sans")
        self.font = os.environ.get("OTD_CAPTION_FONT", default_font)
        self.work = settings.data_dir / "work"
        self.work.mkdir(parents=True, exist_ok=True)

    def render(self, channel: Channel, script: dict, job_id: str, dest: Path) -> Path:
        from .scripts import full_text
        seed = int(hashlib.sha256(job_id.encode()).hexdigest()[:8], 16)
        wd = self.work / job_id
        wd.mkdir(parents=True, exist_ok=True)
        text = re.sub(r"\s+", " ", full_text(script)).strip()
        mp3, words = tts(text, channel.tts_voice, wd)
        speech_end = words[-1].end
        duration = round(PAD_START + speech_end + PAD_END, 2)
        theme = channel.theme
        ass = wd / "captions.ass"
        ass.write_text(build_ass(words, script.get("topic") or script["title"], self.settings.brand["site"], theme, duration, self.font))

        broll = fetch_broll(channel.broll_query, self.settings.data_dir / "broll", seed) if channel.broll_query else None
        cmd = [self.ff, "-y", "-hide_banner", "-loglevel", "error"]
        if broll:
            cmd += ["-stream_loop", "-1", "-i", str(broll)]
            bg = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},"
                  f"eq=brightness=-0.22:saturation=0.75,vignette=PI/4,trim=duration={duration},setpts=PTS-STARTPTS[bg]")
        else:
            c0, c1, c2 = theme["colors"]
            cmd += ["-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0={c0}:c1={c1}:c2={c2}:speed=0.012:type=linear:d={duration}:r={FPS}"]
            bg = f"[0:v]vignette=PI/5[bg]"
        cmd += ["-i", str(mp3)]
        afilter = f"[1:a]adelay={int(PAD_START*1000)}|{int(PAD_START*1000)},apad=pad_dur={PAD_END},volume=1.0[voice]"
        music = self._pick_music(seed)
        if music:
            cmd += ["-stream_loop", "-1", "-i", str(music)]
            afilter += f";[2:a]volume=0.10,atrim=duration={duration}[mus];[voice][mus]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        else:
            afilter += ";[voice]anull[aout]"
        ass_path = str(ass).replace("\\", "/").replace(":", "\\:")
        vf = f"{bg};[bg]subtitles='{ass_path}'[vout]"
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["-filter_complex", f"{vf};{afilter}", "-map", "[vout]", "-map", "[aout]",
                "-t", str(duration), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not dest.exists():
            raise LocalRenderError(f"ffmpeg failed: {proc.stderr[-800:]}")
        shutil.rmtree(wd, ignore_errors=True)
        return dest

    def _pick_music(self, seed: int) -> Path | None:
        d = os.environ.get("OTD_MUSIC_DIR")
        if not d:
            return None
        files = sorted(p for p in Path(d).glob("*") if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg"))
        return random.Random(seed).choice(files) if files else None
