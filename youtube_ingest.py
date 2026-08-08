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
        ("network", "unable to download", "Network problem reaching YouTube."),
        ("network", "temporary failure", "Network problem reaching YouTube."),
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


def fetch_audio(url: str, dest_dir: str, timeout: int = 3600) -> str:
    """Download AUDIO ONLY as 16 kHz mono WAV. Returns the path.

    -f bestaudio is LOAD-BEARING: `-x` alone still downloads yt-dlp's default
    best-video+audio and strips the audio afterwards, so an hour-long 1080p video would
    cross the network for a transcript (Codex review r1). The brief said audio only.

    --match-filter is a SECOND live check at download time, closing the race where an
    upcoming premiere goes live between probe() and here."""
    rc, _out, err = _run([
        "yt-dlp", "--ignore-config", "--no-playlist",
        "-f", "bestaudio",
        "--match-filter", "live_status != 'is_live' & live_status != 'is_upcoming'",
        "-x", "--audio-format", "wav",
        "--postprocessor-args", "ExtractAudio:-ar 16000 -ac 1",
        "--no-progress", "-o", os.path.join(dest_dir, "%(id)s.%(ext)s"),
        "--", url], timeout=timeout)
    if rc != 0:
        kind, msg = _classify(err)
        raise IngestError(kind, msg)
    wavs = [f for f in os.listdir(dest_dir) if f.endswith(".wav")]
    if not wavs:
        raise IngestError("unknown", "yt-dlp reported success but produced no audio "
                                     "(the video may have no audio track)")
    return os.path.join(dest_dir, wavs[0])


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
