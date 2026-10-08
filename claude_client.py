"""Claude, through the Anthropic Messages API, for the few steps where a stronger model is
worth a few cents: writing a research brief and checking its citations. Everything else
(transcripts, per-video summaries) stays local.

The key is read from ANTHROPIC_API_KEY, else from ~/.ytgist/anthropic_key (one line, mode 600).
No key, no Claude: available() says so and the local model is used. Plain urllib, no SDK.
"""
import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.anthropic.com/v1/messages"
KEY_FILE = os.path.expanduser(os.environ.get("YTGIST_ANTHROPIC_KEY_FILE", "~/.ytgist/anthropic_key"))
# USD per million tokens (input, output), from the pricing page on 8 Oct 2026. Only used to
# show what a brief cost; if a model is missing here the cost is just not shown.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}


class ClaudeError(RuntimeError):
    pass


def _key():
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not k and os.path.exists(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8") as f:
            k = f.read().strip()
    return k


def available():
    return bool(_key())


def cost(model, usage):
    p = PRICES.get(model)
    if not p or not usage:
        return None
    return round((usage.get("input_tokens", 0) * p[0] + usage.get("output_tokens", 0) * p[1]) / 1e6, 4)


def chat(model, system, user, max_tokens=1400, temperature=0.3, retries=3):
    """One message in, the text out, plus the token usage: (text, usage)."""
    key = _key()
    if not key:
        raise ClaudeError("no Anthropic API key (ANTHROPIC_API_KEY or ~/.ytgist/anthropic_key)")
    # `temperature` is not sent: the 5.x models reject it ("deprecated for this model",
    # 8 Oct 2026). The argument stays so callers can share one signature with the local model.
    body = json.dumps({"model": model, "max_tokens": max_tokens, "system": system,
                       "messages": [{"role": "user", "content": user}]}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(API, data=body, method="POST", headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read())
            text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
            return text, d.get("usage") or {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503, 529) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))     # overloaded or rate-limited: wait and retry
                continue
            raise ClaudeError(f"Claude API {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            raise ClaudeError(f"Claude API unreachable: {e.reason}") from None
