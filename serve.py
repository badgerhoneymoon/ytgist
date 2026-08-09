#!/usr/bin/env python3
"""A one-page local UI for ytgist: paste a link, watch it work, read the gist.

    ./serve            → http://127.0.0.1:8765

Stdlib only — http.server plus server-sent events. No Flask, no npm, nothing to install
or keep updated. This is a tool for one person on one machine; a dependency here would
cost more than it buys.

BOUND TO 127.0.0.1 ON PURPOSE. It shells out to yt-dlp with a URL from the request, so
exposing it on the network would be handing strangers a downloader running as Denis.
"""
import html
import json
import os
import queue
import re
import sys
import signal
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timing_log
import ytgist

_RUN = threading.Lock()   # the 27B is one physical resource; runs queue, never overlap


class Control:
    """The handle a running job can be stopped by.

    `server` is filled in once the model server exists — stopping it is what interrupts a
    generation that is already under way, since between checkpoints the worker is blocked
    inside one HTTP call for minutes."""

    def __init__(self):
        self.cancelled = threading.Event()
        self.server = None

    def stop(self):
        self.cancelled.set()
        srv = self.server
        if srv is not None:
            try:
                srv.stop()
            except Exception:
                pass


_ctl = {}                 # job id → Control

# WHAT IS RUNNING RIGHT NOW, so a page can rejoin it.
#
# The job already survived a reload — it runs in its own thread inside a detached engine, and
# closing the window or quitting the app never touched it. What did not survive was the
# VIEW: the browser forgot the job id, and nothing let it ask (Denis, 2026-08-09). Keeping
# the latest frame here means a reconnecting page can restore the progress bar immediately
# instead of waiting for the next frame — and keeping the finished result means it can pick
# up a summary that landed while it was away.
_RESULT_TTL = 600
_current = {"job": None, "url": "", "video": "", "native": False,
            "frame": {}, "result": None, "at": 0.0, "phase_at": 0.0}


def _publish(job, url, video, native):
    _current.update({"job": job, "url": url, "video": video, "native": bool(native),
                     "frame": {}, "result": None,
                     "at": time.time(), "phase_at": time.time()})


def _snapshot():
    """What /api/current answers. Drops a stale finished result so a page opened an hour
    later is not greeted with someone else's summary."""
    cur = dict(_current)
    if cur["result"] is not None and time.time() - cur["at"] > _RESULT_TTL:
        return {}
    # HOW LONG THIS PHASE HAS ALREADY BEEN RUNNING. Without it a reloaded page restarts its
    # countdown from the full estimate while the work is sixty seconds in — the job survived
    # but the clock did not (Denis, 2026-08-09).
    if cur["job"] and not cur["result"]:
        cur["phase_elapsed"] = round(time.time() - (cur["phase_at"] or time.time()), 1)
        cur["elapsed"] = round(time.time() - (cur["at"] or time.time()), 1)
    return cur

PORT = int(os.environ.get("YTGIST_PORT", "8765"))
_jobs = {}          # id → Queue of event dicts


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>ytgist</title>
<style>
  :root { --ink:#221E1A; --body:#4A443C; --soft:#6F6A62; --line:#E6E1D8;
          --canvas:#FBFAF7; --accent:#C2571A; }
  * { box-sizing: border-box; }
  body { margin:0; padding:48px 24px; background:var(--canvas); color:var(--ink);
         font:16px/1.6 -apple-system,BlinkMacSystemFont,sans-serif; }
  main { max-width: 760px; margin: 0 auto; }
  h1 { font-size:15px; letter-spacing:.14em; text-transform:uppercase; color:var(--soft);
       font-weight:600; margin:0 0 24px; }
  form { display:flex; gap:10px; }
  input[type=url], input[type=text] { flex:1; padding:13px 15px; font-size:15px;
       border:1px solid var(--line); border-radius:10px; background:transparent;
       color:var(--ink); font-family:inherit; }
  input:focus { outline:2px solid var(--accent); outline-offset:-1px; }
  button { padding:13px 22px; font-size:15px; font-weight:600; border:0; border-radius:10px;
       background:var(--ink); color:var(--canvas); cursor:pointer; font-family:inherit; }
  button:disabled { opacity:.45; cursor:default; }
  .row { display:flex; gap:10px; margin-top:10px; align-items:center; }
  .row input { flex:1; }
  #bar { height:4px; background:var(--line); border-radius:2px; margin:26px 0 14px;
       overflow:hidden; display:none; }
  #fill { height:100%; width:0; background:var(--accent); transition:width .5s ease; }
  #steps { display:flex; gap:8px; flex-wrap:wrap; font-size:13px; margin-bottom:8px; }
  #steps span { display:flex; align-items:center; gap:6px; color:var(--soft);
       padding:4px 10px; border:1px solid var(--line); border-radius:99px; }
  /* DONE = green, not faded. A finished step that looks dimmed reads as "skipped" or
     "disabled"; the eye wants a positive signal for work that succeeded (Denis). */
  #steps span.done { color:#3F7D5A; border-color:#3F7D5A;
       background:color-mix(in srgb, #3F7D5A 10%, transparent); }
  @media (prefers-color-scheme: dark) { #steps span.done { color:#63B187; border-color:#3F7D5A; } }
  #steps span.now { color:var(--canvas); background:var(--accent); border-color:var(--accent);
       font-weight:600; }
  #steps span.now::before { content:""; width:6px; height:6px; border-radius:50%;
       background:currentColor; animation:pulse 1s infinite; }
  #steps span.done::before { content:"✓"; font-size:11px; font-weight:700; }
  @keyframes pulse { 50% { opacity:.25 } }
  #stage { color:var(--soft); font-size:14px; min-height:22px; }
  #out { margin-top:30px; white-space:pre-wrap; }
  #timing { margin-top:28px; }
  #tbar { display:flex; height:10px; border-radius:5px; overflow:hidden; background:var(--line); }
  #tbar div { transition:width .4s ease; }
  #tlegend { display:flex; flex-wrap:wrap; gap:14px; margin-top:10px; font-size:13px;
             color:var(--soft); }
  #tlegend span { display:flex; align-items:center; gap:6px; }
  #tlegend i { width:9px; height:9px; border-radius:2px; display:inline-block; }
  #tlegend b { color:var(--ink); font-weight:600; font-variant-numeric:tabular-nums; }
  #out h2 { font-size:22px; margin:0 0 14px; letter-spacing:-.01em; }
  .tldr { display:block; font-size:17px; line-height:1.5; color:var(--body);
          padding:14px 16px; border-left:3px solid var(--accent); margin:0 0 26px;
          background:color-mix(in srgb, var(--accent) 6%, transparent); }
  b.take { display:block; font-size:17px; font-weight:650; letter-spacing:-.01em;
           margin:22px 0 4px; color:var(--ink); }
  details.ev { display:inline; }
  details.ev summary { display:inline; cursor:pointer; font-size:12px; color:var(--soft);
           margin-left:8px; list-style:none; border-bottom:1px dotted var(--line); }
  details.ev summary::-webkit-details-marker { display:none; }
  details.ev[open] { display:block; margin:8px 0 2px; padding:11px 14px;
           background:color-mix(in srgb, var(--ink) 4%, transparent);
           border-left:2px solid var(--line); border-radius:0 8px 8px 0;
           font-size:14px; line-height:1.55; color:var(--body); white-space:normal; }
  #out a { color:var(--accent); font-variant-numeric:tabular-nums; text-decoration:none;
       border-bottom:1px solid color-mix(in srgb, var(--accent) 35%, transparent); }
  #out a:hover { border-bottom-color:var(--accent); }
  .err { color:#C0392B; }
  .note { color:var(--soft); font-size:13px; margin-top:22px; }
</style>
<main>
  <h1>ytgist</h1>
  <form id="f">
    <input id="url" type="url" placeholder="paste a YouTube link" required autofocus>
    <button id="go">Gist</button>
  </form>
  <div class="row">
  </div>
  <div id="bar"><div id="fill"></div></div>
  <div id="steps"></div>
  <div id="stage"></div>
  <div id="out"></div>
  <div id="timing"></div>
  <p class="note">Audio only, transcribed and summarised on this Mac. Transcripts are
     cached, so a repeat of the same video is instant.</p>
</main>
<script>
const f=document.getElementById('f'), out=document.getElementById('out'),
      bar=document.getElementById('bar'), fill=document.getElementById('fill'),
      stage=document.getElementById('stage'), go=document.getElementById('go'),
      steps=document.getElementById('steps');

// The stages, in the order they happen. Naming them beats a bare percentage: "40%" tells
// you nothing, "transcribing (3 of 4)" tells you what the machine is doing and what is
// left (Denis, 2026-08-08).
const STAGES = [['check','checking'], ['download','downloading audio'],
                ['transcribe','transcribing'], ['summarise','summarising']];
function drawSteps(current) {
  const i = STAGES.findIndex(s => s[0] === current);
  steps.innerHTML = STAGES.map(([k,label], n) =>
    `<span class="${n < i ? 'done' : n === i ? 'now' : ''}">${label}</span>`).join('');
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  n.textContent = text;
  return n;
}

async function submit() {
  const url = document.getElementById('url').value;
  if (!url) return;
  out.innerHTML=''; document.getElementById('timing').innerHTML='';
  bar.style.display='block'; fill.style.width='0';
  go.disabled = true;
  stage.textContent = 'starting…';
  drawSteps('check');
  const r = await fetch('/api/gist', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url})});
  const {job} = await r.json();
  const es = new EventSource('/api/events?job='+job);
  const finish = () => { es.close(); go.disabled = false;
                         bar.style.display='none'; steps.innerHTML=''; };
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if (d.pct !== undefined) fill.style.width = d.pct+'%';
    if (d.stage) drawSteps(d.stage === 'cached' ? 'summarise' : d.stage);
    if (d.msg) stage.textContent = d.msg;
    if (d.error) { stage.textContent=''; out.replaceChildren(el('p','err',d.error));
                   finish(); }
    if (d.markdown !== undefined) {
      stage.textContent=''; finish();
      out.replaceChildren(el('h2','',d.title));      // textContent: no markup from a title
      out.insertAdjacentHTML('beforeend', d.markdown);
      renderTimings(d.timings, d.duration, d.cached);
    }
  };
  es.onerror = () => finish();
}

f.onsubmit = e => { e.preventDefault(); submit(''); };

// Where the time actually went. A stacked bar makes the imbalance obvious at a glance —
// on a cached video the summary IS the whole cost, which no list of numbers conveys.
const COLORS = {check:'#8E877C', download:'#6C8EA4', transcribe:'#4E8C6A',
                'model load':'#B08A3E', summarise:'#C2571A'};
function renderTimings(t, videoSecs, cached) {
  const el = document.getElementById('timing');
  if (!t || !Object.keys(t).length) { el.innerHTML=''; return; }
  const total = Object.values(t).reduce((a,b)=>a+b, 0);
  const bar = Object.entries(t).map(([k,v]) =>
      `<div style="width:${(v/total*100).toFixed(1)}%;background:${COLORS[k]||'#999'}"
            title="${k} ${v}s"></div>`).join('');
  const legend = Object.entries(t).map(([k,v]) =>
      `<span><i style="background:${COLORS[k]||'#999'}"></i>${k} <b>${v}s</b></span>`).join('');
  const speed = videoSecs ? ` · ${(videoSecs/total).toFixed(0)}× faster than watching it` : '';
  // An ABSENT phase is information too. Without this line the bar looks broken —
  // "where's transcription?" (Denis, 2026-08-08) — when in fact the cache skipped it.
  const note = cached
    ? `<div style="margin-top:8px;font-size:13px;color:var(--soft)">
         transcript was cached — download and transcription skipped</div>` : '';
  el.innerHTML = `<div id="tbar">${bar}</div><div id="tlegend">${legend}
      <span style="margin-left:auto">total <b>${total.toFixed(1)}s</b>${speed}</span></div>${note}`;
}
</script>
"""


def _render(markdown: str, sentences=None) -> str:
    """Scannable takeaways → HTML. **bold** headlines, timestamps as quiet links.

    Escape FIRST, then add markup: the text is derived from a stranger's transcript, and
    building HTML from it any other way is how a summary becomes an injection."""
    import re
    safe = html.escape(markdown)
    safe = re.sub(r"\[([^\]]+)\]\((https://youtu\.be/[^)]+)\)",
                  r'<a href="\2" target="_blank" rel="noopener">\1</a>', safe)
    # **headline** → its own line, big and bold: skimming these alone must carry the
    # argument, which is the whole reason this format won the A/B.
    safe = re.sub(r"\*\*(.+?)\*\*", r'<b class="take">\1</b>', safe)
    # EVIDENCE, Perplexity-style: every takeaway carries the transcript lines it came
    # from, one click away. This does NOT prove the claim — the check that a cited
    # timestamp exists is syntactic, and a wrong claim can quote a real moment. What it
    # does is make CHECKING free: the reader sees the speaker's own words next to the
    # summary instead of taking it on faith. That is the honest version of "verified",
    # and it is what Perplexity actually does — it shows sources, it doesn't prove them.
    if sentences:
        def evidence(m):
            secs = int(m.group(1))
            near = [s for s in sentences if secs - 8 <= s["start"] <= secs + 20]
            if not near:
                return m.group(0)
            quote = " ".join(s["text"].strip() for s in near)[:420]
            return (m.group(0) + '<details class="ev"><summary>what was said</summary>'
                    + html.escape(quote) + "…</details>")
        safe = re.sub(r'<a href="https://youtu\.be/[^?]+\?t=(\d+)"[^>]*>[^<]*</a>',
                      evidence, safe)
    safe = re.sub(r"^TL;DR\s*", '<span class="tldr">', safe, count=1, flags=re.M)
    if '<span class="tldr">' in safe:
        i = safe.index('<span class="tldr">')
        nl = safe.find("\n", i)
        if nl > 0:
            safe = safe[:nl] + "</span>" + safe[nl:]
    return safe


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                                        # the page is the log

    # CORS for the Next dev origin. The UI talks to this engine DIRECTLY rather than
    # through Next's rewrite, because that rewrite BUFFERS server-sent events: the job
    # ran to completion server-side while the page sat on "starting…" forever, since not
    # one frame reached the browser (proven 2026-08-08 — curl direct streamed instantly,
    # curl through :3210 returned nothing). Streaming and proxies are a known bad pair;
    # the fix is to stop proxying the stream.
    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ("http://127.0.0.1:3210", "http://localhost:3210"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/current"):
            body = json.dumps(_snapshot()).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/limits"):
            # The page used to hard-code "under 20 min ~1 min". Those numbers came from my
            # head; the engine has been measuring the real ones all along (Denis, 2026-08-09).
            bands = []
            for label, mins in (("under 20 min", 12), ("about an hour", 60), ("2 hours", 120)):
                secs = sum(ytgist.estimate(mins, cached=False).values())
                bands.append({"label": label, "secs": round(secs)})
            body = json.dumps({"bands": bands,
                               "max_hours": round(ytgist.max_minutes() / 60, 1),
                               "runs": len(timing_log._rows())}).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/history"):
            body = json.dumps(ytgist.history()).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/oembed"):
            self._oembed()
        elif self.path.startswith("/api/events"):
            self._events()
        else:
            self.send_error(404)

    def _oembed(self):
        """Title + thumbnail for a video id. No API key, no quota — and it doubles as
        proof to the user that we understood the link they pasted."""
        from urllib.parse import parse_qs, urlparse
        import urllib.request
        vid = (parse_qs(urlparse(self.path).query).get("v") or [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid or ""):
            return self.send_error(400)
        try:
            url = ("https://www.youtube.com/oembed?format=json&url="
                   + urllib.parse.quote(f"https://www.youtube.com/watch?v={vid}", safe=""))
            with urllib.request.urlopen(url, timeout=6) as r:
                body = r.read()
        except Exception:
            return self.send_error(502)
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _events(self):
        from urllib.parse import parse_qs, urlparse
        job = (parse_qs(urlparse(self.path).query).get("job") or [""])[0]
        q = _jobs.get(job)
        if q is None:
            self.send_error(404)
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        while True:
            ev = q.get()
            try:
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break                               # the tab closed; stop writing
            if "markdown" in ev or "error" in ev:
                break
        _jobs.pop(job, None)

    def do_POST(self):
        if self.path == "/api/expand":
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}") or {}
            # Takes the SAME run lock as a gist. Expanding starts a model server, and two
            # of those is how we lost a run this morning.
            with _RUN:
                try:
                    text = ytgist.expand(
                        req.get("video", ""), float(req.get("start") or 0),
                        float(req.get("end") or 0), req.get("headline", ""),
                        req.get("body", ""), bool(req.get("native")))
                    body = json.dumps({"text": text}).encode()
                except Exception as exc:
                    traceback.print_exc()
                    body = json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/cancel":
            n = int(self.headers.get("Content-Length", 0))
            job = (json.loads(self.rfile.read(n) or b"{}") or {}).get("job", "")
            ctl = _ctl.get(job)
            if ctl:
                ctl.stop()
            body = json.dumps({"ok": bool(ctl)}).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/api/gist":
            return self.send_error(404)
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        job = uuid.uuid4().hex
        q = queue.Queue()
        _jobs[job] = q
        ctl = Control()
        _ctl[job] = ctl
        _publish(job, req.get("url", ""), "", bool(req.get("native")))
        threading.Thread(target=self._work, args=(q, req, ctl, job), daemon=True).start()
        body = json.dumps({"job": job}).encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _work(q, req, ctl=None, job=None):
        try:
            if not _RUN.acquire(blocking=False):
                q.put({"stage": "summarise", "pct": 5,
                       "msg": "another summary is running — waiting for the model"})
                _RUN.acquire()
            # HEARTBEAT. Summarising emits nothing until it is finished — 183s on a
            # 57-minute video — and the browser cannot tell a working engine from a dead
            # one during silence, so its watchdog declared a perfectly healthy run dead
            # (2026-08-08). A tick every 10s carries the elapsed seconds, which is both
            # the keepalive and the only honest thing there is to report.
            last = {"stage": "check", "pct": 0, "msg": ""}

            def progress(f):
                if f.get("stage") and f["stage"] != last.get("stage"):
                    _current["phase_at"] = time.time()
                if f.get("stage"):
                    last.update({k: f[k] for k in ("stage", "pct", "msg") if k in f})
                if f.get("eta"):
                    last["eta"] = f["eta"]
                # The snapshot carries the CUMULATIVE state, not this one frame, so a page
                # that rejoins mid-run gets the whole picture rather than whatever happened
                # to arrive next.
                _current["frame"] = dict(last)
                q.put(f)

            stop = threading.Event()

            def beat():
                t0 = time.time()
                while not stop.wait(10):
                    q.put({**last, "elapsed": round(time.time() - t0)})

            threading.Thread(target=beat, daemon=True).start()
            try:
                ytgist.run(req.get("url", ""), req.get("model", "dense"),
                           refresh=bool(req.get("refresh")), progress=progress,
                           native=bool(req.get("native")),
                           regen=bool(req.get("regen")), control=ctl)
            finally:
                stop.set()
                _RUN.release()
            res = getattr(ytgist.run, "last", None)
            if not res:
                q.put({"error": "No speech was found in that video."})
                return
            final = {"title": res["title"],
                   # RAW markdown for the Next UI, which parses the ** markers itself to
                   # build real components. The pre-rendered HTML below is for the plain
                   # fallback page — sending only that made the React app find zero
                   # takeaways, because _render had already replaced every ** with <b>.
                   "raw": res["markdown"],
                   "markdown": _render(res["markdown"], res.get("sentences") or []),
                   "timings": res.get("timings", {}),
                   "duration": res.get("duration", 0),
                   "cached": res.get("cached", False),
                   "sentences": res.get("sentences") or [],
                   "expansions": res.get("expansions") or {}}
            _current["result"] = final
            _current["at"] = time.time()
            q.put(final)
        except ytgist.TooLong as e:
            q.put({"error": str(e)})
        except ytgist.Cancelled:
            # Not an error. Stopping is a legitimate outcome, and dressing it up in red
            # would teach the user that pressing their own Stop button broke something.
            q.put({"stopped": True})
        except ytgist.yt.IngestError as e:
            q.put({"error": str(e)})
        except Exception as e:                      # a crash must reach the page, not just stderr
            traceback.print_exc()
            q.put({"error": f"{type(e).__name__}: {e}"})
        finally:
            # A job that ends WITHOUT a result — stopped, refused, crashed — must leave no
            # trace in the snapshot, or a reload resurrects the view of work that is not
            # happening: press Stop, reload, and the bar carries on summarising something
            # already dead (Denis, 2026-08-09).
            if job and _current.get("job") == job and _current.get("result") is None:
                _current.update({"job": None, "url": "", "frame": {}, "at": 0.0})



if __name__ == "__main__":
    def _bye(*_):
        # GRACEFUL. A summary that is mid-generation when the engine is killed is simply
        # lost — the transcript survives, but the model time does not, and the next click
        # silently pays for it again. That is almost certainly what happened when a restart
        # of mine landed on a live run (2026-08-09). Waiting for the lock costs nothing when
        # nothing is running, and saves a minute of GPU when something is.
        if not _RUN.acquire(timeout=120):
            print("  a run did not finish in 120s — exiting anyway")
        sys.exit(0)

    # SIGTERM must unwind, not vanish. The engine now PARKS a 21GB llama-server between
    # jobs, and atexit only runs on a normal interpreter exit — so a plain `pkill` left the
    # parked server orphaned, which is the exact failure the warm pool was supposed to be
    # safe against (measured, 2026-08-08). Turning the signal into sys.exit runs atexit,
    # which stops it.
    for _sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(_sig, _bye)

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"ytgist → http://127.0.0.1:{PORT}   (ctrl-c to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()
