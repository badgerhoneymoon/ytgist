"""Booking a SHARED GPU before using it. A no-op unless YTGIST_SHED is set.

On a machine where other things also use the GPU (video renders, a voice model), ytgist asks a
small booking service before it puts anything on the card and releases it afterwards. If
someone else holds the GPU, the job WAITS and says who it is waiting for, rather than starting
and running everybody out of memory.

The service is not part of ytgist. Any HTTP server with these endpoints works:
    POST /api/gpu/book     {"who", "task", "minutes"}  → 200, or 409 {"msg"} while someone else holds it
                           optional "gb" (share the card; none = all of it) and "interactive" (false =
                           background work, which waits behind a person). Booking again renews/resizes.
    POST /api/gpu/release  {"who"}
    GET  /api/status       {"gpu": {"used_gb"}, "job": ..., "svc": {"comfyui": {"running"}}}  (optional;
                           "bookings" and "share": {"free_gb"} when the service shares the card)
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
_holds = {"n": 0, "task": "", "gb": None, "interactive": True, "renewer": None, "stop": None,
          "ready": threading.Event()}


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


def _book(task, gb=None, interactive=True):
    """Book (or renew, or resize) our one booking. gb=None is the whole card, as before; a number lets
    others share the card (Shed agent, 8 Oct 2026). Background work says interactive=False and waits
    behind a person."""
    body = {"who": WHO, "task": task, "minutes": BOOK_MINUTES, "interactive": bool(interactive)}
    if gb is not None:
        body["gb"] = gb
    return _call("POST", "/api/gpu/book", body)


def _holds_comfy(bookings):
    """Does any live booking cover ComfyUI (a ComfyUI booking or the whole card)?"""
    now = time.time()
    return any((b.get("comfy") or b.get("gb") is None) and b.get("until", 0) > now for b in bookings)


def busy(need_gb=None):
    """Who is in the way of a job needing `need_gb` GB (None: the whole card), as
    {"who", "task", "minutes", "text"}, or None.

    In the way: someone else's whole-card booking, a ComfyUI render nobody booked (Denis rendering
    by hand), or, for the whole card, any other booking; for a smaller job, too little memory left.
    The page asks this before it starts a job, so it can offer this Mac instead of a silent wait
    (Denis, 7 Oct). None when there is no Shed agent or it is down: unknown is not busy."""
    if not SHED:
        return None
    try:
        _, st = _call("GET", "/api/status", timeout=3)
    except Exception:
        return None
    now = time.time()
    if st.get("bookings") is None:                       # an older Shed: one booking for the whole card
        b = st.get("booking")
        bookings = [dict(b, gb=None)] if b else []
    else:
        bookings = st["bookings"]
    others = [b for b in bookings if b.get("who") != WHO and b.get("until", 0) > now]
    whole = next((b for b in others if b.get("gb") is None), None)
    if whole or (need_gb is None and others):
        b = whole or others[0]
        mins = max(1, round((b["until"] - now) / 60))
        task = b.get("task") or ""
        return {"who": b["who"], "task": task, "minutes": mins,
                "text": f"{b['who']} has it{' for ' + task if task else ''} · booked for up to {mins} more min"}
    j = st.get("job")
    if j and not _holds_comfy(bookings):
        pct = f" · {round(100 * j['value'] / j['max'])}%" if j.get("max") else ""
        return {"who": "ComfyUI", "task": j.get("name") or "a render", "minutes": None,
                "text": f"ComfyUI is rendering {j.get('name') or 'something'}{pct} (not booked)"}
    free = (st.get("share") or {}).get("free_gb")
    if need_gb is not None and others and free is not None and free < need_gb:
        held = ", ".join(f"{b['who']} {b['gb']:g} GB" for b in others)
        return {"who": others[0]["who"], "task": others[0].get("task") or "", "minutes": None,
                "text": f"The GPU is full: {held}, {free:g} GB free"}
    return None


def _unbooked_render():
    """A ComfyUI render with no booking behind it: the GPU is full even though the booking
    says free. Loading a 25 GB model next to it would crash one of the two."""
    try:
        _, st = _call("GET", "/api/status", timeout=3)
        if st.get("bookings") is None:
            b = st.get("booking") or {}
            return bool(st.get("job")) and not (b.get("who") and b.get("until", 0) > time.time())
        return bool(st.get("job")) and not _holds_comfy(st["bookings"])
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
            _book(_holds["task"], _holds.get("gb"), _holds.get("interactive", True))
        except Exception:
            pass


def _more(gb, held):
    """Does a job needing `gb` need more than the booking we hold (`held`)? None is the whole card."""
    if held is None:
        return False
    return gb is None or gb > held


def _wait_for(task, gb, interactive, wait_msg, cancelled, joined=False):
    """Book `gb` (or grow our booking to it), waiting while the GPU can't take it."""
    shown = None
    while True:
        try:
            code, msg = _book(task, gb, interactive)
        except OSError:
            return                                   # the Shed agent is down: don't block work on it
        if code == 200 and (gb is not None or joined or not _unbooked_render()):
            return
        if code == 200:                              # whole card booked, but ComfyUI is rendering unbooked:
            _call("POST", "/api/gpu/release", {"who": WHO})          # give it back, wait
            msg = {"msg": "ComfyUI is rendering (not booked)"}
        text = "Waiting for the GPU: " + (msg.get("msg") or "someone else is using it")
        if wait_msg and text != shown:
            wait_msg(text)
            shown = text
        # A small booking asks again sooner: it fits as soon as someone shrinks, and holds no one up.
        for _ in range(5 if gb is not None else 15):
            if cancelled and cancelled():
                raise InterruptedError("cancelled while waiting for the GPU")
            time.sleep(1)


@contextlib.contextmanager
def hold(task, wait_msg=None, cancelled=None, gb=None, interactive=True):
    """Hold the GPU for the duration of the block. Re-entrant: a research run holds it across
    ten videos, and each video's own hold just joins in (growing the one booking if it needs more).

    gb: how many GB the block needs; None takes the whole card, as before. A number lets others
    share the card (a transcript needs ~4). interactive=False marks background work (research),
    which waits behind a person.

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
            if _more(gb, _holds.get("gb")):        # a bigger job inside a smaller hold: grow the booking
                inter = _holds.get("interactive", True) or interactive
                _wait_for(task, gb, inter, wait_msg, cancelled, joined=True)
                _holds.update(gb=gb, interactive=inter)
        else:
            _wait_for(task, gb, interactive, wait_msg, cancelled)
            if gb is None or gb > 8:               # a small booking leaves ComfyUI's models alone
                _free_comfy_if_idle()
            stop = threading.Event()
            _holds.update(task=task, gb=gb, interactive=interactive, stop=stop,
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
