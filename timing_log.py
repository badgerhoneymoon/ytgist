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
KEEP = 200                  # newest N kept; older entries describe a machine long gone
MIN_SAMPLES = 3             # below this, a median is just an anecdote

# Phases whose cost scales with the length of the video.
SCALING = ("download", "transcribe", "summarise", "answer")


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
           predicted: dict | None = None):
    """Append one finished run, WITH the prediction it was given.

    Storing only the actuals makes the log self-improving but unfalsifiable — you can see
    the rates move without ever learning whether the numbers shown to the user got better
    (Denis, 2026-08-08). Keeping the prediction beside the outcome turns that into a
    measurable error that should shrink."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        row = {"at": time.time(), "minutes": round(minutes, 2), "cached": bool(cached),
               "ctx": int(ctx or 0), "power": power_mode(), "native": bool(native),
               "timings": {k: round(float(v), 2) for k, v in (timings or {}).items()
                           if not k.startswith("_")},
               "predicted": {k: round(float(v), 2) for k, v in (predicted or {}).items()}}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _trim()
    except Exception:
        pass


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


def _median_rate(rows, phase):
    """Seconds per minute of video for one phase, or None if nothing usable."""
    vals = [r["timings"][phase] / r["minutes"]
            for r in rows
            if r.get("minutes", 0) > 0.5 and phase in (r.get("timings") or {})]
    return statistics.median(vals) if len(vals) >= MIN_SAMPLES else None


def _median_load(rows, ctx):
    """Model load is flat in video length but grows with context, so match on context
    band first and widen only if that is too thin to be meaningful."""
    def loads(pred):
        return [r["timings"]["model load"] for r in pred
                if "model load" in (r.get("timings") or {})]

    band = loads([r for r in rows if abs(r.get("ctx", 0) - ctx) <= 16384])
    if len(band) >= MIN_SAMPLES:
        return statistics.median(band)
    allv = loads(rows)
    return statistics.median(allv) if len(allv) >= MIN_SAMPLES else None


def learned(power: str, ctx: int) -> dict:
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
        rate = _median_rate(src, phase)
        if rate is not None:
            out[phase] = rate
    load = _median_load(src, ctx)
    if load is not None:
        out["model load"] = load
    out["_samples"] = len(src)
    out["_power"] = power
    return out


def accuracy():
    """(predicted, actual, signed error %) per run that carries a prediction, oldest first."""
    out = []
    for r in _rows():
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
    for p, a, e in accuracy()[-8:]:
        print(f"    predicted {p:>6.0f}s   actual {a:>6.0f}s   {e:+.0f}%")
    for phase in SCALING:
        r = _median_rate(rows, phase)
        print(f"  {phase:11} {r:.2f} s per minute of video" if r
              else f"  {phase:11} — not enough samples")
