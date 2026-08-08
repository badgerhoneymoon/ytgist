#!/usr/bin/env python3
"""llama-server lifecycle + chat, with exact process ownership.

THE RULES, each one from a review finding or a measurement:

  • BORROW a server only if it QUALIFIES — right model, and enough context for THIS
    transcript. "Something answers on the port" proves neither (Codex r1). A borrowed
    server is left exactly as we found it.
  • Our own server gets a FREE EPHEMERAL PORT, never a fixed one. The earlier plan said
    "refuse to borrow :18081, then start our own on :18081" — which guarantees a bind
    failure whenever an unqualified server holds that port (Codex r2). Asking the OS for
    a free port removes the collision entirely.
  • Stop by PID, never `pkill`. pkill would kill a server Denis started for something
    else — and ~/serve-model.sh does exactly that, which is why we don't call it.
  • Never leave a server running. Started in its own process group; killed as a group so
    llama-server's children go too.
"""
import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

MODEL = os.path.expanduser("~/models/Qwen3.6-27B-UD-Q5_K_XL.gguf")
BORROW_PORT = 18081          # where we LOOK for an existing server
CTX = 32768                  # our own server's context; 90 min of speech is ~15k tokens
HTTP_TIMEOUT = 20            # every request is bounded — a hung server must not hang us
GEN_TIMEOUT = 900            # generation over a long transcript is legitimately slow


class ModelError(Exception):
    pass


def _get(base, path, timeout=HTTP_TIMEOUT):
    with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(base, path, payload, timeout=HTTP_TIMEOUT):
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    """Either a borrowed server (we leave it alone) or one we own (we stop it)."""

    def __init__(self, base: str, proc=None, ctx: int = CTX):
        self.base, self._proc, self.ctx = base, proc, ctx
        self.borrowed = proc is None

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    def acquire(cls, need_tokens: int = 0, model: str = MODEL, log=print):
        borrowed = cls._try_borrow(need_tokens, model, log)
        if borrowed:
            return borrowed
        return cls._start(model, log)

    @classmethod
    def _try_borrow(cls, need_tokens, model, log):
        base = f"http://127.0.0.1:{BORROW_PORT}"
        try:
            models = _get(base, "/v1/models", timeout=3)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None
        names = " ".join(str(m.get("id", "")) + str(m.get("model", ""))
                         for m in models.get("models", models.get("data", [])))
        if os.path.basename(model).split(".")[0] not in names:
            log(f"  a server is on :{BORROW_PORT} but runs a different model — not borrowing")
            return None
        try:
            props = _get(base, "/props", timeout=3)
            ctx = int(props.get("default_generation_settings", {}).get("n_ctx") or 0)
        except Exception:
            ctx = 0
        if need_tokens and ctx and need_tokens > ctx:
            log(f"  borrowed server's context ({ctx}) is too small for this "
                f"transcript ({need_tokens}) — starting our own")
            return None
        log(f"  borrowing the llama-server already on :{BORROW_PORT} (left untouched)")
        return cls(base, proc=None, ctx=ctx or CTX)

    @classmethod
    def _start(cls, model, log):
        if not os.path.isfile(model):
            raise ModelError(f"model not found: {model}")
        port = _free_port()
        cmd = ["llama-server", "-m", model, "-c", str(CTX), "-fa", "on", "-np", "1",
               "--port", str(port), "--reasoning", "off"]
        log(f"  starting llama-server on :{port} (own process, stopped when done)")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 300          # a 20GB model takes a while to map
        while time.time() < deadline:
            if proc.poll() is not None:
                raise ModelError("llama-server exited during startup "
                                 "(run it by hand to see why)")
            try:
                if _get(base, "/health", timeout=2).get("status") == "ok":
                    return cls(base, proc=proc, ctx=CTX)
            except Exception:
                time.sleep(1)
        cls(base, proc=proc).stop()
        raise ModelError("llama-server did not become healthy within 300s")

    def stop(self):
        """Only ever stops the process WE started, as a group."""
        if self._proc is None:
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                self._proc.wait(timeout=10)
        except (ProcessLookupError, PermissionError):
            pass
        finally:
            self._proc = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    # ------------------------------------------------------------------- calls
    def count_tokens(self, text: str) -> int:
        """The server's OWN tokenizer, not a guess. Character heuristics are wrong by
        enough to matter when the decision is 'does this fit'."""
        try:
            return len(_post(self.base, "/tokenize", {"content": text}).get("tokens", []))
        except Exception:
            return max(1, len(text) // 3)     # only a fallback, and deliberately pessimistic

    def chat(self, system: str, user: str, max_tokens: int = 1400,
             temperature: float = 0.3) -> str:
        r = _post(self.base, "/v1/chat/completions", {
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": temperature, "stream": False,
        }, timeout=GEN_TIMEOUT)
        try:
            return r["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError):
            raise ModelError(f"unexpected response from llama-server: {str(r)[:200]}")
