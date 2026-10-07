#!/usr/bin/env python3
"""YouTube → a 16 kHz mono WAV on disk. The ONLY module that knows yt-dlp exists.

Swap this file and ytgist works on any source; nothing above it mentions YouTube's
quirks. That isolation is the brief's one architectural requirement.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

# Strict host allowlist. A lookalike domain must not reach yt-dlp at all — parsing an
# id out of "youtube.com.evil.tld/watch?v=…" and handing it over is how a URL parser
# becomes an SSRF.
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
          "youtu.be", "www.youtu.be", "youtube-nocookie.com",
          "www.youtube-nocookie.com"}
_ID = r"[A-Za-z0-9_-]{11}"


class IngestError(Exception):
    """Carries a machine-readable `kind` so the caller can say something useful."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def video_id(url: str) -> str:
    """The 11-character id, or IngestError('bad_url'). Rejects anything not YouTube."""
    from urllib.parse import parse_qs, urlparse
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https"):
        raise IngestError("bad_url", f"not an http(s) URL: {url!r}")
    if (u.hostname or "").lower() not in _HOSTS:
        raise IngestError("bad_url", f"not a YouTube URL: {u.hostname!r}")
    if (u.hostname or "").lower().endswith("youtu.be"):
        cand = u.path.lstrip("/").split("/")[0]
    elif u.path.startswith(("/shorts/", "/embed/", "/live/", "/v/")):
        cand = u.path.split("/")[2] if len(u.path.split("/")) > 2 else ""
    else:
        cand = (parse_qs(u.query).get("v") or [""])[0]
    if not re.fullmatch(_ID, cand):
        raise IngestError("bad_url", f"no video id in {url!r}")
    return cand


def _classify(stderr: str) -> tuple:
    """yt-dlp's stderr → (kind, human sentence). Substring matching is brittle across
    yt-dlp versions, so `unknown` is a FIRST-CLASS outcome that quotes the real error:
    a taxonomy pretending to be exhaustive just mislabels the case it never saw."""
    s = stderr.lower()
    table = [
        ("private", "private video", "That video is private."),
        ("removed", "video unavailable", "That video is unavailable or has been removed."),
        ("removed", "has been removed", "That video has been removed."),
        ("age", "age-restricted", "That video is age-restricted and needs a signed-in account."),
        ("age", "confirm your age", "That video is age-restricted and needs a signed-in account."),
        ("geo", "not available in your country", "That video is blocked in this country."),
        ("geo", "geo restricted", "That video is blocked in this country."),
        ("members", "members-only", "That video is members-only."),
        ("members", "join this channel", "That video is members-only."),
        # 403 on the MEDIA url is not a network problem — it is YouTube refusing this
        # yt-dlp. It cost a debugging round because "unable to download" matched the
        # network rule first and buried the real cause (2026-08-08: the installed
        # yt-dlp was 10 months old and could not handle YouTube's SABR streaming).
        # Specific rules must come BEFORE generic ones.
        # 403 is USUALLY THE MISSING JS RUNTIME, not a stale yt-dlp. YouTube's player
        # challenges need JavaScript to solve, and without a runtime yt-dlp quietly falls
        # back to formats that can 403. The old message here blamed the version and sent
        # Denis to `brew upgrade yt-dlp`, which reported "already installed" and left him
        # no better off (2026-08-10).
        # BOTH SPELLINGS. yt-dlp writes "HTTP Error 403: Forbidden"; ffmpeg, which handles
        # some fetches, writes "HTTP error 403 Forbidden" with no colon — and matching only
        # the first meant the real-world failure classified as "unknown" and so was never
        # retried (2026-08-10).
        ("blocked", "403", "YouTube refused the download (HTTP 403). Usually a "
                                      "missing JavaScript runtime — install one with: "
                                      "brew install deno"),
        ("blocked", "sabr", "YouTube is forcing SABR streaming for this video and your "
                            "yt-dlp can't handle it — try: brew upgrade yt-dlp"),
        ("network", "temporary failure", "Network problem reaching YouTube."),
        ("network", "unable to download", "Couldn't download the audio — see the yt-dlp "
                                          "error above."),
    ]
    for kind, needle, msg in table:
        if needle in s:
            return kind, msg
    last = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    return "unknown", f"yt-dlp failed: {last[-1] if last else 'no output'}"


def _run(cmd, timeout):
    """Run yt-dlp in its OWN PROCESS GROUP so we can kill the whole tree.

    yt-dlp spawns ffmpeg. Killing only the python parent leaves ffmpeg writing to the
    WAV we are about to delete — cleanup then races a live writer and can leave a file
    behind (Codex review r2). start_new_session puts them in one group; killpg ends all
    of it."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            p.kill()
        p.communicate()
        raise IngestError("network", f"yt-dlp timed out after {timeout}s")


def js_runtime_ok() -> bool:
    """Can yt-dlp solve YouTube's player challenges?

    Without a JavaScript runtime it still runs, warns, and falls back to formats that 403
    partway through — a failure that looks like a network problem an hour later. Checking
    for the runtime turns that into something sayable up front."""
    import shutil
    for exe in ("deno", "node", "bun", "quickjs"):
        if shutil.which(exe):
            return True
    return False


def probe(url: str) -> dict:
    """Metadata only — no media. Refuses live/upcoming BEFORE anything is downloaded."""
    if not shutil.which("yt-dlp"):
        raise IngestError("missing_tool", "yt-dlp is not installed (brew install yt-dlp)")
    rc, out, err = _run(["yt-dlp", "--ignore-config", "--no-playlist", "--skip-download",
                         "--dump-single-json", "--", url], timeout=60)
    if rc != 0:
        kind, msg = _classify(err)
        raise IngestError(kind, msg)
    try:
        meta = json.loads(out)
    except ValueError:
        raise IngestError("unknown", "yt-dlp returned metadata that isn't JSON")
    # live_status covers what a bare is_live check misses: an UPCOMING premiere flips to
    # live between our probe and our download, and yt-dlp does not error on a live
    # stream — it records until the stream ends, which for us is unbounded (Codex r2).
    status = meta.get("live_status") or ("is_live" if meta.get("is_live") else "not_live")
    if status in ("is_live", "is_upcoming", "post_live"):
        raise IngestError("live", f"That video is {status.replace('_', ' ')} — "
                                  "ytgist only handles finished videos.")
    return {"id": meta.get("id"), "title": meta.get("title") or "(untitled)",
            "duration": float(meta.get("duration") or 0),
            "language": meta.get("language"), "live_status": status}


# Tried in order. None means yt-dlp's own default. Measured against a video YouTube was
# actively refusing: default resolved to ANDROID_VR and 403'd, web_embedded served it.
# `web`, `tv`, `android` and `ios` all answered "not available" — they need PO tokens now,
# so listing them would only add dead attempts.
_CLIENTS = (None, "web_embedded", "default")


def fetch_audio(url: str, dest_dir: str, timeout: int = 3600, on_retry=None) -> str:
    """Download AUDIO ONLY as 16 kHz mono WAV. Returns the path.

    -f bestaudio is LOAD-BEARING: `-x` alone still downloads yt-dlp's default
    best-video+audio and strips the audio afterwards, so an hour-long 1080p video would
    cross the network for a transcript (Codex review r1). The brief said audio only.

    --match-filter is a SECOND live check at download time, closing the race where an
    upcoming premiere goes live between probe() and here.

    IT RETRIES, because YouTube refuses transiently. A 403 on the media URL killed an
    entire run and the same video downloaded on the next click, minutes later, unchanged
    (Denis, 2026-08-10). Every other fragile thing here already recovers by itself — the
    run lock queues, the warm pool reaps, a dropped stream rejoins, SIGTERM waits for the
    job — while the one step that talks to a third party over HTTP had no retry at all.

    Each attempt re-runs yt-dlp from scratch AND ASKS A DIFFERENT PLAYER CLIENT. Media URLs
    are short-lived, IP-bound and signed per client, so retrying the same one is knocking on
    the same door twice — and measurement showed that door was the problem: yt-dlp's default
    picks ANDROID_VR, whose URLs were being refused while WEB_EMBEDDED_PLAYER served the
    same audio without complaint (2026-08-10)."""
    err = ""
    for attempt, client in enumerate(_CLIENTS):
        rc, _out, err = _run([
            "yt-dlp", "--ignore-config", "--no-playlist",
            # bestaudio FIRST and audio-only if at all possible — `-x` alone would fetch
            # video and throw it away, which for an hour-long 1080p talk is the whole point
            # of the tool undone (Codex r1). But a fallback client offers a different format
            # set, and web_embedded has no audio-only stream at all, so refusing to fall
            # back to a combined one means the fallback cannot work (2026-08-10).
            "-f", "bestaudio/bestaudio*/best",
            *(["--extractor-args", f"youtube:player_client={client}"] if client else []),
            "--match-filter", "live_status != 'is_live' & live_status != 'is_upcoming'",
            "-x", "--audio-format", "wav",
            "--postprocessor-args", "ExtractAudio:-ar 16000 -ac 1",
            # yt-dlp's own retries, for failures inside a single extraction.
            "--retries", "5", "--fragment-retries", "5", "--extractor-retries", "3",
            "--no-progress", "-o", os.path.join(dest_dir, "%(id)s.%(ext)s"),
            "--", url], timeout=timeout)
        if rc == 0:
            break
        kind, msg = _classify(err)
        # Only what is plausibly transient. A private, deleted or age-gated video will
        # refuse identically forever, and retrying it just makes the user wait longer to
        # be told the same thing.
        if kind not in ("blocked", "network") or attempt == len(_CLIENTS) - 1:
            raise IngestError(kind, msg)
        for f in os.listdir(dest_dir):      # a half-written part would confuse the next try
            try:
                os.remove(os.path.join(dest_dir, f))
            except OSError:
                pass
        if on_retry:
            on_retry(attempt + 1, msg)
        # 3s, then 10s. The evidence points at rate limiting rather than a bad URL — the
        # same client answered "no such format" and then served it a minute later, and a
        # download that 403'd succeeded on the next attempt — so the pause has to be long
        # enough to matter, while staying short enough that a user does not give up.
        time.sleep(3 + 7 * attempt)
    wavs = [f for f in os.listdir(dest_dir) if f.endswith(".wav")]
    if not wavs:
        raise IngestError("unknown", "yt-dlp reported success but produced no audio "
                                     "(the video may have no audio track)")
    return os.path.join(dest_dir, wavs[0])


def storyboard(url: str) -> dict:
    """YouTube's own contact sheets: fragments, grid shape, and seconds per tile.

    One ~25 KB WebP per ~90 seconds of video, each a 3x3 grid of 320x180 tiles — the whole
    of an 80-minute video for 1.5 MB and a single listing call, which is why the picker
    looks here before downloading one frame of video.

    THE TIMING IS APPROXIMATE AND CALLERS MUST TREAT IT THAT WAY. The installed yt-dlp
    discards the storyboard spec's real interval and derives fragment duration from
    duration/frame_count (Codex r3), so a tile's second is a good guess, not a fact — which
    is why the frame grab verifies what it got against the tile it came from.
    """
    rc, out, err = _run(["yt-dlp", "--ignore-config", "--no-playlist", "--skip-download",
                         "--dump-single-json", "--", url], timeout=90)
    if rc != 0:
        kind, msg = _classify(err)
        raise IngestError(kind, msg)
    try:
        formats = json.loads(out).get("formats", [])
    except ValueError:
        raise IngestError("unknown", "yt-dlp returned metadata that isn't JSON")
    # The biggest sheet there is: sb0 gives 320x180 tiles, sb3 gives 48x27, and the picker
    # is already working at the edge of what can be made out.
    sheets = sorted((f for f in formats
                     if str(f.get("format_id", "")).startswith("sb") and f.get("fragments")),
                    key=lambda f: -(f.get("width") or 0))
    if not sheets:
        raise IngestError("no_storyboard", "This video has no storyboard thumbnails.")
    sb = sheets[0]
    frags = sb["fragments"]
    per = float(frags[0].get("duration") or 0) or 1.0
    cols, rows = int(sb.get("columns") or 1), int(sb.get("rows") or 1)
    return {"fragments": frags, "cols": cols, "rows": rows, "per_sheet": per,
            "per_tile": per / max(cols * rows, 1)}


def sheet_jpeg(frag_url: str, dest: str) -> str:
    """One storyboard sheet, as a JPEG on disk.

    sips, NOT ffmpeg. ffmpeg cannot decode YouTube's storyboard WebP: it fails, loops and
    writes a 1.5 GB "jpg" per input — measured, on a disk that had just been cleared
    (2026-10-02). The size check below is that afternoon, written down.
    """
    webp = dest + ".webp"
    try:
        urllib.request.urlretrieve(frag_url, webp)
        if shutil.which("sips"):
            r = subprocess.run(["sips", "-s", "format", "jpeg", webp, "--out", dest],
                               capture_output=True, text=True)
            ok = r.returncode == 0
        else:                       # the PC (Linux): Pillow reads WebP; ffmpeg still must not
            try:
                from PIL import Image
                Image.open(webp).convert("RGB").save(dest, "JPEG", quality=90)
                ok = True
            except Exception:
                ok = False
        if not ok or not os.path.exists(dest):
            raise IngestError("sheet", "could not convert a storyboard sheet")
        if os.path.getsize(dest) > 8_000_000:       # a sheet is ~120 KB; 8 MB is a runaway
            os.remove(dest)
            raise IngestError("sheet", "storyboard conversion produced a runaway file")
        return dest
    except (urllib.error.URLError, OSError) as e:
        raise IngestError("network", f"could not fetch a storyboard sheet: {e}")
    finally:
        try:
            os.remove(webp)
        except OSError:
            pass


def frames_around(url: str, secs: float, dest_prefix: str, offsets=(0.0,),
                  margin: float = 4.0, timeout: int = 180) -> dict:
    """{offset: jpeg path} — several candidate frames from ONE download.

    Two problems meet here. --download-sections cuts on KEYFRAMES, so the clip does not
    begin at the second asked for and its first frame can be a different slide entirely
    (Codex r1); ffmpeg therefore seeks inside the clip to the real offset. And the second
    itself is only approximate, because yt-dlp derives storyboard timing rather than
    reading it (Codex r3) — so the caller asks for neighbours too and keeps whichever
    actually matches the tile it chose. Video only, 720p: a window's audio is pure cost.
    """
    lo, hi = min(offsets), max(offsets)
    start = max(0.0, secs + lo - margin)
    clip = dest_prefix + ".clip.mp4"
    rc, _out, err = _run([
        "yt-dlp", "--ignore-config", "--no-playlist",
        "-f", "bv[height<=720]/bv*[height<=720]/best[height<=720]/best",
        "--download-sections", f"*{start:.2f}-{secs + hi + margin:.2f}",
        "--retries", "3", "--fragment-retries", "3",
        "--no-progress", "-o", clip, "--", url], timeout=timeout)
    if rc != 0 or not os.path.exists(clip):
        kind, msg = _classify(err)
        raise IngestError(kind, msg)
    out = {}
    try:
        for off in offsets:
            at = secs + off - start
            if at < 0:
                continue
            dest = f"{dest_prefix}{'' if off == 0 else f'{off:+.0f}'}.jpg"
            r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                                "-ss", f"{at:.2f}", "-i", clip,
                                "-frames:v", "1", "-q:v", "3", dest],
                               capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(dest):
                out[off] = dest
        if not out:
            raise IngestError("frame", "ffmpeg could not extract a frame")
        return out
    finally:
        try:
            os.remove(clip)
        except OSError:
            pass


def temp_dir(root: str) -> str:
    """A temp dir under our own cache, not /tmp.

    Why not mkdtemp in /tmp: cleanup is not guaranteed. `finally` and signal handlers
    cover exceptions, SIGINT and SIGTERM — they do NOT survive SIGKILL, a native crash,
    or power loss (Codex r1, and he is right). Keeping the temp under our cache means a
    later run can sweep what a killed run left behind; see sweep()."""
    os.makedirs(root, exist_ok=True)
    return tempfile.mkdtemp(prefix="dl-", dir=root)


def sweep(root: str, older_than_s: int = 86400) -> int:
    """Delete leftovers from runs that never got to clean up. Returns how many."""
    if not os.path.isdir(root):
        return 0
    now, gone = time.time(), 0
    for name in os.listdir(root):
        p = os.path.join(root, name)
        try:
            if now - os.path.getmtime(p) > older_than_s:
                shutil.rmtree(p, ignore_errors=True)
                gone += 1
        except OSError:
            continue
    return gone
