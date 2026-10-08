"""Which model does which step. The one place that decides it.

Tested side by side on 8 Oct 2026 (three research topics, six videos): Claude Haiku 5.5 picked
videos as well as Sonnet, wrote clearly better per-video summaries than the local Qwen3.6-27B,
and its briefs held up under Sonnet's own citation check, at well under a cent per step. The
brief is the one step Denis chose Sonnet for: it is read most closely and runs once per topic.

    step      what it does                                         default
    queries   turns a research topic into YouTube searches         Haiku
    pick      scores every search result for fit                   Haiku
    gist      one video's numbered summary                         Haiku
    expand    "more detail" on one takeaway                        Haiku
    frames    chooses a screenshot from YouTube's thumbnail sheets  Haiku
    brief     the combined research brief                          Sonnet
    check     does each cited moment back its takeaway?            Haiku

Transcripts always stay local (Parakeet). Without an API key every step is local, exactly as
before, and the local model is also the fallback when a Claude call fails.

Override any step in ~/.ytgist/models.json, e.g. {"brief": "claude-opus-5-5", "gist": "local"},
or set YTGIST_LOCAL_ONLY=1 to keep everything on this machine.
"""
import json
import os

import claude_client

HAIKU, SONNET, LOCAL = "claude-haiku-5-5", "claude-sonnet-5-5", "local"
DEFAULTS = {"queries": HAIKU, "pick": HAIKU, "gist": HAIKU, "expand": HAIKU,
            "frames": HAIKU, "brief": SONNET, "check": HAIKU}
CONFIG = os.path.expanduser(os.environ.get("YTGIST_MODELS_FILE", "~/.ytgist/models.json"))


def _overrides():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            d = json.load(f)
        return {k: str(v) for k, v in d.items() if k in DEFAULTS} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def chosen(step):
    """What the settings ask for, whether or not it can run here."""
    if os.environ.get("YTGIST_LOCAL_ONLY", "").strip() not in ("", "0"):
        return LOCAL
    return _overrides().get(step, DEFAULTS[step])


def for_step(step):
    """The model this step will actually use: a Claude model id, or "local"."""
    m = chosen(step)
    return m if m.startswith("claude") and claude_client.available() else LOCAL


def is_claude(step):
    return for_step(step) != LOCAL


def label(model):
    """For progress messages and the page."""
    return claude_client.name(model) if model.startswith("claude") else "the local model"


def table():
    """{step: model} as it will run right now, for /api/models and the logs."""
    return {s: for_step(s) for s in DEFAULTS}
