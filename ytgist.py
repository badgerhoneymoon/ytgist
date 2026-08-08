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
import shutil
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gist_prompt
import model_client
import youtube_ingest as yt

CACHE = os.path.expanduser("~/.cache/ytgist")
TMP = os.path.join(CACHE, "tmp")
PARAKEET = "mlx-community/parakeet-tdt-0.6b-v3"     # multilingual: YouTube isn't English-only
CHUNK, OVERLAP = 120.0, 15.0
CACHE_V = 1

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
def run(url, question=None, model_key="dense", refresh=False):
    swept = yt.sweep(TMP)
    if swept:
        log(f"  swept {swept} leftover download dir(s) from an earlier killed run")

    vid = yt.video_id(url)
    cached = None if refresh else load_cached(vid)

    if cached:
        log(f"→ cached transcript for {vid} ({len(cached['sentences'])} segments)")
        meta, sentences = cached, cached["sentences"]
    else:
        log(f"→ checking {vid} …")
        info = yt.probe(url)
        mins = info["duration"] / 60
        log(f"   {gist_prompt.sanitize(info['title'])}  ({mins:.0f} min)")
        d = yt.temp_dir(TMP)
        try:
            log("→ downloading audio only …")
            wav = yt.fetch_audio(url, d)
            size = os.path.getsize(wav) / 1e6
            log(f"→ transcribing {size:.0f} MB of audio …")
            t0 = time.time()
            sentences = transcribe(wav)
            el = time.time() - t0
            log(f"   {len(sentences)} segments in {el:.0f}s "
                f"({info['duration'] / max(el, 0.1):.0f}x realtime)")
        finally:
            shutil.rmtree(d, ignore_errors=True)   # the audio never outlives the run
        meta = {"v": CACHE_V, "model": PARAKEET, "chunk": CHUNK, "overlap": OVERLAP,
                "parakeet": _parakeet_version(), "title": info["title"],
                "duration": info["duration"], "sentences": sentences}
        save_cached(vid, meta)

    if not sentences:
        log("✗ no speech found in that video.")
        return 1

    transcript = gist_prompt.format_transcript(sentences)
    user = (gist_prompt.ASK_USER.format(question=question, transcript=transcript)
            if question else gist_prompt.GIST_USER.format(transcript=transcript))

    log("→ summarising …")
    with model_client.Server.acquire(model=MODELS[model_key], log=log) as srv:
        need = srv.count_tokens(gist_prompt.SYSTEM + user) + 1400
        if need > srv.ctx:
            log(f"   transcript needs ~{need} tokens, server holds {srv.ctx} — "
                f"summarising in halves")
            half = len(sentences) // 2
            parts = []
            for chunk in (sentences[:half], sentences[half:]):
                pu = gist_prompt.GIST_USER.format(
                    transcript=gist_prompt.format_transcript(chunk))
                parts.append(srv.chat(gist_prompt.SYSTEM, pu))
            out = srv.chat(gist_prompt.SYSTEM,
                           gist_prompt.GIST_USER.format(transcript="\n".join(parts)))
        else:
            out = srv.chat(gist_prompt.SYSTEM, user)

    text, dropped = gist_prompt.verify(out, sentences, vid)
    print()
    print(gist_prompt.sanitize(meta.get("title", "")))
    print()
    print(gist_prompt.sanitize(text))
    if dropped:
        log(f"\n  ({dropped} invented timestamp(s) removed — the text was kept)")
    return 0


def main():
    ap = argparse.ArgumentParser(description="YouTube → gist with timestamps")
    ap.add_argument("url")
    ap.add_argument("--ask", help="ask a question about the video instead of a gist")
    ap.add_argument("--model", choices=sorted(MODELS), default="dense")
    ap.add_argument("--refresh", action="store_true", help="ignore any cached transcript")
    a = ap.parse_args()

    # SIGINT/SIGTERM must run the finally blocks. Without this the temp dir survives an
    # ordinary Ctrl-C; with it, only SIGKILL leaves anything (which sweep() then clears).
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: sys.exit(130))
    try:
        return run(a.url, a.ask, a.model, a.refresh)
    except yt.IngestError as e:
        log(f"✗ {e}")
        return 2
    except model_client.ModelError as e:
        log(f"✗ model: {e}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
