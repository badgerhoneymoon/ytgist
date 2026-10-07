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
WIDEN_BELOW = 6                 # fewer on-topic picks than this → one wider search round
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
def start(topic, n=10, native=False, shots=False):
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
        r = {"id": rid, "topic": topic, "n": n, "native": bool(native), "shots": bool(shots),
             "status": "searching",
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


def recombine(rid):
    """Write the brief again from the summaries already made (after a drop, or a new layout).
    No searching and no summarising: only the combine step, under its own GPU booking."""
    with _lock:
        if _active["id"]:
            raise Busy("Another research is running.")
        r = load(rid)
        if not r or r["status"] in ACTIVE:
            return False
        stop = threading.Event()
        _active.update(id=rid, stop=stop)
        r["status"], r["msg"] = "combining", "Writing the brief again"
        _save(r)

    def work():
        try:
            with shed.hold("YouTube research: rewriting a brief", cancelled=stop.is_set):
                rr = load(rid)
                rr["report"] = combine(rr, stop)
                rr["status"], rr["msg"], rr["error"] = "done", "", None
                _save(rr)
                _write_markdown(rr)
        except Exception as e:
            rr = load(rid)
            rr["status"], rr["error"] = "failed", str(e)
            _save(rr)
        finally:
            with _lock:
                if _active["id"] == rid:
                    _active.update(id=None, stop=None)
    threading.Thread(target=work, daemon=True).start()
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
        # drop() writes picks from another thread, so the file wins for picks; this thread's
        # own fields (queries, how many were found) win over the file.
        fresh = load(rid) or {}
        merged = {**r, **fresh}
        if r.get("picks") and not fresh.get("picks"):
            merged["picks"] = r["picks"]
        for k in ("queries", "searched", "wider_at", "subject", "error"):
            if k in r:
                merged[k] = r[k]
        r = merged
        if status:
            r["status"] = status
        if msg is not None:
            r["msg"] = msg
        _save(r)

    try:
        with shed.hold("YouTube research: " + r["topic"][:40],
                       wait_msg=lambda t: say(msg=t), cancelled=stop.is_set):
            say(msg="Working out what to search for")
            r["queries"] = queries_for(r["topic"], stop)
            say(msg=f"Searching YouTube {len(r['queries'])} ways")
            cands = search_all(r["queries"])
            r["searched"] = len(cands)
            say(msg=f"Found {len(cands)} videos. Choosing {r['n']}")
            if len(cands) < 2:
                raise RuntimeError("YouTube returned almost nothing for that topic. Try other words.")
            say(status="picking")
            picks, r["subject"] = pick(r["topic"], cands, r["n"], stop)
            if len(picks) < min(r["n"], WIDEN_BELOW):
                say(msg=f"Only {len(picks)} fit. Searching wider")
                more = queries_for(r["topic"], stop, tried=r["queries"])
                if more:
                    r["wider_at"] = len(r["queries"])
                    r["queries"] = r["queries"] + more
                    cands = search_all(r["queries"], limit=72)
                    r["searched"] = len(cands)
                    say(msg=f"Found {len(cands)} videos. Choosing again")
                    picks, r["subject"] = pick(r["topic"], cands, r["n"], stop)
            if len(picks) < 2:
                raise RuntimeError("Fewer than two videos on YouTube fit that topic closely. "
                                   "Try broader words, or split it into two topics.")
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


QUERY_SYSTEM = """You turn a research topic into YouTube search queries.
People type short searches, and YouTube matches long ones badly: a query that stacks every
detail of a topic finds videos that fit none of it. So first split the topic into its SUBJECT
(what the videos must be about) and its CONSTRAINTS (audience, place, budget, platform...).
Write 3 queries of 2-6 words each, the way a person types them:
1. the subject alone, in the plainest words;
2. the subject plus its single most important constraint;
3. the subject as a practitioner would search it, or as examples / a case study.
Same language as the topic.
Answer with JSON only: {"queries": ["...", "...", "..."]}"""

WIDER_SYSTEM = """A YouTube search for a research topic found too few videos that fit it.
Write 3 BROADER searches of 2-6 words each that would find useful neighbours: drop the
narrowest constraints, name the general activity behind the topic, or use a common synonym.
Don't repeat a search that was already tried. Same language as the topic.
Answer with JSON only: {"queries": ["...", "...", "..."]}"""


def queries_for(topic, stop=None, tried=None):
    """The topic as typed, plus three short rewrites by the model. A single YouTube search only
    sees what YouTube ranks for that exact phrase (Denis, 7 Oct); pooling several finds more.
    Rewrites are kept short because long, stacked queries return noise (Denis, 7 Oct: a
    Facebook-ads question got a printer review and a Pakistan TikTok guide).

    With `tried`, the first round found too few videos that fit: three broader searches come
    back instead, without the topic or any query already tried."""
    out = [] if tried else [topic]
    seen = [q.lower() for q in (tried or [])]
    system = WIDER_SYSTEM if tried else QUERY_SYSTEM
    user = f"Topic: {topic}" + ("\nAlready tried:\n" + "\n".join(f"- {q}" for q in tried) if tried else "")
    try:
        with (_run_lock or threading.Lock()):
            if stop is not None and stop.is_set():
                raise InterruptedError
            with model_client.Server.acquire(2000, log=lambda *_: None) as srv:
                raw = srv.chat(system, user, max_tokens=200, temperature=0.0)   # same topic, same searches
        m = re.search(r"\{.*\}", raw, re.S)
        for q in (json.loads(m.group(0)).get("queries") if m else []) or []:
            q = " ".join(str(q).split())[:120]
            if q and q.lower() not in seen + [x.lower() for x in out]:
                out.append(q)
    except (model_client.ModelError, ValueError, TypeError) as e:
        ytgist.log(f"  research: no query rewrites ({e})")
    return out[:3] if tried else out[:4]


def search_all(queries, limit=48):
    """Every query's results, pooled: a video found by several queries ranks by its best
    position, and the pool is interleaved so no single query crowds out the others."""
    pooled = {}
    for qi, q in enumerate(queries):
        for c in search(q, per=SEARCH_N if qi == 0 else 25):
            old = pooled.get(c["id"])
            if old is None or c["rank"] < old["rank"]:
                pooled[c["id"]] = {**c, "query": qi}
    return sorted(pooled.values(), key=lambda c: (c["rank"], c["query"]))[:limit]


def search(topic, per=SEARCH_N):
    """yt-dlp's YouTube search, metadata only. Nothing is downloaded here."""
    # approximate_date turns YouTube's "2 years ago" into a timestamp, at no extra cost: a flat
    # search has no dates otherwise, and the picker needs them to prefer recent videos.
    rc, out, err = yt._run(["yt-dlp", "--ignore-config", "--flat-playlist", "-J",
                            "--extractor-args", "youtubetab:approximate_date",
                            f"ytsearch{per}:{topic}"], timeout=90)
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
                         "duration": dur, "views": e.get("view_count"), "ts": e.get("timestamp"),
                         "desc": (e.get("description") or "")[:160]})
    return out_list


PICK_SYSTEM = """You judge YouTube search results for someone researching a topic.
First split the topic into its SUBJECT (the core thing a video must be about, in a few words,
with no audience, place or platform in it) and its DETAILS (audience, place, platform,
budget...). Then score EVERY numbered result by its title and description:
3 = about the subject and matching its details
2 = about the subject, with some details different or missing (another platform, market or
    audience, or a similar product); it still answers part of the question directly
1 = only near it: general advice from the wider field, product reviews, lists of ideas,
    business basics that never touch the subject
0 = another subject, or a reaction video, compilation, music, trailer or clickbait
When several results say the
same thing from the same angle (the same tutorial from different channels), score only the
best of them 2 or 3 and the rest 1: the picks should cover different parts of the question.
Also say whether the topic depends on things that change fast (tools, AI models, versions,
prices, platforms, ad rules, algorithms).
Answer with JSON only:
{"subject": "<the subject, without the details>", "details": ["..."], "changes": true or false,
 "scores": [{"n": <number>, "s": <0-3>, "why": "<under 12 words, only when s is 2 or 3>"}]}"""

OLD_YEARS, TOO_OLD_YEARS = 2, 3   # for fast-changing topics: sorted last / left out


def _age(ts, now=None):
    """'3 weeks ago', '2 years ago': as rough as YouTube's own label, which is where it comes from."""
    if not ts:
        return ""
    days = max(0, ((now or time.time()) - ts) / 86400)
    for size, unit in ((365, "year"), (30, "month"), (7, "week"), (1, "day")):
        if days >= size:
            n = int(days // size)
            return f"{n} {unit}{'s' if n > 1 else ''} ago"
    return "today"


def _fmt_dur(s):
    s = int(s or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def pick(topic, cands, n, stop=None):
    lines = []
    for i, c in enumerate(cands, 1):
        views = f" · {c['views']:,} views" if c.get("views") else ""
        age = f" · {_age(c.get('ts'))}" if c.get("ts") else ""
        lines.append(f"{i}. {c['title']} — {c['channel']} · {_fmt_dur(c['duration'])}{views}{age}"
                     + (f"\n   {c['desc']}" if c.get("desc") else ""))
    user = f"Topic: {topic}\nToday: {time.strftime('%Y-%m-%d')}\n\n" + "\n".join(lines)
    scores, why, subject, changes, judged = {}, {}, "", False, False
    budget = 400 + 30 * len(cands)          # one short line per result
    try:
        with (_run_lock or threading.Lock()):
            if stop is not None and stop.is_set():
                raise InterruptedError
            with model_client.Server.acquire(len(user) // 2 + budget + 600, log=lambda *_: None) as srv:
                raw = srv.chat(PICK_SYSTEM, user, max_tokens=budget, temperature=0.0)   # same pool, same picks
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
        subject = " ".join(str(data.get("subject") or "").split())[:80]
        changes = bool(data.get("changes"))
        for row in data.get("scores") or []:
            i = int(row.get("n", 0)) - 1
            if 0 <= i < len(cands) and i not in scores:
                scores[i] = max(0, min(3, int(row.get("s", 0))))
                why[i] = str(row.get("why") or "")[:120]
        judged = bool(scores)
    except (model_client.ModelError, ValueError, KeyError, TypeError, AttributeError) as e:
        ytgist.log(f"  research: the model couldn't score the results ({e}); falling back to search order")

    # The model scores every result; the choosing happens here, where it is the same every
    # time. Only 3s and 2s are ever picked, however few: topping up from YouTube's order is
    # what filled runs with a printer review and a Pakistan TikTok guide (Denis, 7 Oct). On a
    # topic that changes fast, old videos go last and very old ones not at all (Denis, 7 Oct:
    # "I don't think we should rely on old videos"). Search order is only the fallback for a
    # model that gave no answer at all.
    now = time.time()

    def years(c):
        return (now - c["ts"]) / (365 * 86400) + 0.01 if c.get("ts") else 0

    if judged:
        order = [i for i in scores if scores[i] >= 2
                 and not (changes and years(cands[i]) >= TOO_OLD_YEARS)]
        order.sort(key=lambda i: (-scores[i], changes and years(cands[i]) >= OLD_YEARS,
                                  cands[i].get("rank", 99)))
    else:
        order = list(range(len(cands)))
    per, final = {}, []
    for i in order:
        ch = cands[i]["channel"].lower()
        if per.get(ch, 0) >= PER_CHANNEL:
            continue
        per[ch] = per.get(ch, 0) + 1
        final.append({**{k: cands[i].get(k) for k in ("id", "title", "channel", "duration", "views", "ts")},
                      "why": why.get(i, ""),
                      "fit": {3: "fits", 2: "close"}.get(scores.get(i), "")})
        if len(final) >= n:
            break
    return final, subject


def _gist_all(rid, stop, say):
    """Each pick becomes an ordinary gist job. The engine's dispatcher runs them one by one;
    this just submits and watches."""
    r = load(rid)
    for p in r["picks"]:
        if p["status"] == "dropped":
            continue
        req = {"url": f"https://www.youtube.com/watch?v={p['id']}", "native": r["native"],
               "model": "dense", "refresh": False, "regen": False, "shots": bool(r.get("shots"))}
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
# THE SAME SHAPE AS ONE VIDEO'S GIST (Denis, 7 Oct): TL;DR, then numbered takeaways whose
# bold headlines state the point, a few short sentences each, the moments they rest on, and
# what was said there. The comparative sections (agree / disagree / only one / watch first)
# stay, but as extras the page shows collapsed.
COMBINE_SYSTEM = """You write one research brief from several YouTube video summaries on the same topic.
Each summary is tagged [V1], [V2] and so on, and its points carry timestamps like [12:34].
Write {lang}. Use exactly this structure:

TL;DR <3-4 sentences: the answer to the topic, as these videos give it together>

1. **<headline that STATES THE POINT, not the topic>** [V2 12:34] [V5 03:10]
<2-4 short sentences that keep the reasoning: why it holds, how, with what numbers>

2. **<next point>** [V1 04:20]
<...>

## Where they agree
1. **<point>** <1-2 sentences> [V2 12:34] [V5 03:10]

## Where they disagree
1. **<what about>** <who says what> [V1 04:20] [V4 22:15]

## Only one of them says
1. **<point>** <1-2 sentences> [V7 08:02]

## Watch first
1. [V3] <why, under 15 words>

Rules:
- Headlines in sentence case, like a sentence, not Title Case.
- {steps} numbered takeaways, ordered so that reading only the bold headlines gives the
  shape of the answer. Combine what several videos say into one takeaway; do not go video by video.
- Every takeaway and every point cites at least one source as [V<k> <mm:ss>], with a
  timestamp that appears in that video's summary. Put the citations right after the headline.
- The extra sections are lists, not one line each: 3-6 points under "Where they agree",
  1-4 under "Where they disagree", 2-5 under "Only one of them says" - as many as are real.
- Each video says roughly when it was published. On anything that changes (tools, versions,
  prices, platforms, rules), trust the newer video when they differ, and if a takeaway rests
  only on videos 2 or more years old, say so in its text ("from a 2023 video").
- Use only what the summaries say. Write "Nothing worth noting." under a section with
  nothing real in it. Exactly 3 videos under "Watch first"."""

_LINK = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]\(https?://[^)]+\)")
_CITE = re.compile(r"\[V(\d{1,2})(?:[ ,]+(\d{1,2}:\d{2}(?::\d{2})?))?\]")
_STEP = re.compile(r"^\s*\d+[.)]\s+\*\*(.+?)\*\*\s*(.*)$")


def _secs(ts):
    parts = [int(x) for x in ts.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def _stamp(secs):
    secs = int(secs)
    return (f"{secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}" if secs >= 3600
            else f"{secs // 60:02d}:{secs % 60:02d}")


def _digest(text):
    """A summary as the combine step sees it: links shortened to [mm:ss], quotes dropped
    (the brief cites timestamps; what was said is attached afterwards, from the transcript)."""
    text = _LINK.sub(lambda m: f"[{m.group(1)}]", text)
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(">")).strip()


def _said(vid, secs):
    """What was said at a moment: the transcript itself, the same window a single video's
    page shows. Taken from the cache, never from the model, so it cannot be invented."""
    d = ytgist.load_cached(vid) or {}
    near = [s["text"].strip() for s in d.get("sentences", []) if secs - 8 <= s["start"] <= secs + 22]
    return " ".join(near)[:480]


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
                        "duration": p["duration"], "ts": p.get("ts")})
        age = f", published {_age(p.get('ts'))}" if p.get("ts") else ""
        blocks.append(f"[V{k}] {p['title']} — {p['channel']} ({_fmt_dur(p['duration'])}{age})\n{text}")
    if len(sources) < 2:
        raise RuntimeError("Fewer than two videos could be summarised, so there is nothing to combine.")
    lang = "in the language most of these videos are in" if r["native"] else "in English"
    steps = "6-9" if len(sources) <= 5 else "8-12"
    user = f"Topic: {r['topic']}\n\n" + "\n\n".join(blocks)
    with (_run_lock or threading.Lock()):
        if stop is not None and stop.is_set():
            raise InterruptedError
        with model_client.Server.acquire(len(user) // 2 + 4500, log=lambda *_: None) as srv:
            if srv.count_tokens(user) + 4500 > srv.ctx:
                raise RuntimeError("Too much to combine in one pass; try fewer videos.")
            raw = srv.chat(COMBINE_SYSTEM.format(lang=lang, steps=steps), user,
                           max_tokens=4200, temperature=0.3)
    return _structure(raw, sources, stamps, r["native"])


_HEAD_STAMP = re.compile(r"^\s*\d+[.)]\s+\*\*.*?\*\*\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]", re.M)


def _shots_for(source, native):
    """{moment a takeaway of this video cites: the second of the screenshot found for it}.

    A video's own screenshot pass already looked at each of ITS takeaways and kept a frame
    only where one matched; a research takeaway citing that same verified moment can show
    the same picture. Nothing is looked at again, and nothing is guessed."""
    g = ytgist.load_summary(source["id"], native) or {}
    if g.get("frames_v") != ytgist.FRAMES_V:
        return {}
    heads = [_secs(t) for t in _HEAD_STAMP.findall(_digest(g.get("text", "")))]
    out = {}
    for secs, f in zip(heads, g.get("frames") or []):
        if f and f.get("state") == "found":
            out[secs] = f["secs"]
    return out


SUPPORT_SYSTEM = """You check the citations in a research brief.
Each numbered item has a CLAIM (one takeaway from the brief) and a QUOTE (what one video says
at the moment the claim cites, taken from its transcript). For each item, decide whether the
quote BACKS the claim: it has to talk about the claim's own subject and state at least one of
its facts or arguments. A quote from the same video about a different aspect (speed instead of
power, setup instead of cost, a demo instead of a comparison) does NOT back it, and neither does
a quote that only shares a word with the claim.
Answer with JSON only, one entry per item, the reason first:
{"items": [{"n": 1, "why": "<under 10 words>", "backs": true or false}, ...]}"""


def _window(vid, secs, before=10, after=40):
    """A wider slice of the transcript than "what was said" shows, for checking a citation:
    a summary's timestamp marks where a point starts, and the point can take a minute."""
    d = ytgist.load_cached(vid) or {}
    near = [s["text"].strip() for s in d.get("sentences", []) if secs - before <= s["start"] <= secs + after]
    return " ".join(near)[:700]


def _check_support(takeaways):
    """Does each cited moment actually say what its takeaway claims?

    The citation check in _structure only proves the timestamp exists in that video's summary,
    not that it is about the takeaway: a brief once put "the 5090 draws 575-600 W" on a moment
    about inference time (Denis, 7 Oct). One model call reads every claim next to its quote;
    citations it rejects are removed, and a takeaway left with no supported moment goes, since
    nothing on screen backs it. Returns (citations removed, takeaways removed). A model that
    gives no usable answer changes nothing.

    Every removal is also returned with the model's reason, so the page can show what was cut
    and why, and a reader can disagree."""
    items = []
    for ti, t in enumerate(takeaways):
        for c in t["cites"]:
            if c["secs"] is not None:
                q = _window(c["id"], c["secs"])
                if q:
                    items.append((ti, c, q))
    if not items:
        return 0, 0, []
    user = "\n\n".join(f"{n}. CLAIM: {takeaways[ti]['headline']}. {takeaways[ti]['body']}\n"
                        f"   QUOTE (V{c['k']} {c['stamp']}): {q}" for n, (ti, c, q) in enumerate(items, 1))
    try:
        with (_run_lock or threading.Lock()):
            with model_client.Server.acquire(len(user) // 2 + 1200, log=lambda *_: None) as srv:
                raw = srv.chat(SUPPORT_SYSTEM, user, max_tokens=200 + 40 * len(items), temperature=0.0)
        m = re.search(r"\{.*\}", raw, re.S)
        verdicts = (json.loads(m.group(0)).get("items") if m else None) or []
        if not verdicts:
            raise ValueError("no verdicts")
        bad = {int(v["n"]) for v in verdicts if isinstance(v, dict) and v.get("backs") is False}
        reason = {int(v["n"]): str(v.get("why") or "")[:120] for v in verdicts if isinstance(v, dict)}
    except (model_client.ModelError, ValueError, TypeError) as e:
        ytgist.log(f"  research: couldn't check the citations ({e}); keeping them")
        return 0, 0, []
    gone = [(items[n - 1][0], items[n - 1][1]) for n in sorted(bad) if 1 <= n <= len(items)]
    removed = [{"headline": takeaways[ti]["headline"], "k": c["k"], "id": c["id"], "secs": c["secs"],
                "stamp": c["stamp"], "why": reason.get(n, "")}
               for n, (ti, c) in zip(sorted(n for n in bad if 1 <= n <= len(items)), gone)]
    for ti, c in gone:
        takeaways[ti]["cites"] = [x for x in takeaways[ti]["cites"] if x is not c]
    before = len(takeaways)
    checked = {ti for ti, _, _ in items}
    keep = [t for ti, t in enumerate(takeaways)
            if ti not in checked or any(c["secs"] is not None for c in t["cites"])]
    left = {t["headline"] for t in keep}
    for x in removed:
        x["takeaway_left_out"] = x["headline"] not in left
    takeaways[:] = keep
    return len(gone), before - len(keep), removed


def _structure(raw, sources, stamps, native=False):
    """The model's text → TL;DR, takeaways and extras, with every citation checked."""
    by_k = {s["k"]: s for s in sources}
    shots = {s["k"]: _shots_for(s, native) for s in sources}
    dropped = [0]

    def cites_in(text):
        out = []
        for m in _CITE.finditer(text):
            k, ts = int(m.group(1)), m.group(2)
            if k not in by_k:
                dropped[0] += 1
                continue
            secs = _secs(ts) if ts else None
            if secs is not None and secs not in stamps.get(k, set()):
                dropped[0] += 1             # a timestamp the summary doesn't have: keep the source only
                secs = None
            c = {"k": k, "id": by_k[k]["id"], "secs": secs, "stamp": _stamp(secs) if secs is not None else None}
            if c not in out:
                out.append(c)
        return out

    def link(m):
        k, ts = int(m.group(1)), m.group(2)
        s = by_k.get(k)
        if not s:
            return ""
        if ts and _secs(ts) in stamps.get(k, set()):
            return f"[V{k} {ts}](https://youtu.be/{s['id']}?t={_secs(ts)})"
        return f"[V{k}](https://youtu.be/{s['id']})"

    tldr, takeaways, extras, section = "", [], [], None
    lines = raw.replace("\r", "").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        if line.upper().startswith("TL;DR") or line.upper().startswith("TLDR"):
            tldr = _CITE.sub("", re.sub(r"^TL;?DR[:\s]*", "", line, flags=re.I)).strip()
            continue
        if line.startswith("## "):
            section = line[3:].strip()
            extras.append(line)
            continue
        if section is not None:
            extras.append(_CITE.sub(link, line))
            continue
        m = _STEP.match(line)
        if m:
            head, rest = m.group(1).strip(), m.group(2)
            body = []
            while i < len(lines) and lines[i].strip() and not _STEP.match(lines[i]) and not lines[i].startswith("## "):
                body.append(lines[i].strip())
                i += 1
            text = " ".join([rest] + body)
            takeaways.append({"headline": _CITE.sub("", head).strip(" ."),
                              "body": re.sub(r"\s{2,}", " ", _CITE.sub("", text)).strip(),
                              "cites": cites_in(head + " " + text)})
    unsupported, cut, removed = _check_support(takeaways)
    for t in takeaways:
        cites = t["cites"]
        said = [{"k": c["k"], "stamp": c["stamp"], "text": _said(c["id"], c["secs"])}
                for c in cites if c["secs"] is not None][:2]
        t["said"] = [s for s in said if s["text"]]
        t["shot"] = next(({"k": c["k"], "id": c["id"], "secs": shots[c["k"]][c["secs"]]}
                          for c in cites if c["secs"] is not None and c["secs"] in shots.get(c["k"], {})), None)
    extras_md = "\n".join(extras).strip()
    md = "\n\n".join([f"TL;DR {tldr}"] + [
        f"{n}. **{t['headline']}** " + " ".join(
            f"[V{c['k']} {c['stamp']}](https://youtu.be/{c['id']}?t={c['secs']})" if c["secs"] is not None
            else f"[V{c['k']}](https://youtu.be/{c['id']})" for c in t["cites"]) + f"\n{t['body']}"
        for n, t in enumerate(takeaways, 1)] + ([extras_md] if extras_md else []))
    return {"tldr": tldr, "takeaways": takeaways, "extras": extras_md, "markdown": md,
            "sources": sources, "at": time.time(), "unverified_dropped": dropped[0],
            "unsupported_dropped": unsupported, "takeaways_dropped": cut, "removed": removed}


def _write_markdown(r):
    """The same brief as a plain .md next to the JSON, readable without any app."""
    rep = r["report"]
    out = [f"# Research: {r['topic']}", "",
           time.strftime("%Y-%m-%d %H:%M", time.localtime(rep["at"])) + f" · {len(rep['sources'])} videos", ""]
    if rep.get("takeaways"):
        out.append(f"**TL;DR** {rep['tldr']}\n")
        for n, t in enumerate(rep["takeaways"], 1):
            cites = " ".join(f"[V{c['k']}{' ' + c['stamp'] if c['stamp'] else ''}](https://youtu.be/{c['id']}"
                             + (f"?t={c['secs']}" if c["secs"] is not None else "") + ")" for c in t["cites"])
            out += [f"## {n}. {t['headline']} {cites}", "", t["body"], ""]
            for s in t["said"]:
                out += [f"> V{s['k']} {s['stamp']}: {s['text']}…", ""]
        if rep.get("extras"):
            out += ["---", "", rep["extras"], ""]
    else:
        out += [rep["markdown"], ""]
    out += ["## Sources", ""]
    for s in rep["sources"]:
        out.append(f"- **V{s['k']}** [{s['title']}](https://youtu.be/{s['id']}) — "
                   f"{s['channel']} · {_fmt_dur(s['duration'])}" + (f" · {_age(s['ts'])}" if s.get("ts") else ""))
    with open(os.path.join(DIR, f"{r['id']}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


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
.steps{list-style:none;padding:0;margin:34px 0}.steps li{display:grid;grid-template-columns:44px 1fr;gap:6px;margin:0 0 34px}
.steps .n{font-weight:650;color:var(--mute);font-size:14px;padding-top:3px}.steps h3{margin:0;font-size:19px;line-height:1.3}
.steps p{margin:8px 0 0;font:18px/1.6 Georgia,"PT Serif",serif;color:var(--ink)}.chips{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px}
details summary{cursor:pointer;color:var(--mute);font-size:13px;margin-top:10px}blockquote{margin:8px 0 0;padding:10px 14px;border-left:2px solid var(--line);
 background:var(--card);font:15.5px/1.5 Georgia,"PT Serif",serif}.extra{border-top:1px solid var(--line);padding:4px 0 8px}
.extra summary{font:600 16px -apple-system,sans-serif;color:var(--ink);margin:12px 0}.extra ol{padding-left:22px}
.shot{display:block;max-width:100%;width:420px;border-radius:10px;margin-top:12px;border:1px solid var(--line)}
"""


def page(r):
    """The brief as one self-contained HTML page, laid out like a single video's gist.
    Escape first, then add markup: the text comes from strangers' transcripts."""
    import html as H
    live = r["status"] in ACTIVE
    out = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>{H.escape(r['topic'])}: research</title><style>{_PAGE_CSS}</style>"
           + ("<meta http-equiv=refresh content=5>" if live else "") + "<main>",
           f"<div class=k>YouTube research · {time.strftime('%d %b %Y', time.localtime(r['created']))}</div>",
           f"<h1>{H.escape(r['topic'])}</h1>"]
    if len(r.get("queries") or []) > 1:
        out.append("<div class=k style='text-transform:none;letter-spacing:0'>Searched YouTube for "
                   + " · ".join(H.escape(q) for q in r["queries"])
                   + (f" — {r['searched']} videos found" if r.get("searched") else "") + "</div>")
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

    def cite_links(md):
        md = H.escape(md)
        md = re.sub(r"\[(V\d+(?: \d{1,2}:\d{2}(?::\d{2})?)?)\]\((https://youtu\.be/[^)]+)\)",
                    r'<a class=cite href="\2" target=_blank rel=noopener>\1</a>', md)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", md)

    rep = r.get("report")
    if rep and rep.get("takeaways"):
        out.append(f"<div class=tldr>{H.escape(rep['tldr'])}</div><ol class=steps>")
        for n, t in enumerate(rep["takeaways"], 1):
            chips = "".join(
                f"<a class=cite href='https://youtu.be/{c['id']}" + (f"?t={c['secs']}" if c["secs"] is not None else "")
                + f"' target=_blank rel=noopener>V{c['k']}{(' ' + c['stamp']) if c['stamp'] else ''}</a>"
                for c in t["cites"])
            said = "".join(f"<blockquote><span class=k>V{s['k']} · {s['stamp']}</span> {H.escape(s['text'])}…</blockquote>"
                           for s in t["said"])
            sh = t.get("shot")
            pic = (f"<a href='/api/frame?v={sh['id']}&t={sh['secs']}' target=_blank rel=noopener title='full size'>"
                   f"<img class=shot src='/api/frame?v={sh['id']}&t={sh['secs']}' alt='V{sh['k']} at {_stamp(sh['secs'])}'></a>"
                   f"<a class=cite href='https://youtu.be/{sh['id']}?t={sh['secs']}' target=_blank rel=noopener>"
                   f"watch V{sh['k']} from {_stamp(sh['secs'])}</a>") if sh else ""
            out.append(f"<li><div class=n>{n}</div><div><h3>{H.escape(t['headline'])}</h3><p>{H.escape(t['body'])}</p>{pic}"
                       f"<div class=chips>{chips}</div>"
                       + (f"<details open><summary>what was said</summary>{said}</details>" if said else "")
                       + "</div></li>")
        out.append("</ol>")
        if rep.get("extras"):
            parts = re.split(r"(?m)^## ", rep["extras"])
            for part in parts:
                if not part.strip():
                    continue
                title, _, body = part.partition("\n")
                items = []
                for line in body.splitlines():
                    s = line.strip()
                    if not s:
                        continue
                    m = re.match(r"^\d+[.)]\s+(.*)", s)
                    items.append(f"<li>{cite_links(m.group(1) if m else s)}</li>")
                out.append(f"<details class=extra><summary>{H.escape(title.strip())}</summary><ol>{''.join(items)}</ol></details>")
        out.append("<h2>Sources</h2>")
    elif rep:                                    # a brief written before the takeaway layout
        blocks = []
        for line in rep["markdown"].splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("TL;DR"):
                blocks.append(f"<div class=tldr>{cite_links(s[5:].strip())}</div>")
            elif s.startswith("## "):
                blocks.append(f"<h2>{H.escape(s[3:])}</h2>")
            else:
                body = re.sub(r"^\d+[.)]\s+", "", s)
                blocks.append(f"<p>{cite_links(body)}</p>")
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
                   + (f" · {_age(v['ts'])}" if v.get("ts") else "")
                   + (f" · {H.escape(v.get('why') or '')}" if v.get("why") else "") + "</span></span>"
                   f"<span class='st{' ok' if st == 'succeeded' else ''}'>{H.escape(stl)}</span></div>")
    out.append("</div></main>")
    return "".join(out)
