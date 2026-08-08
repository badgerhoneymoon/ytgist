#!/usr/bin/env python3
"""ytgist — paste a YouTube URL, get a gist with timestamps you can click.

    ytgist "https://youtu.be/…"
    ytgist "…" --ask "what did they say about pricing?"
    ytgist "…" --model coder          # A/B the 27B dense against Coder-Next

PIPELINE: yt-dlp (audio only) → Parakeet v3 (chunked, timestamped) → Qwen 27B.
No cleanup model: the reader is a 27B that handles "um" fine, and rewriting sentences
would break their alignment with the timestamps (Denis, 2026-08-08).

WHY IT DOESN'T IMPORT MYNA. An earlier plan called dictate._pk_load(). That drags in
Myna's whole module — audio devices, hotkey monitors, the engine client — and depends on
a private function (Codex review r2). We load the same weights from the same HF cache
through parakeet-mlx's PUBLIC from_pretrained. Same model, no coupling, and Myna's
.engine preference is never touched.
"""
import argparse
import json
import os
import re
import glob
import shutil
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gist_prompt
import model_client
import timing_log
import youtube_ingest as yt
from youtube_ingest import IngestError

CACHE = os.path.expanduser("~/.cache/ytgist")
TMP = os.path.join(CACHE, "tmp")
PARAKEET = "mlx-community/parakeet-tdt-0.6b-v3"     # multilingual: YouTube isn't English-only
CHUNK, OVERLAP = 120.0, 15.0
CACHE_V = 1
# MEASURED per-minute-of-video costs, from real runs on this machine (M4 Max, 27B Q5):
#   download    17.2s / 57 min = 0.30
#   transcribe  35s   / 57 min = 0.61   (matches the observed 99x realtime)
#   summarise   41.6s / 11 min, 53.2s / 12 min, 182.7s / 57 min → ~4.0 s per minute
# Model load is flat-ish but grows with context, so it is a floor plus a small slope.
#
# These are ESTIMATES SHOWN TO A HUMAN, so they are tuned to run slightly long. An ETA
# that expires while you are still waiting is worse than one you beat.
RATE = {"download": 0.30, "transcribe": 0.65, "summarise": 4.2}

# THE HARD LIMIT. Beyond the largest context we will start, the only way to proceed is to
# summarise the transcript in halves and merge — each half written blind to the other, so
# a thread running from hour one to hour three simply disappears, with nothing in the
# output admitting it. Denis chose refusal over a quiet downgrade (2026-08-08): if a video
# this long ever matters, it deserves a real design (rolling summary, map-reduce with a
# shared outline), not a silent fallback. Checked BEFORE the download, so nothing is spent.
CHARS_PER_SEC = 18.6        # formatted transcript, measured on real Russian speech
CHARS_PER_TOKEN = 2.0       # Cyrillic; Latin is cheaper, so this errs toward refusing late
LOAD_BASE, LOAD_PER_MIN = 4.0, 0.14


def max_minutes() -> float:
    """Longest video that fits the biggest context we will start, in one pass."""
    room = model_client.CTX_MAX - 2048 - 1400          # answer + prompt overhead
    return room * CHARS_PER_TOKEN / CHARS_PER_SEC / 60


def _refuse_if_too_long(minutes: float):
    cap = max_minutes()
    if minutes > cap:
        raise TooLong(
            f"That video is {minutes/60:.1f} hours long. ytgist summarises up to "
            f"{cap/60:.1f} hours in one pass, and refuses beyond that rather than "
            f"splitting the transcript and quietly losing the thread between halves."
        )


def estimate(minutes: float, cached: bool) -> dict:
    """Seconds per remaining phase for a video of this length.

    Built-in rates are the FLOOR, not the answer: whatever the timing log has measured on
    this machine, at this power mode, wins. Each finished run makes this sharper."""
    ctx = model_client.ctx_for(int(minutes * 60 * CHARS_PER_SEC / CHARS_PER_TOKEN) + 1400)
    known = timing_log.learned(timing_log.power_mode(), ctx,
                               warm=model_client.warm_available())

    def cost(phase):
        """Learned affine cost if we have one, else the hand-fitted per-minute constant."""
        fit = known.get(phase)
        if isinstance(fit, tuple):
            return fit[0] + fit[1] * minutes
        return RATE[phase] * minutes

    load = known.get("model load", LOAD_BASE + LOAD_PER_MIN * minutes)
    if isinstance(load, tuple):
        load = load[0] + load[1] * minutes

    if cached:
        return {"model load": load, "summarise": cost("summarise")}
    return {"read info": 2.0,
            "download": cost("download"),
            "transcribe": cost("transcribe"),
            "model load": load,
            "summarise": cost("summarise")}


EXPAND_MAX = 8 * 60         # a step's span, capped — beyond this it stops being one point
# And a FLOOR. Takeaways cluster at the start of a video, so step 1 of one talk spanned
# 00:09 → 00:32 — 23 seconds, 404 characters — and the model quite reasonably reported
# "nothing further" while the passage plainly contained the thousand-IQ-versus-million-IQ
# point and the "is it already smarter and hiding it" exchange (Denis spotted it,
# 2026-08-08). Overlapping into the next step is a far smaller cost than having nothing to
# read: the summary of that next step is not shown to this call anyway.
EXPAND_MIN = 60


def save_expansion(vid, native, start, text):
    """Keep an expansion with the summary it belongs to.

    They lived only in the browser's memory, so a reload threw away work that cost ten
    seconds of GPU each (Denis, 2026-08-08). Keyed by the step's start second, and stored
    per LANGUAGE VARIANT — an English expansion does not belong to a Russian summary."""
    saved = load_summary(vid, native)
    if not saved:
        return
    saved["exp_v"] = EXP_V
    saved.setdefault("expansions", {})[str(int(start))] = text
    save_summary(vid, saved, native)


def expand(vid, start, end, headline, body, native=False, log=print):
    """More detail about ONE takeaway, from that takeaway's own span of the transcript.

    Cheaper and safer than a free question: the window is fixed by the argument's own
    structure, so there is nothing else in context to invent from. Returns "" when the
    passage genuinely adds nothing, which the prompt is explicitly allowed to say."""
    cached = load_cached(vid)
    if not cached:
        raise IngestError("missing", "That transcript is no longer cached.")
    sentences = cached["sentences"]
    end = min(max(end if end and end > start else start + 240, start + EXPAND_MIN),
              start + EXPAND_MAX)
    window = [x for x in sentences if start - 5 <= x["start"] <= end]
    if not window:
        return ""

    user = gist_prompt.EXPAND_USER.format(
        headline=headline.strip(), body=body.strip(),
        language=("Every word in the same language as the passage — do not translate."
                  if native else "Write in English."),
        transcript=gist_prompt.format_transcript(window))

    system = gist_prompt.expand_system_for(native)
    need = len(system + user) // 2 + 600
    t0 = time.time()
    srv = model_client.Server.acquire(need_tokens=need, model=MODELS["dense"], log=log)
    warm = getattr(srv, "was_warm", False)
    with srv:
        out = srv.chat(system, user, max_tokens=600, temperature=0.25)
    timing_log.record_expand((end - start) / 60, len(user), warm, time.time() - t0, native)
    if "NOTHING FURTHER" in out.upper():
        save_expansion(vid, native, start, "")   # "asked, and there is nothing" is an answer
        return ""
    text, dropped = gist_prompt.verify(_destaff(out.strip()), sentences, vid)
    if dropped:
        log(f"  ({dropped} invented timestamp(s) removed from the expansion)")
    save_expansion(vid, native, start, text)
    return text


_SCAFFOLD = re.compile(
    r"\b(?:the|our|this)\s+speaker\s+"
    r"(?:notes?|claims?|says?|states?|explains?|outlines?|suggests?|argues?|points? out|"
    r"observes?|adds?|mentions?|describes?|admits?|confirms?|invites?|asks?)\s+(?:that\s+)?",
    re.I)
_SCAFFOLD2 = re.compile(r"\b(?:the answer provided is|it is (?:worth )?noted) that\s+", re.I)


def _destaff(text: str) -> str:
    """Strip reported-speech scaffolding the model slipped past the prompt.

    "The speaker notes that X" → "X". The whole page is already about what was said, so
    naming that fact in every sentence is three words of nothing (Denis, 2026-08-08)."""
    out = _SCAFFOLD2.sub("", _SCAFFOLD.sub("", text))
    # Re-capitalise wherever a sentence now begins mid-clause.
    out = re.sub(r"(^|(?<=[.!?]\s))([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    return out


class Cancelled(Exception):
    """The user pressed Stop. Not an error — nothing is wrong, the work is simply over."""


class TooLong(Exception):
    """Longer than one context can hold. Refused deliberately — see the note above."""


GIST_V = 2          # bump when the PROMPT changes, so old summaries are re-made
EXP_V = 2           # bump when the EXPAND PROMPT changes; older expansions are dropped
                    # rather than shown — Denis reloaded and
                    # still saw "the speaker, the speaker, the speaker" because those were
                    # written before the rule existed (2026-08-08)

MODELS = {
    "dense": os.path.expanduser("~/models/Qwen3.6-27B-UD-Q5_K_XL.gguf"),
    "coder": os.path.expanduser(
        "~/.cache/huggingface/hub/models--unsloth--Qwen3-Coder-Next-GGUF/blobs/"
        "abf56d7fe8a0a99c15d220c13de4aa57b69cfba6ef4c2a007b56e34d7b40cd11"),
}


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------------- transcript
def _cache_path(vid):
    return os.path.join(CACHE, f"{vid}.json")


def load_cached(vid):
    """A cache entry is only usable if it was made the SAME WAY. The key covers model,
    chunking AND parakeet-mlx's version: change any of them and the segmentation moves,
    which silently invalidates every timestamp (Codex r2)."""
    try:
        with open(_cache_path(vid)) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if (d.get("v") != CACHE_V or d.get("model") != PARAKEET
            or d.get("chunk") != CHUNK or d.get("overlap") != OVERLAP
            or d.get("parakeet") != _parakeet_version()):
        return None
    return d


def _parakeet_version():
    try:
        from importlib.metadata import version
        return version("parakeet-mlx")
    except Exception:
        return "?"


def save_cached(vid, payload):
    """Atomic: a half-written cache read by the next run is worse than no cache."""
    os.makedirs(CACHE, exist_ok=True)
    tmp = _cache_path(vid) + f".{os.getpid()}.part"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, _cache_path(vid))


def gist_path(vid, native=False):
    """One file PER LANGUAGE VARIANT. An English summary and an original-language one are
    different artefacts for the same video, and keeping both means flipping the toggle
    never destroys the other — and never silently pays 40s to remake something already
    on disk (2026-08-08)."""
    return os.path.join(CACHE, f"{vid}.gist.native.json" if native else f"{vid}.gist.json")


def load_summary(vid, native=False):
    """A saved summary, if it was made by the CURRENT prompt. Kept in its own file so a
    transcript and its summary can be invalidated independently — the transcript is
    expensive and rarely stale; the summary is cheap and changes whenever the prompt does."""
    try:
        with open(gist_path(vid, native), encoding="utf-8") as f:
            d = json.load(f)
        if d.get("gist_v") != GIST_V:
            return None
        if d.get("exp_v") != EXP_V:
            d["expansions"] = {}
        return d
    except (OSError, ValueError):
        return None


def save_summary(vid, payload, native=False):
    os.makedirs(CACHE, exist_ok=True)
    tmp = gist_path(vid, native) + f".{os.getpid()}.part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, gist_path(vid, native))


# The 25 commonest English function words. Script alone cannot answer this — Spanish and
# French are Latin too — but function words are the highest-frequency, least topic-
# dependent signal there is, and a transcript is long enough to make the ratio stable.
_EN_STOPWORDS = frozenset(
    "the of and to a in that is it you for on was with as this but be at have or not "
    "they we so what".split()
)


def is_english(meta) -> bool:
    """Is the SPOKEN language English?

    Answers from yt-dlp's own tag when the video carries one, and falls back to the
    transcript otherwise — which is what every transcript cached before this existed has.
    Used to hide the "Original" chip on an English video, where making an
    original-language summary would just produce a second English one."""
    tag = (meta.get("language") or "").lower()
    if tag:
        return tag.split("-")[0] == "en"
    text = " ".join(s.get("text", "") for s in (meta.get("sentences") or [])[:120]).lower()
    words = [w.strip(".,!?;:—\"'\u00ab\u00bb") for w in text.split()]
    words = [w for w in words if w]
    if len(words) < 25:
        return False                      # too little to judge; keep the option visible
    hits = sum(1 for w in words if w in _EN_STOPWORDS)
    return hits / len(words) > 0.18       # English transcripts land ~0.30, Russian ~0.00


def history():
    """Every video we have, newest first — transcript always, summary when we made one."""
    out = []
    for f in glob.glob(os.path.join(CACHE, "*.json")):
        if ".gist." in os.path.basename(f):
            continue
        vid = os.path.basename(f)[:-5]
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        # BOTH variants, reported separately: the library is where you choose which
        # language to open, so it has to know which ones actually exist.
        en = load_summary(vid, native=False)
        ru = load_summary(vid, native=True)
        g = en or ru
        out.append({"id": vid, "title": d.get("title", vid),
                    "duration": d.get("duration", 0),
                    "segments": len(d.get("sentences", [])),
                    "has_summary": bool(g),
                    "has_en": bool(en), "has_native": bool(ru),
                    "is_english": is_english(d),
                    "at": (g or {}).get("at") or os.path.getmtime(f)})
    out.sort(key=lambda r: r["at"], reverse=True)
    return out


def transcribe(wav_path):
    """Chunked, so memory stays bounded and throughput stays constant.

    MEASURED on this M4 Max: one-shot 10min = 44x realtime and +8.4GB; chunked 30min =
    105x with flat MODEL memory. (Audio memory still grows with duration — parakeet's
    loader holds the whole PCM — but a 60-minute 16kHz mono float32 buffer is ~230MB,
    which is fine and worth stating rather than claiming 'flat' outright.)"""
    from parakeet_mlx import from_pretrained
    from parakeet_mlx.parakeet import DecodingConfig, SentenceConfig
    m = from_pretrained(PARAKEET)
    # max_duration caps a segment: parakeet splits sentences on PUNCTUATION, and
    # punctuation-poor speech can otherwise become one enormous block with a single
    # timestamp — useless for "5-10 ideas with timestamps" (Codex r2).
    cfg = DecodingConfig(sentence=SentenceConfig(max_duration=30.0))
    res = m.transcribe(wav_path, chunk_duration=CHUNK, overlap_duration=OVERLAP,
                       decoding_config=cfg)
    return [{"start": float(s.start), "end": float(s.end), "text": s.text}
            for s in res.sentences]


# ------------------------------------------------------------------------ main
def run(url, model_key="dense", refresh=False, progress=None,
        native=False, regen=False, control=None):
    timings = {}                 # phase → seconds; the UI draws these as a stacked bar
    predicted = {}               # what we TOLD the user it would take, kept for scoring

    class phase:
        """Times a phase. Every stage of this pipeline has a wildly different cost
        (model load 2s, download 8s, summary 50s) and until you see them side by side
        you optimise the wrong one (Denis, 2026-08-08)."""
        def __init__(self, name):
            self.name = name
        def __enter__(self):
            self.t = time.time(); return self
        def __exit__(self, *e):
            timings[self.name] = round(time.time() - self.t, 1); return False

    def step(stage, pct, msg=""):
        """One place that reports where we are — the CLI prints it, the web UI streams it.

        Also the CANCELLATION CHECKPOINT. Every stage passes through here, so one test
        covers the whole pipeline without threading a flag through each phase."""
        if control is not None and control.cancelled.is_set():
            raise Cancelled()
        if progress:
            progress({"stage": stage, "pct": pct, "msg": msg})

    model_client.sweep_orphans(log)      # a 20GB leftover halves the GPU for this run
    swept = yt.sweep(TMP)
    if swept:
        log(f"  swept {swept} leftover download dir(s) from an earlier killed run")

    step("parse", 2, "the link")
    vid = yt.video_id(url)
    cached = None if refresh else load_cached(vid)

    if cached:
        log(f"→ cached transcript for {vid} ({len(cached['sentences'])} segments)")
        step("cached", 70, f"cached transcript, {len(cached['sentences'])} segments")
        _refuse_if_too_long(cached.get("duration", 0) / 60)
        predicted = estimate(cached.get("duration", 0) / 60, True)
        if progress:
            progress({"eta": predicted,
                      "video_minutes": round(cached.get("duration", 0) / 60)})
        timings["_cached"] = True     # so the UI can EXPLAIN the missing phases
        meta, sentences = cached, cached["sentences"]
    else:
        log(f"→ checking {vid} …")
        step("probe", 5, "title, length and type")
        with phase("read info"):
            info = yt.probe(url)
        mins = info["duration"] / 60
        log(f"   {gist_prompt.sanitize(info['title'])}  ({mins:.0f} min)")
        _refuse_if_too_long(mins)
        predicted = estimate(mins, False)
        if progress:
            progress({"eta": predicted, "video_minutes": round(mins)})
        d = yt.temp_dir(TMP)
        try:
            log("→ downloading audio only …")
            step("download", 15, f"{info['duration']/60:.0f} min of audio")
            with phase("download"):
                wav = yt.fetch_audio(url, d)
            size = os.path.getsize(wav) / 1e6
            log(f"→ transcribing {size:.0f} MB of audio …")
            step("transcribe", 40, f"{size:.0f} MB on the GPU")
            t0 = time.time()
            with phase("transcribe"):
                sentences = transcribe(wav)
            el = time.time() - t0
            log(f"   {len(sentences)} segments in {el:.0f}s "
                f"({info['duration'] / max(el, 0.1):.0f}x realtime)")
        finally:
            shutil.rmtree(d, ignore_errors=True)   # the audio never outlives the run
        meta = {"v": CACHE_V, "model": PARAKEET, "chunk": CHUNK, "overlap": OVERLAP,
                "parakeet": _parakeet_version(), "title": info["title"],
                "language": info.get("language"),
                "duration": info["duration"], "sentences": sentences}
        save_cached(vid, meta)

    if not sentences:
        log("✗ no speech found in that video.")
        return 1

    saved = None if (refresh or regen) else load_summary(vid, native)
    if saved:
        step("done", 100, "")
        log("  using the saved summary (regenerate to make a new one)")
        run.last = {"title": meta.get("title", ""), "markdown": saved["text"],
                    "raw": saved["text"], "dropped": 0, "video_id": vid,
                    "timings": {}, "duration": meta.get("duration", 0),
                    "cached": True, "sentences": sentences, "from_saved": True,
                    "expansions": saved.get("expansions") or {}}
        return 0

    transcript = gist_prompt.format_transcript(sentences)
    user = gist_prompt.GIST_USER.format(
        transcript=transcript,
        steps=gist_prompt.steps_for(meta.get("duration", 0) / 60))
    user += gist_prompt.NATIVE_RULE if native else gist_prompt.ENGLISH_RULE

    step("summarise", 75, "27B, one pass over the whole transcript")
    log("→ summarising …")
    # Estimated BEFORE the server exists, because the server's own tokeniser is what we
    # would otherwise need to size it. Two chars per token is the Cyrillic rate measured
    # on a real transcript; it over-estimates Latin text, and over-estimating only costs a
    # little unused context, where under-estimating costs a whole halved summary.
    srv_ctx, srv_warm = 0, False
    est = len(gist_prompt.system_for(native) + user) // 2 + 1400
    with phase("model load"):
        _srv = model_client.Server.acquire(need_tokens=est, model=MODELS[model_key], log=log)
    # Handing the server to the controller is what makes STOP work during generation.
    # Between step() checkpoints the process sits inside one blocking HTTP call for
    # minutes; there is nothing to poll. Stopping our own llama-server breaks that call,
    # which is the only way to interrupt it that does not require the model to cooperate.
    if control is not None:
        control.server = _srv
    srv_ctx, srv_warm = _srv.ctx, getattr(_srv, "was_warm", False)
    with _srv as srv:
        # The length gate above uses an ESTIMATE; this is the server's own tokeniser
        # having the last word. It should never fire — the estimate deliberately runs
        # high — but if it does, refusing is the honest outcome. There is no longer a
        # halving path to fall back to, by design.
        need = srv.count_tokens(gist_prompt.system_for(native) + user) + 1400
        if need > srv.ctx:
            raise TooLong(
                f"That transcript needs ~{need:,} tokens and the largest context ytgist "
                f"will start holds {srv.ctx:,}. Refusing rather than summarising it in "
                f"halves, which loses whatever connects the two."
            )
        with phase("summarise"):
            try:
                out = srv.chat(gist_prompt.system_for(native), user)
            except Exception:
                if control is not None and control.cancelled.is_set():
                    raise Cancelled()
                raise

    step("done", 100, "")

    was_cached = bool(timings.pop("_cached", False))   # popped BEFORE any consumer
    text, dropped = gist_prompt.verify(out, sentences, vid)
    print()
    print(gist_prompt.sanitize(meta.get("title", "")))
    print()
    print(gist_prompt.sanitize(text))
    if dropped:
        log(f"\n  ({dropped} invented timestamp(s) removed — the text was kept)")
    save_summary(vid, native=native, payload={
                           "gist_v": GIST_V, "text": text, "at": time.time(),
                           "title": meta.get("title", ""),
                           "exp_v": EXP_V, "native": bool(native),
                           "duration": meta.get("duration", 0)})
    run.last = {"title": meta.get("title", ""), "markdown": text, "dropped": dropped,
                "video_id": vid, "timings": timings,
                "duration": meta.get("duration", 0), "cached": was_cached,
                "sentences": sentences,   # the UI shows the evidence behind each claim
                "expansions": {}}
    timing_log.record(meta.get("duration", 0) / 60, was_cached, srv_ctx, timings, native,
                      predicted=predicted, warm=srv_warm)
    total = sum(timings.values())
    log("\n  " + " · ".join(f"{k} {v:g}s" for k, v in timings.items())
        + f"  =  {total:.0f}s total")
    return 0


def main():
    ap = argparse.ArgumentParser(description="YouTube → gist with timestamps")
    ap.add_argument("url")
    ap.add_argument("--model", choices=sorted(MODELS), default="dense")
    ap.add_argument("--refresh", action="store_true", help="ignore any cached transcript")
    ap.add_argument("--regen", action="store_true",
                    help="make a new summary from the transcript already cached")
    ap.add_argument("--native", action="store_true",
                    help="write the takeaways in the video's own language")
    a = ap.parse_args()

    # SIGINT/SIGTERM must run the finally blocks. Without this the temp dir survives an
    # ordinary Ctrl-C; with it, only SIGKILL leaves anything (which sweep() then clears).
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: sys.exit(130))
    try:
        return run(a.url, a.model, a.refresh, native=a.native, regen=a.regen)
    except yt.IngestError as e:
        log(f"✗ {e}")
        return 2
    except model_client.ModelError as e:
        log(f"✗ model: {e}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
