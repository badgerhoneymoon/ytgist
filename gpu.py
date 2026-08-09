#!/usr/bin/env python3
"""What the GPU is doing, without sudo and without dependencies.

Denis, 2026-08-09: the MacBook gets hot and the fan spins up during a summary, and nothing
on screen explains why. This is the part of that question we can answer for free.

TWO SOURCES, in order of what they can tell you.

`macmon` (brew) reads the SMC sensors properly and sudo-free, which gives real degrees, fan
RPM and watts. It is OPTIONAL — a direct ctypes attempt at the same IOHIDEventSystemClient
route returned zero sensors on macOS 26, so this is not work worth repeating badly, but it
is also not worth making a hard dependency of a local tool.

`ioreg -c IOAccelerator` needs nothing at all and always answers: utilisation and how much
system memory the GPU holds. The second is the interesting one — a parked 21GB llama-server
is visible in it, which is the actual reason the fan is running.

So: degrees when macmon is installed, load either way.
"""
import collections
import json
import re
import subprocess
import threading
import time

_NUM = r'"{}"\s*=\s*(\d+)'


def _macmon() -> dict:
    """Degrees, fan and watts — or {} if macmon is not installed. Never raises.

    One sample takes ~1.9s because macmon measures over an interval; that is fine on a
    ten-second heartbeat and would be absurd per frame."""
    try:
        out = subprocess.run(["macmon", "pipe", "-s", "1", "-i", "500"],
                             capture_output=True, text=True, timeout=6).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return {}
    try:
        d = json.loads(out.splitlines()[0])
    except (ValueError, IndexError):
        return {}

    got = {}
    t = d.get("temp") or {}
    if t.get("gpu_temp_avg"):
        got["gpu_c"] = round(t["gpu_temp_avg"])
    if t.get("cpu_temp_avg"):
        got["cpu_c"] = round(t["cpu_temp_avg"])
    if d.get("all_power"):
        got["watts"] = round(d["all_power"], 1)
    fans = d.get("fans") or []
    rpm = max((f.get("speed", 0) for f in fans), default=0)
    if rpm:
        got["fan_rpm"] = round(rpm)
    # macmon's gpu_usage is (freq, ratio 0-1); the ratio is the useful half.
    gu = d.get("gpu_usage")
    if isinstance(gu, list) and len(gu) == 2:
        got["util"] = round(gu[1] * 100)
    return got


def stats() -> dict:
    """Everything we can see about the GPU. Empty dict if nothing is available.

    Never raises — a monitoring readout must not be able to fail a summary."""
    got = _macmon()
    try:
        out = subprocess.run(
            ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
            capture_output=True, text=True, timeout=4).stdout
    except Exception:
        return got

    def num(key):
        m = re.search(_NUM.format(re.escape(key)), out)
        return int(m.group(1)) if m else None

    util = num("Device Utilization %")
    mem = num("In use system memory")
    if util is not None and "util" not in got:
        got["util"] = util
    if mem is not None:
        got["mem_gb"] = round(mem / 1e9, 1)
    return got


if __name__ == "__main__":
    s = stats()
    if not s:
        print("  GPU stats unavailable")
    else:
        bits = [f"GPU {s.get('util', '?')}%"]
        if "gpu_c" in s:
            bits.append(f"{s['gpu_c']}°C GPU / {s.get('cpu_c', '?')}°C CPU")
        if "mem_gb" in s:
            bits.append(f"{s['mem_gb']} GB held")
        if "watts" in s:
            bits.append(f"{s['watts']} W")
        if "fan_rpm" in s:
            bits.append(f"fan {s['fan_rpm']} rpm")
        print("  " + " · ".join(bits))


# ------------------------------------------------------------------ continuous sampling
#
# A CHART NEEDS A STREAM, NOT POLLS. One `macmon pipe -s 1` costs ~1.9s because macmon
# measures across an interval, so asking once a second would mean a new process permanently
# in flight. Instead one long-lived `macmon pipe -s 0 -i 1000` is left running for the
# duration of a job and its lines are read as they arrive: one sample a second, one process,
# effectively free (Denis, 2026-08-09).
class Sampler:
    """A second-by-second stream of machine stats while a job runs.

    Never raises and never blocks the caller. If macmon is missing, drain() simply returns
    nothing and the UI falls back to the ioreg numbers on the heartbeat."""

    def __init__(self, keep: int = 900):
        self._proc = None
        self._buf = collections.deque(maxlen=keep)
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self):
        if self._proc is not None:
            return
        try:
            self._proc = subprocess.Popen(
                ["macmon", "pipe", "-s", "0", "-i", "1000"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except (FileNotFoundError, OSError):
            self._proc = None
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        proc = self._proc
        for line in proc.stdout:
            if self._stop.is_set():
                break
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = (d.get("temp") or {})
            gu = d.get("gpu_usage")
            with self._lock:
                self._buf.append({
                    "t": round(time.time(), 1),
                    "c": round(t["gpu_temp_avg"]) if t.get("gpu_temp_avg") else None,
                    "cpu": round(t["cpu_temp_avg"]) if t.get("cpu_temp_avg") else None,
                    "u": round(gu[1] * 100) if isinstance(gu, list) and len(gu) == 2 else None,
                    "w": round(d["all_power"], 1) if d.get("all_power") else None,
                })

    def drain(self):
        """Everything sampled since the last call. The caller owns it from here."""
        with self._lock:
            out = list(self._buf)
            self._buf.clear()
        return out

    def stop(self):
        self._stop.set()
        p, self._proc = self._proc, None
        if p is not None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                pass
        self._stop.clear()
