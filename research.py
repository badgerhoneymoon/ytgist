"""Research mode: a topic → the ~10 most useful YouTube videos → one combined brief.

    POST /api/research {"topic": "...", "n": 10, "native": false}

1. SEARCH   yt-dlp's YouTube search, 40 results, metadata only (nothing downloaded).
2. PICK     the model chooses the n that together teach the most: substantive, different
            people and angles, no reaction videos or shorts, at most 2 per channel.
3. GIST     each pick goes through the normal ytgist pipeline as an ordinary job, so it lands
            in the library, uses the cache, and can be opened on its own like any other video.
4. COMBINE  one brief across all of them: where they agree, where they disagree, what only one
            says, and which to watch first. Every claim cites [V3 12:34]; a citation the
            model invents is checked against that video's own summary and its timestamp is
            dropped if it isn't there (the same rule ytgist applies to every summary).

One research run at a time; the model is one physical resource. On the PC the whole run holds
a GPU booking in the Shed, so a video render can't start halfway through.
"""
import json
import os
import re
import threading
import time
import uuid

import model_client
import shed
import ytgist
import youtube_ingest as yt

DIR = os.path.expanduser("~/.ytgist/research")
SEARCH_N = 40
PER_CHANNEL = 2
MIN_SECONDS = 4 * 60            # shorts and clips teach little and crowd out real talks
MAX_SECONDS = 2 * 3600          # and a 6-hour stream would eat the whole run
ACTIVE = ("searching", "picking", "summarising", "combining")

_store = None                   # serve.py's JobStore; research submits ordinary gist jobs
_run_lock = None                # serve.py's _RUN: one model server at a time
_cancel_job = None              # serve.py's way to stop a job, running or queued
_lock = threading.Lock()
_active = {"id": None, "stop": None}


class Busy(Exception):
    pass


# ----------------------------------------------------------------------- storage
def _path(rid):
    return os.path.join(DIR, f"{rid}.json")


def _save(r):
    os.makedirs(DIR, exist_ok=True)
    r["updated"] = time.time()
    tmp = _path(r["id"]) + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    os.replace(tmp, _path(r["id"]))


def load(rid):
    if not re.fullmatch(r"[0-9a-f]{12}", rid or ""):
        return None
    try:
        with open(_path(rid), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def list_runs(limit=20):
    out = []
    try:
        names = sorted(os.listdir(DIR), reverse=True)
    except OSError:
        return out
    runs = []
    for n in names:
        if n.endswith(".json"):
            r = load(n[:-5])
            if r:
                runs.append(r)
    runs.sort(key=lambda r: r.get("created", 0), reverse=True)
    for r in runs[:limit]:
        out.append(summary_of(r))
    return out


def summary_of(r):
    picks = r.get("picks") or []
    return {"id": r["id"], "topic": r["topic"], "status": r["status"],
            "created": r.get("created"), "updated": r.get("updated"),
            "videos": len([p for p in picks if p["status"] != "dropped"]),
            "done": len([p for p in picks if p["status"] == "succeeded"]),
            "msg": r.get("msg", ""), "error": r.get("error")}


def attach(store, run_lock=None, cancel_job=None):
    """Called once by the engine. A run that was in flight when the engine stopped is marked
    interrupted, never silently resumed: re-running is cheap (every finished video is cached)
    but it should be asked for."""
    global _store, _run_lock, _cancel_job
    _store, _run_lock, _cancel_job = store, run_lock, cancel_job
    for s in list_runs(200):
        if s["status"] in ACTIVE:
            r = load(s["id"])
            r["status"], r["msg"] = "interrupted", "The engine stopped during this run. Start it again: finished videos are cached."
            _save(r)


# ------------------------------------------------------------------------- start
def start(topic, n=10, native=False):
    topic = " ".join(str(topic or "").split())
    if not 3 <= len(topic) <= 200:
        raise ValueError("Give a topic of 3–200 characters.")
    n = max(2, min(int(n or 10), 15))
    with _lock:
        if _active["id"]:
            cur = load(_active["id"])
            if cur and cur["status"] in ACTIVE:
                raise Busy(f"Already researching “{cur['topic']}”. Stop it or wait.")
        rid = uuid.uuid4().hex[:12]
        r = {"id": rid, "topic": topic, "n": n, "native": bool(native), "status": "searching",
             "msg": "Searching YouTube", "created": time.time(), "picks": [], "report": None,
             "error": None, "searched": 0}
        _save(r)
        stop = threading.Event()
        _active.update(id=rid, stop=stop)
    threading.Thread(target=_run, args=(rid, stop), daemon=True).start()
    return rid


def cancel(rid):
    with _lock:
        if _active["id"] != rid:
            return False
        _active["stop"].set()
    return True


def drop(rid, video):
    """Leave one video out. If its summary is still queued or running, that job is stopped."""
    r = load(rid)
    if not r:
        return False
    for p in r["picks"]:
        if p["id"] == video and p["status"] not in ("succeeded", "dropped"):
            if p.get("job") and _cancel_job:
                _cancel_job(p["job"])
            p["status"] = "dropped"
            _save(r)
            return True
        if p["id"] == video and p["status"] == "succeeded" and r["status"] in ACTIVE:
            p["status"] = "dropped"          # finished, but leave it out of the brief
            _save(r)
            return True
    return False


# --------------------------------------------------------------------- the run
def _run(rid, stop):
    r = load(rid)

    def say(status=None, msg=None):
        nonlocal r
        fresh = load(rid) or r            # drop() writes picks from another thread
        if r.get("picks") and not fresh.get("picks"):
            fresh["picks"] = r["picks"]
        r = fresh
        if status:
            r["status"] = status
        if msg is not None:
            r["msg"] = msg
        _save(r)

    try:
        with shed.hold("YouTube research: " + r["topic"][:40],
                       wait_msg=lambda t: say(msg=t), cancelled=stop.is_set):
            cands = search(r["topic"])
            say(msg=f"Found {len(cands)} videos. Choosing {r['n']}")
            r["searched"] = len(cands)
            if len(cands) < 2:
                raise RuntimeError("YouTube returned almost nothing for that topic. Try other words.")
            say(status="picking")
            picks = pick(r["topic"], cands, r["n"], stop)
            r["picks"] = [{**p, "status": "queued", "job": None, "stage": ""} for p in picks]
            say(status="summarising", msg=f"Summarising {len(picks)} videos")
            _gist_all(rid, stop, say)
            if stop.is_set():
                raise InterruptedError
            say(status="combining", msg="Writing the combined brief")
            r = load(rid)
            r["report"] = combine(r, stop)
            r["finished"] = time.time()
            _save(r)                     # before say(): say() re-reads the file
            _write_markdown(r)
            say(status="done", msg="")
    except InterruptedError:
        for p in (load(rid) or r).get("picks", []):
            if p.get("job") and p["status"] in ("queued", "running") and _cancel_job:
                _cancel_job(p["job"])
        say(status="cancelled", msg="Stopped")
    except Exception as e:                       # a failure must reach the page, not just stderr
        import traceback
        traceback.print_exc()
        r = load(rid) or r
        r["error"] = f"{e}"
        say(status="failed", msg="")
    finally:
        with _lock:
            if _active["id"] == rid:
                _active.update(id=None, stop=None)


def search(topic):
    """yt-dlp's YouTube search, metadata only. Nothing is downloaded here."""
    rc, out, err = yt._run(["yt-dlp", "--ignore-config", "--flat-playlist", "-J",
                            f"ytsearch{SEARCH_N}:{topic}"], timeout=90)
    if rc != 0:
        raise yt.IngestError(*yt._classify(err))
    cap = min(MAX_SECONDS, ytgist.max_minutes() * 60)
    seen, out_list = set(), []
    for i, e in enumerate(json.loads(out).get("entries") or []):
        vid, dur = e.get("id"), e.get("duration") or 0
        if not vid or vid in seen or e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        if not MIN_SECONDS <= dur <= cap:
            continue
        seen.add(vid)
        out_list.append({"id": vid, "rank": i + 1, "title": e.get("title") or vid,
                         "channel": e.get("channel") or e.get("uploader") or "",
                         "duration": dur, "views": e.get("view_count"),
                         "desc": (e.get("description") or "")[:160]})
    return out_list


PICK_SYSTEM = """You choose YouTube videos for someone researching a topic.
You get numbered search results. Pick the {n} that TOGETHER teach the most about the topic:
substantive talks, lectures, interviews, deep dives, tutorials and expert analysis, from
different people and angles. Skip reaction videos, compilations, music, trailers, clickbait
and near-duplicates. At most 2 from the same channel.
Answer with JSON only: {{"picks": [{{"n": <number>, "why": "<under 12 words>"}}]}}, most useful first."""


def _fmt_dur(s):
    s = int(s or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def pick(topic, cands, n, stop=None):
    lines = []
    for i, c in enumerate(cands, 1):
        views = f" · {c['views']:,} views" if c.get("views") else ""
        lines.append(f"{i}. {c['title']} — {c['channel']} · {_fmt_dur(c['duration'])}{views}"
                     + (f"\n   {c['desc']}" if c.get("desc") else ""))
    user = f"Topic: {topic}\n\n" + "\n".join(lines)
    chosen, why = [], {}
    try:
        with (_run_lock or threading.Lock()):
            if stop is not None and stop.is_set():
                raise InterruptedError
            with model_client.Server.acquire(len(user) // 2 + 1500, log=lambda *_: None) as srv:
                raw = srv.chat(PICK_SYSTEM.format(n=n), user, max_tokens=900, temperature=0.2)
        m = re.search(r"\{.*\}", raw, re.S)
        for p in (json.loads(m.group(0)).get("picks") if m else []) or []:
            i = int(p.get("n", 0)) - 1
            if 0 <= i < len(cands) and i not in chosen:
                chosen.append(i)
                why[i] = str(p.get("why", ""))[:120]
    except (model_client.ModelError, ValueError, KeyError, TypeError) as e:
        ytgist.log(f"  research: the model couldn't pick ({e}); falling back to search order")
    # The channel cap is enforced here, not trusted to the prompt; and if the model picked
    # too few, the list is topped up in YouTube's own relevance order.
    per, final = {}, []
    for i in chosen + [j for j in range(len(cands)) if j not in chosen]:
        ch = cands[i]["channel"].lower()
        if per.get(ch, 0) >= PER_CHANNEL:
            continue
        per[ch] = per.get(ch, 0) + 1
        final.append({**{k: cands[i][k] for k in ("id", "title", "channel", "duration", "views")},
                      "why": why.get(i, "")})
        if len(final) >= n:
            break
    return final


def _gist_all(rid, stop, say):
    """Each pick becomes an ordinary gist job. The engine's dispatcher runs them one by one;
    this just submits and watches."""
    r = load(rid)
    for p in r["picks"]:
        if p["status"] == "dropped":
            continue
        req = {"url": f"https://www.youtube.com/watch?v={p['id']}", "native": r["native"],
               "model": "dense", "refresh": False, "regen": False, "shots": False}
        item, _ = _store.submit("gist", req, f"research-{rid}-{p['id']}")
        p["job"] = item["id"]
    _save(r)
    while True:
        if stop.is_set():
            raise InterruptedError
        r = load(rid)
        pending = 0
        for p in r["picks"]:
            if p["status"] == "dropped" or not p.get("job"):
                continue
            j = _store.get(p["job"]) or {}
            st = j.get("status", "failed")
            p["status"] = {"queued": "queued", "running": "running", "cancelling": "running",
                           "succeeded": "succeeded"}.get(st, "failed")
            ev = j.get("event") or {}
            p["stage"] = ev.get("msg") or ev.get("stage") or ""
            if st == "failed":
                p["error"] = j.get("error") or "failed"
            if p["status"] in ("queued", "running"):
                pending += 1
        done = len([p for p in r["picks"] if p["status"] == "succeeded"])
        live = len([p for p in r["picks"] if p["status"] != "dropped"])
        r["msg"] = f"Summarised {done} of {live}"
        _save(r)
        if not pending:
            return
        time.sleep(2)


# ------------------------------------------------------------------------ combine
COMBINE_SYSTEM = """You write one research brief from several YouTube video summaries on the same topic.
Each summary is tagged [V1], [V2] and so on, and its points carry timestamps like [12:34].
Write {lang}. Use exactly this structure:

TL;DR <3-4 sentences: the answer to the topic, as these videos give it together>

## Where they agree
1. **<the point, stated as a claim>** <1-3 sentences> [V2 12:34] [V5 03:10]

## Where they disagree
1. **<what the disagreement is about>** <who says what> [V1 04:20] [V4 22:15]

## Only one of them says
1. **<the point>** <1-2 sentences> [V7 08:02]

## Watch first
1. [V3] <why, under 15 words>

Rules: 4-7 points under "Where they agree"; write "Nothing worth noting." under a section
with nothing real in it. Every point cites at least one source as [V<k> <mm:ss>] with a
timestamp that appears in that video's summary. Use only what the summaries say. Exactly 3
videos under "Watch first"."""

_LINK = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]\(https?://[^)]+\)")
_CITE = re.compile(r"\[V(\d{1,2})(?:[ ,]+(\d{1,2}:\d{2}(?::\d{2})?))?\]")


def _secs(ts):
    parts = [int(x) for x in ts.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def _digest(text):
    """A summary as the combine step sees it: links shortened to [mm:ss], quotes dropped
    (the brief cites timestamps; the quotes stay one click away in each video's own page)."""
    text = _LINK.sub(lambda m: f"[{m.group(1)}]", text)
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(">")).strip()


def combine(r, stop=None):
    sources, blocks, stamps = [], [], {}
    for p in r["picks"]:
        if p["status"] != "succeeded":
            continue
        g = ytgist.load_summary(p["id"], r["native"])
        if not g or not g.get("text"):
            continue
        k = len(sources) + 1
        text = _digest(g["text"])
        stamps[k] = {_secs(t) for t in re.findall(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", text)}
        sources.append({"k": k, "id": p["id"], "title": p["title"], "channel": p["channel"],
                        "duration": p["duration"]})
        blocks.append(f"[V{k}] {p['title']} — {p['channel']} ({_fmt_dur(p['duration'])})\n{text}")
    if len(sources) < 2:
        raise RuntimeError("Fewer than two videos could be summarised, so there is nothing to combine.")
    lang = ("in the language most of these videos are in" if r["native"] else "in English")
    user = f"Topic: {r['topic']}\n\n" + "\n\n".join(blocks)
    with (_run_lock or threading.Lock()):
        if stop is not None and stop.is_set():
            raise InterruptedError
        with model_client.Server.acquire(len(user) // 2 + 3000, log=lambda *_: None) as srv:
            need = srv.count_tokens(user) + 3000
            if need > srv.ctx:
                raise RuntimeError("Too much to combine in one pass; try fewer videos.")
            raw = srv.chat(COMBINE_SYSTEM.format(lang=lang), user, max_tokens=2600, temperature=0.3)
    by_k = {s["k"]: s for s in sources}
    dropped = [0]

    def cite(m):
        k, ts = int(m.group(1)), m.group(2)
        s = by_k.get(k)
        if not s:
            dropped[0] += 1
            return ""
        if ts and _secs(ts) in stamps.get(k, set()):
            return f"[V{k} {ts}](https://youtu.be/{s['id']}?t={_secs(ts)})"
        if ts:
            dropped[0] += 1             # a timestamp the summary doesn't have: keep the source, drop the time
        return f"[V{k}](https://youtu.be/{s['id']})"

    md = _CITE.sub(cite, raw).strip()
    return {"markdown": md, "sources": sources, "at": time.time(),
            "unverified_dropped": dropped[0]}


def _write_markdown(r):
    """The same brief as a plain .md next to the JSON, readable without any app."""
    rep = r["report"]
    head = [f"# Research: {r['topic']}", "",
            time.strftime("%Y-%m-%d %H:%M", time.localtime(rep["at"])) +
            f" · {len(rep['sources'])} videos", "", rep["markdown"], "", "## Sources", ""]
    for s in rep["sources"]:
        head.append(f"- **V{s['k']}** [{s['title']}](https://youtu.be/{s['id']}) — "
                    f"{s['channel']} · {_fmt_dur(s['duration'])}")
    with open(os.path.join(DIR, f"{r['id']}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(head) + "\n")


# ---------------------------------------------------------------- a page to read it
_PAGE_CSS = """
:root{--bg:#f6f4ef;--card:#fbfaf6;--ink:#1d1d1b;--mute:#76736b;--line:#e6e2d8;--accent:#c4652a;--ok:#2f9e78}
@media (prefers-color-scheme:dark){:root{--bg:#141517;--card:#1b1d20;--ink:#ecebe6;--mute:#9a978f;--line:#2a2d31;--accent:#e8894b;--ok:#55c79c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:760px;margin:0 auto;padding:40px 20px 80px}
.k{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
h1{font-size:30px;line-height:1.2;margin:6px 0 4px}h2{font-size:19px;margin:34px 0 10px}
.tldr{font-size:18px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin:22px 0}
ol{padding-left:22px}li{margin:0 0 14px}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.cite{font:12.5px ui-monospace,Menlo,monospace;white-space:nowrap}
.vids{display:grid;gap:8px;margin-top:10px}.vid{display:grid;grid-template-columns:34px 1fr auto;gap:10px;align-items:baseline;
 background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;font-size:14.5px}
.vid b{font-weight:600}.vid .m{color:var(--mute);font-size:13px}.st{font-size:12.5px;color:var(--mute);white-space:nowrap}
.st.ok{color:var(--ok)}.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin:18px 0 6px}.bar i{display:block;height:100%;background:var(--accent)}
"""


def page(r):
    """The brief as one self-contained HTML page. Escape first, then add markup: the text comes
    from strangers' transcripts."""
    import html as H
    live = r["status"] in ACTIVE
    out = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>{H.escape(r['topic'])}: research</title><style>{_PAGE_CSS}</style>"
           + ("<meta http-equiv=refresh content=5>" if live else "") + "<main>",
           f"<div class=k>YouTube research · {time.strftime('%d %b %Y', time.localtime(r['created']))}</div>",
           f"<h1>{H.escape(r['topic'])}</h1>"]
    picks = [p for p in r.get("picks", []) if p["status"] != "dropped"]
    if live or r["status"] != "done":
        done = len([p for p in picks if p["status"] == "succeeded"])
        pct = 8 if not picks else 10 + 80 * done / max(len(picks), 1)
        if r["status"] == "combining":
            pct = 94
        label = {"searching": "Searching YouTube", "picking": "Choosing videos",
                 "summarising": f"Summarising {done} of {len(picks)}", "combining": "Writing the brief",
                 "failed": "Failed", "cancelled": "Stopped", "interrupted": "Interrupted"}.get(r["status"], r["status"])
        out.append(f"<div class=bar><i style='width:{pct:.0f}%'></i></div><div class=k>{H.escape(label)}"
                   + (f" · {H.escape(r.get('msg') or '')}" if r.get("msg") and r["status"] in ACTIVE else "") + "</div>")
        if r.get("error"):
            out.append(f"<p>{H.escape(r['error'])}</p>")
    rep = r.get("report")
    if rep:
        md = H.escape(rep["markdown"])
        md = re.sub(r"\[(V\d+(?: \d{1,2}:\d{2}(?::\d{2})?)?)\]\((https://youtu\.be/[^)]+)\)",
                    r'<a class=cite href="\2" target=_blank rel=noopener>\1</a>', md)
        md = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", md)
        blocks, items = [], []

        def close():
            if items:
                blocks.append("<ol>" + "".join(f"<li>{i}</li>" for i in items) + "</ol>")
                items.clear()
        for line in md.splitlines():
            s = line.strip()
            if not s:
                continue
            m = re.match(r"^\d+[.)]\s+(.*)", s)
            if s.startswith("TL;DR"):
                close(); blocks.append(f"<div class=tldr>{s[5:].strip()}</div>")
            elif s.startswith("## "):
                close(); blocks.append(f"<h2>{s[3:]}</h2>")
            elif m:
                items.append(m.group(1))
            elif items:
                items[-1] += " " + s
            else:
                blocks.append(f"<p>{s}</p>")
        close()
        out += blocks
        out.append("<h2>Sources</h2>")
    out.append("<div class=vids>")
    vids = rep["sources"] if rep else picks
    for i, v in enumerate(vids, 1):
        k = v.get("k", i)
        st = "" if rep else v["status"]
        stl = {"succeeded": "summarised", "running": "working…", "queued": "queued",
               "failed": "failed"}.get(st, st)
        out.append(f"<div class=vid><span class=k>V{k}</span><span><a href='https://youtu.be/{v['id']}' target=_blank rel=noopener>"
                   f"<b>{H.escape(v['title'])}</b></a><br><span class=m>{H.escape(v['channel'])} · {_fmt_dur(v['duration'])}"
                   + (f" · {H.escape(v.get('why') or '')}" if v.get("why") else "") + "</span></span>"
                   f"<span class='st{' ok' if st == 'succeeded' else ''}'>{H.escape(stl)}</span></div>")
    out.append("</div></main>")
    return "".join(out)
