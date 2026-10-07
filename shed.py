"""Booking a SHARED GPU before using it. A no-op unless YTGIST_SHED is set.

On a machine where other things also use the GPU (video renders, a voice model), ytgist asks a
small booking service before it puts anything on the card and releases it afterwards. If
someone else holds the GPU, the job WAITS and says who it is waiting for, rather than starting
and running everybody out of memory.

The service is not part of ytgist. Any HTTP server with these endpoints works:
    POST /api/gpu/book     {"who", "task", "minutes"}  → 200, or 409 {"msg"} while someone else holds it
    POST /api/gpu/release  {"who"}
    GET  /api/status       {"gpu": {"used_gb"}, "job": ..., "svc": {"comfyui": {"running"}}}  (optional)
    POST /api/gpu/free     ask an idle ComfyUI to unload its model                     (optional)
The author's is the "Shed" agent on his PC. Set YTGIST_SHED=http://127.0.0.1:8700 to use one.
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


def busy():
    """Who else is on the GPU right now, as {"who", "task", "minutes", "text"}, or None.

    Two cases count: someone else's booking, and a ComfyUI render that nobody booked (Denis
    rendering by hand). The page asks this before it starts a job, so it can offer this Mac
    instead of a silent wait (Denis, 7 Oct). None when there is no Shed agent or it is down:
    unknown is not busy."""
    if not SHED:
        return None
    try:
        _, st = _call("GET", "/api/status", timeout=3)
    except Exception:
        return None
    now = time.time()
    b = st.get("booking") or {}
    if b.get("who") and b.get("who") != WHO and b.get("until", 0) > now:
        mins = max(1, round((b["until"] - now) / 60))
        task = b.get("task") or ""
        return {"who": b["who"], "task": task, "minutes": mins,
                "text": f"{b['who']} has it{' for ' + task if task else ''} · booked for up to {mins} more min"}
    j = st.get("job")
    if j and b.get("who") != WHO:
        pct = f" · {round(100 * j['value'] / j['max'])}%" if j.get("max") else ""
        return {"who": "ComfyUI", "task": j.get("name") or "a render", "minutes": None,
                "text": f"ComfyUI is rendering {j.get('name') or 'something'}{pct} (not booked)"}
    return None


def _unbooked_render():
    """A ComfyUI render with no booking behind it: the GPU is full even though the booking
    says free. Loading a 25 GB model next to it would crash one of the two."""
    try:
        _, st = _call("GET", "/api/status", timeout=3)
        b = st.get("booking") or {}
        return bool(st.get("job")) and not (b.get("who") and b.get("until", 0) > time.time())
    except Exception:
        return False


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
                if code == 200 and not _unbooked_render():
                    break
                if code == 200:                # booked, but ComfyUI is rendering unbooked:
                    _call("POST", "/api/gpu/release", {"who": WHO})     # give it back, wait
                    msg = {"msg": "ComfyUI is rendering (not booked)"}
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
