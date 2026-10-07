"""GPU booking on Denis's PC (the "AI shed"). A no-op on the Mac.

The PC's GPU is shared: H3 video renders, a voice model, and now ytgist. The Shed agent on the
PC keeps one booking at a time (http://127.0.0.1:8700/agents.md). ytgist books before it puts
anything on the GPU and releases when it is done; if someone else holds the GPU, the job WAITS
and says who it is waiting for, rather than starting and running everybody out of memory.

Set YTGIST_SHED=http://127.0.0.1:8700 to turn this on. Unset (the Mac), every call is free.
"""
import contextlib
import json
import os
import threading
import time
import urllib.error
import urllib.request

SHED = os.environ.get("YTGIST_SHED", "").rstrip("/")
WHO = "ytgist"
RENEW_EVERY = 240            # seconds; a booking is renewed while work is still running
BOOK_MINUTES = 15

_lock = threading.Lock()
_holds = {"n": 0, "task": "", "renewer": None, "stop": None, "ready": threading.Event()}


def _call(method, path, body=None, timeout=10):
    req = urllib.request.Request(SHED + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def _book(task):
    return _call("POST", "/api/gpu/book", {"who": WHO, "task": task, "minutes": BOOK_MINUTES})


def _free_comfy_if_idle():
    """ComfyUI keeps its last model in VRAM after a render. If nothing is rendering, ask it to
    let go: the 27B needs ~25 GB of the 32."""
    try:
        _, st = _call("GET", "/api/status")
        if st.get("svc", {}).get("comfyui", {}).get("running") and not st.get("job"):
            if (st.get("gpu") or {}).get("used_gb", 0) > 6:
                _call("POST", "/api/gpu/free")
                time.sleep(4)
    except Exception:
        pass


def _renew(stop):
    while not stop.wait(RENEW_EVERY):
        try:
            _book(_holds["task"])
        except Exception:
            pass


@contextlib.contextmanager
def hold(task, wait_msg=None, cancelled=None):
    """Hold the GPU for the duration of the block. Re-entrant: a research run holds it across
    ten videos, and each video's own hold just joins in.

    wait_msg(text) is called while waiting for someone else's booking, so the caller can show
    it. cancelled() returning True stops the wait (raises InterruptedError)."""
    if not SHED:
        yield
        return
    with _lock:
        joined = _holds["n"] > 0
        _holds["n"] += 1
        if not joined:
            _holds["ready"].clear()
    try:
        if joined:
            while not _holds["ready"].wait(1):     # the first holder is still waiting for the GPU
                if cancelled and cancelled():
                    raise InterruptedError("cancelled while waiting for the GPU")
        else:
            shown = None
            while True:
                try:
                    code, msg = _book(task)
                except OSError:
                    break                      # the Shed agent is down: don't block work on it
                if code == 200:
                    break
                text = "Waiting for the GPU: " + (msg.get("msg") or "someone else is using it")
                if wait_msg and text != shown:
                    wait_msg(text)
                    shown = text
                for _ in range(15):
                    if cancelled and cancelled():
                        raise InterruptedError("cancelled while waiting for the GPU")
                    time.sleep(1)
            _free_comfy_if_idle()
            stop = threading.Event()
            _holds.update(task=task, stop=stop,
                          renewer=threading.Thread(target=_renew, args=(stop,), daemon=True))
            _holds["renewer"].start()
            _holds["ready"].set()
        yield
    finally:
        with _lock:
            _holds["n"] -= 1
            last = _holds["n"] == 0
        if last:
            _holds["ready"].clear()
            if _holds.get("stop"):
                _holds["stop"].set()
            try:
                import model_client
                model_client.warm_stop()       # a parked 20 GB server must not outlive the booking
            except Exception:
                pass
            try:
                import sys
                if "ytgist" in sys.modules:
                    sys.modules["ytgist"].asr_unload()   # nor Parakeet
            except Exception:
                pass
            try:
                _call("POST", "/api/gpu/release", {"who": WHO})
            except OSError:
                pass
