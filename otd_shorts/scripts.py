"""Script engine for 30-45 second Shorts.

Two generators share one contract (a dict with hook/body/cta/title/description):
  * template_script  - deterministic, offline, uses config/topics.yaml. Always available.
  * claude_script    - calls Claude when ANTHROPIC_API_KEY is set, for fresher wording.
Both outputs go through `lint()` which rejects banned phrases (guarantees, risk-free, ...).
"""
from __future__ import annotations

import json
import os
import random
from typing import Any

from .config import Channel, Settings

FORMATS = ("lesson", "mistake", "myth", "checklist", "story", "rule")

TARGET_WORDS = (70, 120)  # ~30-45 s at 150-160 wpm

_HOOKS = {
    "lesson": [
        "{title}. Here is why, in thirty seconds.",
        "Most traders get this wrong. {title}.",
        "{title}. Let me explain.",
    ],
    "mistake": [
        "Stop doing this. {mistake_sentence}",
        "This one mistake blows up more accounts than any crash. {mistake_sentence}",
        "I lost money for years because of this. {mistake_sentence}",
    ],
    "myth": [
        "Everyone believes this, and it is costing them money. {title}.",
        "Unpopular opinion. {title}.",
        "The internet lies to you about this. {title}.",
    ],
    "checklist": [
        "Before your next trade, run this checklist. {title}.",
        "My thirty-second checklist. {title}.",
        "Three checks, every single trade. {title}.",
    ],
    "story": [
        "Quick story. {example}",
        "Picture this. {example}",
        "I watched this happen last week. {example}",
    ],
    "rule": [
        "Rule number {n}. {title}.",
        "Write this rule down. {title}.",
        "One rule that fixed my trading. {title}.",
    ],
}

_BODIES = {
    "lesson": "{lesson} For example, {example_lc} Here is the mistake most people make. {mistake_sentence} Fix that one thing and your results change.",
    "mistake": "The problem is simple. {mistake_sentence} Here is what works instead. {lesson} {example} That is the whole difference between losing and lasting.",
    "myth": "Here is the myth in action. {mistake_sentence} The reality is the opposite. {lesson} Think about it like this. {example} Once you see it, you cannot unsee it.",
    "checklist": "One. {lesson_first} Two. Make sure you are not doing this. {mistake_sentence} If you are, stop. Three. Picture the example. {example} Three checks, every single time.",
    "story": "The mistake behind it is simple. {mistake_sentence} The lesson is just as simple. {lesson} It is not exciting, and that is exactly why it works.",
    "rule": "{lesson} Here is why it matters. {example} Break the rule and this is what it looks like. {mistake_sentence} Keep the rule and you keep your account.",
}

_TITLES = {
    "lesson": ["{title}", "{title} (30 second lesson)", "{title}, explained"],
    "mistake": ["Stop {mistake_gerund}", "This mistake kills trading accounts", "{title}: the mistake"],
    "myth": ["Myth vs reality: {title}", "What nobody tells you: {title}", "{title}?"],
    "checklist": ["{title}: 3-step check", "3 checks before every trade", "{title} in 3 steps"],
    "story": ["{title}", "A lesson from one bad trade", "{title}: a true story"],
    "rule": ["Trading rule #{n}: {title}", "{title}", "One rule that changes everything"],
}


def _lc(s: str) -> str:
    return s[0].lower() + s[1:] if s else s


def _short(title: str) -> str:
    t = title.lower()
    for prefix in ("why ", "how ", "the ", "what ", "my "):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t.rstrip("?.")


def _first_sentence(s: str) -> str:
    return s.split(". ")[0].rstrip(".") + "."


def _gerund(mistake: str) -> str:
    return _lc(mistake.rstrip("."))


def word_count(text: str) -> int:
    return len(text.split())


def lint(script: dict[str, Any], banned: list[str]) -> list[str]:
    """Return a list of problems. Empty list means the script is publishable."""
    problems = []
    blob = " ".join(str(script.get(k, "")) for k in ("hook", "body", "cta", "title", "description_intro")).lower()
    for phrase in banned:
        if phrase.lower() in blob:
            problems.append(f"banned phrase: {phrase!r}")
    n = word_count(full_text(script))
    if n < TARGET_WORDS[0] - 15:
        problems.append(f"too short ({n} words)")
    if n > TARGET_WORDS[1] + 40:
        problems.append(f"too long ({n} words)")
    if len(script.get("title", "")) > 100:
        problems.append("title over 100 chars")
    return problems


def full_text(script: dict[str, Any]) -> str:
    return " ".join(part.strip() for part in (script["hook"], script["body"], script["cta"]) if part)


def build_description(script: dict[str, Any], channel: Channel, settings: Settings) -> str:
    b = settings.brand
    tags = " ".join(b.get("hashtags", []))
    lines = [
        script["description_intro"].strip(),
        "",
        f"Full course: {b['url']}",
        f"{b['course_name']}",
        "",
        b["disclaimer"].strip(),
        "",
        tags,
    ]
    return "\n".join(lines)


def template_script(topic: dict[str, str], fmt: str, channel: Channel, settings: Settings, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    ctx = {
        "title": topic["title"],
        "short": _short(topic["title"]),
        "lesson": topic["lesson"].strip(),
        "lesson_first": _first_sentence(topic["lesson"]),
        "example": topic["example"].strip(),
        "example_lc": _lc(topic["example"].strip()),
        "mistake_sentence": topic["mistake"].strip().rstrip(".") + ".",
        "mistake_lc": _lc(topic["mistake"].strip().rstrip(".")) + ".",
        "mistake_gerund": _gerund(topic["mistake"]),
        "n": rng.randint(1, 12),
    }
    hook = rng.choice(_HOOKS[fmt]).format(**ctx)
    body = _BODIES[fmt].format(**ctx)
    cta = rng.choice(settings.brand["ctas"])
    title = rng.choice(_TITLES[fmt]).format(**ctx)
    title = title[0].upper() + title[1:]
    script = {
        "hook": hook,
        "body": body,
        "cta": cta,
        "title": title[:100],
        "description_intro": f"{topic['title']}. {topic['lesson']}",
        "generator": "template",
        "format": fmt,
        "topic": topic["title"],
    }
    script["description"] = build_description(script, channel, settings)
    return script


_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "body": {"type": "string"},
        "cta": {"type": "string"},
        "title": {"type": "string"},
        "description_intro": {"type": "string"},
    },
    "required": ["hook", "body", "cta", "title", "description_intro"],
    "additionalProperties": False,
}


def claude_script(topic: dict[str, str], fmt: str, channel: Channel, settings: Settings, seed: int) -> dict[str, Any]:
    """Ask Claude for a script. Falls back to the template on any API problem or refusal."""
    import anthropic

    b = settings.brand
    client = anthropic.Anthropic()
    system = (
        "You write scripts for 30-45 second vertical YouTube Shorts about trading and investing. "
        f"The presenter is {b['coach_name']}, speaking to camera as {channel.persona}. "
        "Rules: 75-115 words total across hook+body+cta. Hook is one punchy sentence. Body teaches ONE idea with "
        "one concrete example, plain words, no jargon without explanation. Spoken style, short sentences, no lists, "
        "no emojis, no stage directions. Never promise returns, never use the words guaranteed, risk-free or "
        "get rich, never give a buy/sell call on a specific security. The CTA is one sentence that points to "
        f"{b['site']} where the full {b['course_name']} lives. Title under 70 characters, no clickbait caps. "
        "description_intro is two sentences summarising the lesson for the video description."
    )
    user = (
        f"Format: {fmt}\nTopic: {topic['title']}\nCore lesson: {topic['lesson']}\n"
        f"Example: {topic['example']}\nCommon mistake: {topic['mistake']}\n"
        f"Variation seed: {seed} (make the wording distinct from other videos on this topic)."
    )
    try:
        resp = client.beta.messages.create(
            model="claude-opus-5",
            max_tokens=2000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}, "effort": "low"},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("model refused")
        text = next(blk.text for blk in resp.content if blk.type == "text")
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - any API failure falls back to the offline generator
        out = template_script(topic, fmt, channel, settings, seed)
        out["generator_note"] = f"claude fallback: {exc}"
        return out
    script = {**data, "generator": "claude", "format": fmt, "topic": topic["title"]}
    script["title"] = script["title"][:100]
    script["description"] = build_description(script, channel, settings)
    return script


def generate(topic: dict[str, str], fmt: str, channel: Channel, settings: Settings, seed: int) -> dict[str, Any]:
    if os.environ.get("ANTHROPIC_API_KEY"):
        script = claude_script(topic, fmt, channel, settings, seed)
        if not lint(script, settings.brand["banned_phrases"]):
            return script
    script = template_script(topic, fmt, channel, settings, seed)
    problems = lint(script, settings.brand["banned_phrases"])
    if problems:
        raise ValueError(f"template script for {topic['title']!r}/{fmt} failed lint: {problems}")
    return script
