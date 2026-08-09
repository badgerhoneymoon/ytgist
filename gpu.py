#!/usr/bin/env python3
"""What the GPU is doing, without sudo and without dependencies.

Denis, 2026-08-09: the MacBook gets hot and the fan spins up during a summary, and nothing
on screen explains why. This is the part of that question we can answer for free.

TEMPERATURE IS NOT HERE, deliberately. `powermetrics --samplers smc` needs root, which an
app cannot ask for on every run. The sudo-free route is IOHIDEventSystemClient — the
mechanism macmon and smctemp use — and a direct ctypes attempt returned no sensors on this
machine (macOS 26), so rather than ship something that silently reads nothing, temperature
waits for a real dependency.

What IOAccelerator exposes needs neither: utilisation, and how much system memory the GPU
has taken. The second is the interesting one here — a 21GB llama-server is visible in it,
which is the actual reason the fan is running.
"""
import re
import subprocess

_NUM = r'"{}"\s*=\s*(\d+)'


def stats() -> dict:
    """{"util": 0-100, "mem_gb": float} — empty dict if unavailable. Never raises.

    One ioreg call, ~60ms. Cheap enough for a 10-second heartbeat, far too slow for a
    per-frame poll, which is why it rides along with the beat rather than having its own.
    """
    try:
        out = subprocess.run(
            ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
            capture_output=True, text=True, timeout=4).stdout
    except Exception:
        return {}

    def num(key):
        m = re.search(_NUM.format(re.escape(key)), out)
        return int(m.group(1)) if m else None

    util = num("Device Utilization %")
    mem = num("In use system memory")
    got = {}
    if util is not None:
        got["util"] = util
    if mem is not None:
        got["mem_gb"] = round(mem / 1e9, 1)
    return got


if __name__ == "__main__":
    s = stats()
    print(f"  GPU {s.get('util', '?')}% · {s.get('mem_gb', '?')} GB in use" if s
          else "  GPU stats unavailable")
