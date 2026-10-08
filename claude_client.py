"""Claude, through the Anthropic Messages API, for the steps where it beats the local model by
enough to be worth a fraction of a cent (which step uses which model: models.py).

The key is read from ANTHROPIC_API_KEY, else from ~/.ytgist/anthropic_key (one line, mode 600).
No key, no Claude: available() says so and the local model is used. Plain urllib, no SDK.

Session is the same shape as model_client.Server (chat, look, count_tokens, ctx, a context
manager), so a step can be handed either one without knowing which it got.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request

import model_client

API = "https://api.anthropic.com/v1/messages"
KEY_FILE = os.path.expanduser(os.environ.get("YTGIST_ANTHROPIC_KEY_FILE", "~/.ytgist/anthropic_key"))
# USD per million tokens (input, output), from the pricing page on 8 Oct 2026. Only used to
# show what a step cost; if a model is missing here the cost is just not shown.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}
NAMES = {"claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
         "claude-haiku-5-5": "Claude Haiku 5.5"}
# How much a request may hold. Far beyond any transcript ytgist accepts; the length gate in
# ytgist.py uses it instead of the local model's computed ceiling when Claude summarises.
CONTEXT = 200_000


class ClaudeError(model_client.ModelError):
    """A ModelError, so every place that already survives a failed local model call
    survives a failed Claude call the same way."""


def _key():
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not k and os.path.exists(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8") as f:
            k = f.read().strip()
    return k


def available():
    return bool(_key())


def name(model):
    return NAMES.get(model, model)


def cost(model, usage):
    p = PRICES.get(model)
    if not p or not usage:
        return None
    return round((usage.get("input_tokens", 0) * p[0] + usage.get("output_tokens", 0) * p[1]) / 1e6, 4)


def add_usage(total, model, usage):
    """Add one call's tokens to a {model: {input_tokens, output_tokens}} tally."""
    if total is None or not usage:
        return
    t = total.setdefault(model, {"input_tokens": 0, "output_tokens": 0})
    for k in ("input_tokens", "output_tokens"):
        t[k] += int(usage.get(k) or 0)


def total_cost(by_model):
    """What a {model: usage} tally cost in USD, or None when nothing was priced."""
    costs = [cost(m, u) for m, u in (by_model or {}).items()]
    costs = [c for c in costs if c is not None]
    return round(sum(costs), 4) if costs else None


def chat(model, system, user, max_tokens=1400, temperature=0.3, retries=3, images=()):
    """One message in, the text out, plus the token usage: (text, usage).

    `images` are JPEG bytes, put before the text the way the local vision server gets them."""
    key = _key()
    if not key:
        raise ClaudeError("no Anthropic API key (ANTHROPIC_API_KEY or ~/.ytgist/anthropic_key)")
    # `temperature` is not sent: the 5.x models reject it ("deprecated for this model",
    # 8 Oct 2026). The argument stays so callers can share one signature with the local model.
    # Adaptive thinking spends from max_tokens too: Haiku once used a whole 1,400-token budget
    # thinking and returned no text at all (8 Oct). Callers size max_tokens for the ANSWER, so
    # the thinking gets its own room on top.
    budget = min(64000, max(max_tokens * 3, max_tokens + 12000))
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(img).decode()}}
               for img in images] + [{"type": "text", "text": user}]
    msg = {"model": model, "max_tokens": budget,
           "messages": [{"role": "user", "content": content if images else user}]}
    if system:
        msg["system"] = system
    body = json.dumps(msg).encode()
    for attempt in range(retries):
        req = urllib.request.Request(API, data=body, method="POST", headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read())
            text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
            if not text.strip():
                # Said loudly: an empty answer read as "no picks" or "nothing to say" is a
                # wrong result that looks like a right one.
                raise ClaudeError(f"empty answer from {model}: stop_reason={d.get('stop_reason')} "
                                  f"blocks={[b.get('type') for b in d.get('content', [])]} "
                                  f"usage={d.get('usage')}")
            return text, d.get("usage") or {}
        except urllib.error.HTTPError as e:
            msg_ = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503, 529) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))     # overloaded or rate-limited: wait and retry
                continue
            raise ClaudeError(f"Claude API {e.code}: {msg_}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            raise ClaudeError(f"Claude API unreachable: {getattr(e, 'reason', e)}") from None


class Session:
    """Claude in the shape of a model_client.Server, for code written against a local server.

    Nothing to start or stop: `stop()` only marks the session stopped, so a cancelled job's
    answer is thrown away when it arrives (an HTTP call in flight can't be recalled)."""

    vision = True
    borrowed = True
    was_warm = False

    def __init__(self, model, usage=None):
        self.model = model
        self.ctx = CONTEXT
        self.usage = usage if usage is not None else {}
        self.stopped = False

    def _call(self, system, user, max_tokens, images=()):
        if self.stopped:
            raise ClaudeError("stopped")
        text, u = chat(self.model, system, user, max_tokens=max_tokens, images=images)
        add_usage(self.usage, self.model, u)
        if self.stopped:
            raise ClaudeError("stopped")
        return text.strip()

    def chat(self, system, user, max_tokens=1400, temperature=0.3):
        return self._call(system, user, max_tokens)

    def look(self, jpeg, question, max_tokens=120, temperature=0.1, timeout=180):
        return self._call("", question, max_tokens, images=(jpeg,))

    def count_tokens(self, text):
        # A deliberately high estimate (2 characters per token, the Cyrillic rate): it only
        # decides whether a transcript fits, and CONTEXT leaves room to spare.
        return max(1, len(text) // 2)

    def cost(self):
        return total_cost(self.usage)

    def stop(self):
        self.stopped = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
