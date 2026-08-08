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
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ytgist

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
  #askgo { background:transparent; color:var(--ink); border:1px solid var(--line); }
  #askgo:hover:not(:disabled) { border-color:var(--ink); }
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
    <input id="ask" type="text" placeholder="ask something about this video">
    <button id="askgo" type="button">Ask</button>
  </div>
  <div id="bar"><div id="fill"></div></div>
  <div id="steps"></div>
  <div id="stage"></div>
  <div id="out"></div>
  <div id="timing"></div>
  <p class="note">Audio only, transcribed and summarised on this Mac. Transcripts are
     cached, so a second question about the same video is instant.</p>
</main>
<script>
const f=document.getElementById('f'), out=document.getElementById('out'),
      bar=document.getElementById('bar'), fill=document.getElementById('fill'),
      stage=document.getElementById('stage'), go=document.getElementById('go'),
      askBox=document.getElementById('ask'), askGo=document.getElementById('askgo'),
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

async function submit(question) {
  const url = document.getElementById('url').value;
  if (!url) return;
  out.innerHTML=''; document.getElementById('timing').innerHTML='';
  bar.style.display='block'; fill.style.width='0';
  go.disabled = askGo.disabled = true;
  stage.textContent = question ? 'answering your question…' : 'starting…';
  drawSteps('check');
  const r = await fetch('/api/gist', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url, ask: question || ''})});
  const {job} = await r.json();
  const es = new EventSource('/api/events?job='+job);
  const finish = () => { es.close(); go.disabled = askGo.disabled = false;
                         bar.style.display='none'; steps.innerHTML=''; };
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if (d.pct !== undefined) fill.style.width = d.pct+'%';
    if (d.stage) drawSteps(d.stage === 'cached' ? 'summarise' : d.stage);
    if (d.msg) stage.textContent = d.msg;
    if (d.error) { stage.textContent=''; out.innerHTML='<p class="err">'+d.error+'</p>';
                   finish(); }
    if (d.markdown !== undefined) {
      stage.textContent=''; finish();
      out.innerHTML = '<h2>'+d.title+'</h2>' + d.markdown;
      renderTimings(d.timings, d.duration, d.cached);
    }
  };
  es.onerror = () => finish();
}

f.onsubmit = e => { e.preventDefault(); submit(''); };
askGo.onclick = () => submit(askBox.value.trim());
askBox.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); submit(askBox.value.trim()); } };

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

    def do_GET(self):
        if self.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
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
        if self.path != "/api/gist":
            return self.send_error(404)
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        job = uuid.uuid4().hex
        q = queue.Queue()
        _jobs[job] = q
        threading.Thread(target=self._work, args=(q, req), daemon=True).start()
        body = json.dumps({"job": job}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _work(q, req):
        try:
            ytgist.run(req.get("url", ""), req.get("ask") or None,
                       req.get("model", "dense"), progress=q.put)
            res = getattr(ytgist.run, "last", None)
            if not res:
                q.put({"error": "No speech was found in that video."})
                return
            q.put({"title": html.escape(res["title"]),
                   "markdown": _render(res["markdown"], res.get("sentences") or []),
                   "timings": res.get("timings", {}),
                   "duration": res.get("duration", 0),
                   "cached": res.get("cached", False),
                   "sentences": res.get("sentences") or []})
        except ytgist.yt.IngestError as e:
            q.put({"error": html.escape(str(e))})
        except Exception as e:                      # a crash must reach the page, not just stderr
            traceback.print_exc()
            q.put({"error": html.escape(f"{type(e).__name__}: {e}")})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"ytgist → http://127.0.0.1:{PORT}   (ctrl-c to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()
