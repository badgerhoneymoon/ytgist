"use client";

import { useEffect, useRef, useState } from "react";
import Machine from "../Machine";
import type { GpuSample } from "../types";

const ENGINE = "http://127.0.0.1:8765";

/** A design surface for the machine readout.
 *
 *  It exists so the sparkline can be judged without running a summary (Denis, 2026-08-09).
 *  The top of the page is LIVE — the engine keeps a macmon sampler alive while this page is
 *  polling and stops it twenty seconds after you leave — and below it are synthetic curves
 *  at each temperature band, because an idle Mac sits at 41°C and never shows you what the
 *  hot colours look like.
 */
export default function MachinePage() {
  const [series, setSeries] = useState<GpuSample[]>([]);
  const [err, setErr] = useState("");
  const seen = useRef(0);

  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const d = await (await fetch(`${ENGINE}/api/machine`)).json();
        if (d.series?.length) {
          seen.current += d.series.length;
          setSeries((s) => [...s, ...d.series].slice(-180));
        }
        setErr("");
      } catch {
        setErr("engine not reachable on :8765");
      }
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const last = series.at(-1);

  return (
    <main className="mx-auto w-full max-w-[52rem] px-8 py-16">
      <h1 className="text-[12px] font-semibold uppercase tracking-[0.16em] text-soft">
        ytgist · machine
      </h1>
      <p className="mt-2 text-[15px] text-soft">
        Live from macmon, one sample a second. Three minutes of history.
      </p>

      {err && <p className="mt-4 text-[13px] text-accent">{err}</p>}

      <section className="mt-10 rounded-xl border border-line px-5 py-4">
        <p className="mb-3 text-[11.5px] font-semibold uppercase tracking-[0.1em] text-soft">
          Live · {series.length} samples
        </p>
        <Machine series={series} width={420} height={56} />
        {last && (
          <p className="mt-3 text-[12.5px] text-soft/70 tabular-nums">
            GPU {last.c ?? "—"}°C · CPU {last.cpu ?? "—"}°C · {last.u ?? "—"}% · {last.w ?? "—"} W
          </p>
        )}
      </section>

      <section className="mt-10">
        <p className="mb-4 text-[11.5px] font-semibold uppercase tracking-[0.1em] text-soft">
          The bands, so the colours can be judged
        </p>
        <div className="space-y-5">
          {BANDS.map((b) => (
            <div key={b.label} className="flex items-center gap-6">
              <span className="w-[13rem] shrink-0 text-[13px] text-soft">{b.label}</span>
              <Machine series={b.series} width={260} height={40} />
            </div>
          ))}
        </div>
      </section>

      <section className="mt-12">
        <p className="mb-4 text-[11.5px] font-semibold uppercase tracking-[0.1em] text-soft">
          At the size it appears in the progress line
        </p>
        <div className="space-y-3">
          {BANDS.map((b) => (
            <Machine key={b.label} series={b.series} width={200} height={34} />
          ))}
        </div>
      </section>
    </main>
  );
}

/** Synthetic curves. An idle Mac sits at 41°C and will never show what 90°C looks like. */
function curve(from: number, to: number, wobble = 1.5): GpuSample[] {
  return Array.from({ length: 60 }, (_, i) => {
    const k = i / 59;
    const eased = from + (to - from) * (1 - Math.pow(1 - k, 2));
    return {
      t: i,
      c: Math.round(eased + Math.sin(i / 3) * wobble),
      cpu: null,
      u: Math.round(Math.min(99, 20 + k * 78)),
      w: Math.round((6 + k * 40) * 10) / 10,
    };
  });
}

const BANDS = [
  { label: "idle → warm (45°C)", series: curve(38, 45) },
  { label: "working (68°C)", series: curve(45, 68) },
  { label: "fan audible (82°C)", series: curve(60, 82) },
  { label: "throttling (95°C)", series: curve(74, 95) },
];
