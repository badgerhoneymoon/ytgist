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
import atexit
import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

# WHERE THE MODEL IS. Overridable, because a second machine will not necessarily keep its
# GGUFs in ~/models — and a hardcoded path is the thing that turns "clone and run" into an
# afternoon (Denis is handing this to a friend, 2026-08-10).
MODEL = os.path.expanduser(
    os.environ.get("YTGIST_MODEL", "~/models/Qwen3.6-27B-UD-Q5_K_XL.gguf"))
BORROW_PORT = 18081          # where we LOOK for an existing server
# CONTEXT IS SIZED TO THE TRANSCRIPT, not fixed. A fixed 32k forced anything over ~50
# minutes down the "summarise in halves" path, where each half is summarised blind to the
# other and the merge cannot recover a cross-reference between them (Denis: "we should
# raise the context window"). But a fixed 128k would make a 5-minute video pay for a KV
# cache it never touches, so the size follows the input.
CTX = 32768                  # floor — below this the ladder buys nothing
_CTX_LADDER = (8192, 16384, 32768, 49152, 65536, 98304, 131072)


def _total_ram_gb() -> float:
    try:
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"],
                                  capture_output=True, text=True, timeout=3).stdout) / 1e9
    except Exception:
        return 64.0          # assume the machine this was built on rather than cripple it


_RAM_GB = _total_ram_gb()
_OVERHEAD_GB = 4.0           # macOS, a browser, the dev server — measured, not guessed
# Calibrated against this machine: a 20GB model at a 64k context measured 3.2GB of KV and
# compute buffers, which is 0.08 GB per GB of model per 32k of context.
_KV_GB_PER_GB_PER_32K = 0.08


def ctx_ceiling(model: str = None) -> int:
    """The largest context this machine can hold for THIS model.

    RAM alone is the wrong question. A KV cache is proportional to the model as well as to
    the context, so an 8GB Mac running a 1.1GB model can afford a long context while the
    same machine attempting a 20GB one cannot afford any — and capping purely by RAM would
    punish the small model for the big one's appetite (Denis's friend has 8GB, 2026-08-10).
    """
    try:
        model_gb = os.path.getsize(model or MODEL) / 1e9
    except OSError:
        model_gb = 20.0                      # unknown: assume the big one and be careful
    budget = _RAM_GB - model_gb - _OVERHEAD_GB
    if budget <= 0.2:
        return _CTX_LADDER[0]                # it will swap regardless; keep it survivable
    per_32k = model_gb * _KV_GB_PER_GB_PER_32K
    best = _CTX_LADDER[0]
    for rung in _CTX_LADDER:
        if per_32k * (rung / 32768) <= budget * 0.6:   # leave headroom for compute buffers
            best = rung
    return best


def ctx_for(need_tokens: int) -> int:
    """Smallest context on the ladder that fits the prompt plus its answer."""
    if not need_tokens:
        return CTX
    want = need_tokens + 2048
    ceiling = ctx_ceiling()
    return next((c for c in _CTX_LADDER if want <= c <= ceiling), ceiling)
HTTP_TIMEOUT = 20            # every request is bounded — a hung server must not hang us
# https://huggingface.co/Qwen/Qwen3.6-27B — "Best Practices", instruct/non-thinking row.
QWEN_SAMPLING = {"top_p": 0.80, "top_k": 20, "min_p": 0.0,
                 "presence_penalty": 1.5, "repetition_penalty": 1.0}

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


ALIAS = "ytgist-owned"          # the marker that makes orphan cleanup safe


# ------------------------------------------------------------------- the warm pool
#
# ONE server may outlive its job, for a few minutes, so that consecutive work over the same
# transcript reuses the prefix llama.cpp has already processed. The whole cost of expanding
# a takeaway on a long video is prefill — 44k tokens on an 87-minute interview — and paying
# it again per click is the difference between two minutes and two seconds.
#
# The risk this reintroduces is the one we spent the morning fixing: a 20GB process nobody
# owns. So the pool holds at most ONE server, a daemon reaper stops it after IDLE seconds,
# and it is registered with atexit — an engine restart never leaves it behind.
# HOW LONG A FINISHED SERVER STAYS PARKED. Five minutes is free on a 64GB machine and
# hostile on an 8GB one, where those gigabytes are the difference between the browser being
# responsive and the machine swapping. Small memory, short parking.
IDLE = 300 if _RAM_GB >= 24 else 45


def _ctx_max():
    return ctx_ceiling()

_warm = {"srv": None, "until": 0.0}
_warm_lock = threading.Lock()


def _warm_put(srv):
    """Keep a finished server alive instead of killing it."""
    if srv is None or srv._proc is None:          # borrowed servers are not ours to hold
        return
    with _warm_lock:
        old = _warm["srv"]
        if old is not None and old is not srv:
            old.stop()                            # never more than one
        _warm["srv"] = srv
        _warm["until"] = time.time() + IDLE
    _reaper_start()


def _warm_take(model, ctx, log=print):
    """A warm server, if it fits this job. Context must be big enough; the model must match."""
    with _warm_lock:
        srv = _warm["srv"]
        if srv is None:
            return None
        if srv.model != model or srv.ctx < ctx:
            srv.stop()
            _warm["srv"] = None
            return None
        if srv._proc is not None and srv._proc.poll() is not None:
            _warm["srv"] = None                   # it died while parked
            return None
        _warm["srv"] = None
        srv.was_warm = True
        log(f"  reusing the warm llama-server on {srv.base} "
            f"({srv.ctx // 1024}k context, prompt cache intact)")
        return srv


def warm_available() -> bool:
    """Is a server parked right now? The ETA needs to know — model load is 0s if so."""
    with _warm_lock:
        srv = _warm["srv"]
        return srv is not None and (srv._proc is None or srv._proc.poll() is None)


def warm_stop():
    with _warm_lock:
        srv, _warm["srv"] = _warm["srv"], None
    if srv is not None:
        srv.stop()


def _reap():
    while True:
        time.sleep(5)
        with _warm_lock:
            srv, until = _warm["srv"], _warm["until"]
            if srv is None:
                return
            if time.time() < until:
                continue
            _warm["srv"] = None
        srv.stop()
        return


_reaper = {"t": None}


def _reaper_start():
    with _warm_lock:
        t = _reaper["t"]
        if t is not None and t.is_alive():
            return
        _reaper["t"] = threading.Thread(target=_reap, daemon=True)
        _reaper["t"].start()


atexit.register(warm_stop)


def sweep_orphans(log=print) -> int:
    """Kill llama-servers WE started that outlived their run.

    They orphan whenever the engine dies without unwinding — I killed serve.py under a
    running job and its 20GB child kept going; the next run then started a SECOND one and
    both crawled fighting for the GPU (2026-08-08). Only processes carrying our alias are
    touched, so a server Denis started for something else is never at risk.

    CALLERS MUST HOLD THE RUN LOCK. "Ours" is decided by the alias, and a LIVE run's
    server carries that same alias — so a second concurrent run sweeps the first one's
    model out from under it mid-generation and the first dies with RemoteDisconnected.
    That is exactly what happened when two gists were started at once (2026-08-08); the
    cure is serve.py's _RUN lock, not a cleverer sweep."""
    import subprocess as sp
    killed = 0
    try:
        out = sp.run(["pgrep", "-f", f"llama-server .*--alias {ALIAS}"],
                     capture_output=True, text=True).stdout
        with _warm_lock:
            warm = _warm["srv"]
            keep = warm._proc.pid if (warm is not None and warm._proc is not None) else -1
        for pid in [int(x) for x in out.split() if x.strip().isdigit()]:
            if pid == keep:
                continue                          # ours, parked on purpose
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
    except Exception:
        pass
    if killed:
        log(f"  swept {killed} orphaned llama-server(s) from an earlier run")
    return killed


class Server:
    """Either a borrowed server (we leave it alone) or one we own (we stop it)."""

    def __init__(self, base: str, proc=None, ctx: int = CTX, model: str = MODEL):
        self.base, self._proc, self.ctx = base, proc, ctx
        self.model = model
        self.borrowed = proc is None
        self.was_warm = False        # set when handed out of the warm pool, for the log

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    def acquire(cls, need_tokens: int = 0, model: str = MODEL, log=print):
        warm = _warm_take(model, ctx_for(need_tokens), log)
        if warm:
            return warm
        borrowed = cls._try_borrow(need_tokens, model, log)
        if borrowed:
            return borrowed
        return cls._start(model, log, ctx_for(need_tokens))

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
    def _start(cls, model, log, ctx=CTX):
        if not os.path.isfile(model):
            raise ModelError(f"model not found: {model}")
        port = _free_port()
        # --alias MARKS this server as ours. Without a marker there is no safe way to
        # clean up an orphan: matching on "llama-server" would also kill one Denis
        # started for his own work, which is precisely what ~/serve-model.sh does wrong.
        # q8_0 KV cache roughly halves the memory a large context costs, at a quality
        # difference not measurable on summarisation. It requires flash attention, which
        # is already on.
        # --cache-reuse lets the server keep a prompt prefix it has already processed, so
        # a second call over the same transcript skips straight to the new tail.
        cmd = ["llama-server", "-m", model, "-c", str(ctx), "-fa", "on", "-np", "1",
               "--cache-reuse", "256",
               "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
               "--port", str(port), "--reasoning", "off", "--reasoning-budget", "0",
               "--alias", ALIAS]
        log(f"  starting llama-server on :{port} with a {ctx // 1024}k context "
            f"(own process, stopped when done)")
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
                    srv = cls(base, proc=proc, ctx=ctx)
                    srv.model = model
                    return srv
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
        # Released to the warm pool, not killed. Expanding a takeaway re-reads the same
        # transcript prefix every time, and llama.cpp can reuse a cached prefix — but only
        # if the process that holds it is still alive. Killing it after every job threw
        # that away and made each expansion pay the full prefill again.
        _warm_put(self)
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
            # QWEN'S OWN NON-THINKING RECIPE, sent explicitly. Left unset, llama.cpp
            # substitutes its defaults — top_k 40, top_p 0.9, min_p 0.1 — which disagree
            # with the model card on every count. min_p 0.1 is the quiet one: Qwen asks for
            # 0.0, and a non-zero floor interacts badly with the low top_p 0.8 it pairs
            # with. We were inheriting all three silently (researched 2026-08-08).
            #
            # Temperature stays OURS. The card suggests 0.7 for general use; this pipeline
            # extracts claims from a transcript, where a flatter distribution is the point,
            # so callers keep passing 0.2-0.3 deliberately.
            **QWEN_SAMPLING,
        }, timeout=GEN_TIMEOUT)
        try:
            return r["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError):
            raise ModelError(f"unexpected response from llama-server: {str(r)[:200]}")
