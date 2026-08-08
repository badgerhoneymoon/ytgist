#!/usr/bin/env python3
"""Every run's real timings, so the ETA gets better instead of staying a guess.

Denis, 2026-08-08: "we're running real experiments and we're getting real numbers… then
each log entry we are more and more precise."

The constants this replaces were fitted by hand from four runs and were ~20% long. They
also could not know things that genuinely change the answer — low power mode roughly
halves GPU throughput, a cached transcript skips two phases entirely, and a bigger context
makes the model load slower. Rather than model any of that, record what actually happened
and read the answer back off the record.

DESIGN NOTES
  * MEDIAN, not mean. One run that collided with a 20GB model load is an outlier, and a
    mean lets it poison the estimate for the next twenty runs.
  * Rates are per MINUTE OF VIDEO for the phases that scale with length, and absolute for
    model load, which scales with context instead.
  * Power mode is a hard split, not a correction factor: the same machine at 40% GPU is
    effectively a different machine, and blending the two makes both estimates wrong.
  * Falls back gracefully — matching runs, then all runs, then the hand-fitted constants.
    A brand new install still gets a usable number.
"""
import json
import os
import statistics
import subprocess
import time

LOG = os.path.expanduser("~/.ytgist/runs.jsonl")
EXPANDS = os.path.expanduser("~/.ytgist/expands.jsonl")
KEEP = 200                  # newest N kept; older entries describe a machine long gone
MIN_SAMPLES = 3             # below this, a median is just an anecdote

# Phases whose cost scales with the length of the video.
SCALING = ("download", "transcribe", "summarise", "images")


def power_mode() -> str:
    """"low" or "normal". Low power mode throttles the GPU hard enough that a transcript
    which takes 35s on mains can take twice that, which is exactly the kind of surprise an
    ETA exists to prevent."""
    try:
        out = subprocess.run(["pmset", "-g"], capture_output=True, text=True,
                             timeout=3).stdout
        for line in out.splitlines():
            if "lowpowermode" in line:
                return "low" if line.strip().split()[-1] not in ("0", "false") else "normal"
    except Exception:
        pass
    return "normal"


def record(minutes: float, cached: bool, ctx: int, timings: dict, native: bool = False,
           predicted: dict | None = None, warm: bool = False):
    """Append one finished run, WITH the prediction it was given.

    Storing only the actuals makes the log self-improving but unfalsifiable — you can see
    the rates move without ever learning whether the numbers shown to the user got better
    (Denis, 2026-08-08). Keeping the prediction beside the outcome turns that into a
    measurable error that should shrink."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        row = {"at": time.time(), "minutes": round(minutes, 2), "cached": bool(cached),
               "ctx": int(ctx or 0), "power": power_mode(), "native": bool(native),
               "warm": bool(warm),
               "timings": {k: round(float(v), 2) for k, v in (timings or {}).items()
                           if not k.startswith("_")},
               "predicted": {k: round(float(v), 2) for k, v in (predicted or {}).items()}}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _trim()
    except Exception:
        pass


def record_expand(minutes: float, chars: int, warm: bool, secs: float, native: bool = False):
    """One "more detail" click. Kept in its OWN file, not runs.jsonl.

    An expansion is a different animal from a gist run — no download, no transcription, a
    window of a couple of minutes rather than a whole video — and mixing the two would
    corrupt the per-minute fits that runs.jsonl exists to produce. It is also the operation
    clicked most often and the one we had no numbers for at all: the claim that parking the
    server halves its cost was measured once, by hand, and never again (2026-08-08)."""
    try:
        os.makedirs(os.path.dirname(EXPANDS), exist_ok=True)
        row = {"at": time.time(), "window_min": round(minutes, 2), "chars": int(chars),
               "warm": bool(warm), "native": bool(native), "power": power_mode(),
               "secs": round(float(secs), 2)}
        with open(EXPANDS, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def expand_report() -> str:
    """Cold versus warm, in seconds. The whole point of the warm pool, measured."""
    try:
        with open(EXPANDS, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return "no expansions logged yet"
    if not rows:
        return "no expansions logged yet"
    cold = [r["secs"] for r in rows if not r["warm"]]
    warm = [r["secs"] for r in rows if r["warm"]]
    parts = [f"{len(rows)} expansions"]
    if cold:
        parts.append(f"cold {statistics.median(cold):.1f}s (n={len(cold)})")
    if warm:
        parts.append(f"warm {statistics.median(warm):.1f}s (n={len(warm)})")
    if cold and warm:
        parts.append(f"→ {statistics.median(cold) / max(statistics.median(warm), .01):.1f}x")
    return " · ".join(parts)


def _rows():
    try:
        with open(LOG, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []


def _trim():
    rows = _rows()
    if len(rows) <= KEEP:
        return
    with open(LOG, "w", encoding="utf-8") as f:
        for r in rows[-KEEP:]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _fit(rows, phase):
    """(fixed_seconds, seconds_per_minute) for a phase, or None.

    AFFINE, not a bare rate. Summarising a 7-minute video cost 8.0 s/min while an 87-minute
    one cost 4.2 — not because the long one is efficient, but because the OUTPUT barely
    grows: a short video still gets five takeaways written out. Cost is prefill (scales with
    length) plus generation (roughly fixed), and a single per-minute number splits the
    difference badly at both ends — it under-predicted a short video by 105% (2026-08-08).

    Least squares, then clamped: a negative intercept or slope is a fitting artefact of a
    thin sample, not a discovery that longer videos are cheaper."""
    pts = [(r["minutes"], r["timings"][phase])
           for r in rows
           if r.get("minutes", 0) > 0.5 and phase in (r.get("timings") or {})]
    if len(pts) < MIN_SAMPLES:
        return None
    # One point per length BAND, so eleven 11-minute clips do not outvote the single
    # 7-minute one, and two feature-length videos do not set the whole line.
    bands = {}
    for x, y in pts:
        bands.setdefault(int(x // 10), []).append((x, y))
    pts = [(statistics.median([x for x, _ in v]), statistics.median([y for _, y in v]))
           for v in bands.values()]
    if len(pts) < 2:
        x, y = pts[0]
        return (0.0, y / x)
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    var = sum((x - mx) ** 2 for x, _ in pts)
    if var < 1e-9:                       # every sample the same length: no slope to find
        return (my, 0.0)
    slope = sum((x - mx) * (y - my) for x, y in pts) / var
    slope = max(slope, 0.0)
    return (max(my - slope * mx, 0.0), slope)


def _median_rate(rows, phase):
    """Seconds per minute — kept for the CLI report, which reads better as one number."""
    vals = [r["timings"][phase] / r["minutes"]
            for r in rows
            if r.get("minutes", 0) > 0.5 and phase in (r.get("timings") or {})]
    return statistics.median(vals) if len(vals) >= MIN_SAMPLES else None


def _median_load(rows, ctx, warm=False):
    """Model load, split by WARM FIRST.

    It is bimodal: 0.0s when the parked server is reused, 5-9s when one has to start. A
    median across both predicted ~2s and was never right in either case (2026-08-08). Only
    once warm and cold are separated does the context band mean anything."""
    def loads(pred):
        return [r["timings"]["model load"] for r in pred
                if "model load" in (r.get("timings") or {})]

    same = [r for r in rows if bool(r.get("warm")) == bool(warm)]
    if len(loads(same)) >= MIN_SAMPLES:
        rows = same
    band = loads([r for r in rows if abs(r.get("ctx", 0) - ctx) <= 16384])
    if len(band) >= MIN_SAMPLES:
        return statistics.median(band)
    allv = loads(rows)
    return statistics.median(allv) if len(allv) >= MIN_SAMPLES else None


def learned(power: str, ctx: int, warm: bool = False) -> dict:
    """What the log knows, as {phase: seconds-per-minute} plus {"model load": seconds}.

    Missing keys mean "not enough evidence yet" and the caller keeps its own default —
    partial knowledge is used where it exists rather than discarded wholesale."""
    rows = _rows()
    if not rows:
        return {}
    same = [r for r in rows if r.get("power") == power]
    # Enough same-power runs to stand on their own? Use only those; otherwise everything.
    src = same if len(same) >= MIN_SAMPLES else rows

    out = {}
    for phase in SCALING:
        fit = _fit(src, phase)
        if fit is not None:
            out[phase] = fit                  # (fixed seconds, seconds per minute)
    load = _median_load(src, ctx, warm)
    if load is not None:
        out["model load"] = load
    out["_samples"] = len(src)
    out["_power"] = power
    return out


def accuracy():
    """(predicted, actual, signed error %) per run that carries a prediction, oldest first.

    Runs whose phases no longer exist are SKIPPED. Two of the first three scored runs
    measured an "answer" phase from the Ask feature, which has since been deleted — leaving
    them in made the trend look like a regression when the estimator they indict is gone."""
    known = set(SCALING) | {"model load", "read info"}
    out = []
    for r in _rows():
        if set((r.get("predicted") or {})) - known:
            continue
        p = sum((r.get("predicted") or {}).values())
        a = sum((r.get("timings") or {}).values())
        if p > 0 and a > 0:
            out.append((p, a, (a - p) / p * 100))
    return out


def drift() -> str:
    """Is the estimate getting better? Compares the newest runs against the ones before.

    Median ABSOLUTE error, because over- and under-shoots must not cancel out into a
    flattering zero."""
    rows = accuracy()
    if len(rows) < 2:
        return f"{len(rows)} scored run(s) — need a few more before a trend means anything"
    errs = [abs(e) for _, _, e in rows]
    half = max(1, len(errs) // 2)
    old, new = statistics.median(errs[:-half]), statistics.median(errs[-half:])
    arrow = "improving" if new < old else "getting worse" if new > old else "flat"
    return (f"prediction error: {old:.0f}% → {new:.0f}% ({arrow}) "
            f"over {len(errs)} scored runs")


def summary() -> str:
    """One line for the log, so it is visible that calibration is happening at all."""
    rows = _rows()
    if not rows:
        return "no timing history yet — using built-in estimates"
    p = power_mode()
    same = sum(1 for r in rows if r.get("power") == p)
    return f"{len(rows)} runs logged ({same} at {p} power) — ETA is calibrated from them"


if __name__ == "__main__":
    rows = _rows()
    print(summary())
    print(drift())
    print(expand_report())
    for p, a, e in accuracy()[-8:]:
        print(f"    predicted {p:>6.0f}s   actual {a:>6.0f}s   {e:+.0f}%")
    for phase in SCALING:
        r = _median_rate(rows, phase)
        print(f"  {phase:11} {r:.2f} s per minute of video" if r
              else f"  {phase:11} — not enough samples")
