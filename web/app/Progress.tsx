"use client";

import { useEffect, useState } from "react";
import type { Stage } from "./types";

/** Named stages and a bar that KEEPS MOVING.
 *
 *  The honest problem: summarising takes ~40s and emits no intermediate signal, so a
 *  literal progress bar sits frozen at 75% for most of the wait and reads as a hang —
 *  which is exactly what happened. So the bar advances on its own within the current
 *  stage, easing toward that stage's ceiling and never reaching it. That is not a fake
 *  progress bar: the ceiling is real, only the motion inside it is estimated, and a real
 *  frame always overrides the estimate.
 *
 *  Weights come from MEASURED costs (ytgist.py): read info ~1s, download ~8s,
 *  transcribe ~10s, summarise ~40s. Cached runs skip the middle two entirely.
 */
const STEPS: { key: Stage; label: string; ceiling: number; secs: number }[] = [
  { key: "check", label: "reading video info", ceiling: 12, secs: 2 },
  { key: "download", label: "downloading audio", ceiling: 38, secs: 10 },
  { key: "transcribe", label: "transcribing", ceiling: 72, secs: 12 },
  { key: "summarise", label: "summarising", ceiling: 98, secs: 45 },
];

export default function Progress({
  stage,
  pct,
  msg,
}: {
  stage: Stage | null;
  pct: number;
  msg: string;
}) {
  const i = STEPS.findIndex((s) => s.key === stage);
  const [crept, setCrept] = useState(pct);

  useEffect(() => {
    setCrept((c) => Math.max(c, pct));
    if (i < 0) return;
    const { ceiling, secs } = STEPS[i];
    const id = setInterval(() => {
      // Approach the ceiling asymptotically: fast at first, then slower — the shape of
      // "still working" rather than "nearly done".
      setCrept((c) => (c >= ceiling ? c : c + (ceiling - c) * (0.35 / secs)));
    }, 250);
    return () => clearInterval(id);
  }, [pct, i]);

  return (
    <div className="col-start-2 mt-10">
      <div className="h-1 overflow-hidden rounded-full bg-line">
        <div
          className="h-full rounded-full bg-accent"
          style={{
            width: `${Math.min(crept, 99)}%`,
            transition: "width 400ms var(--ease-out-expo)",
          }}
        />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {STEPS.map((s, n) => {
          const done = n < i;
          const now = n === i;
          return (
            <span
              key={s.key}
              className={[
                "flex items-center gap-1.5 rounded-full border px-3 py-1 text-[13px]",
                "transition-all duration-300",
                done ? "border-good/35 bg-good/[0.08] text-good" : "",
                now ? "border-accent bg-accent font-semibold text-canvas shadow-[0_1px_6px_rgba(194,87,26,0.25)]" : "",
                !done && !now ? "border-line text-soft/55" : "",
              ].join(" ")}
              style={{ transitionTimingFunction: "var(--ease-out-expo)" }}
            >
              {done && <span className="text-[11px] font-bold">✓</span>}
              {now && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
              {s.label}
            </span>
          );
        })}
      </div>

      {msg && <p className="mt-3.5 text-[14px] text-soft">{msg}…</p>}

      {/* A skeleton in the SHAPE of the answer, shown during the long tail. It tells the
          eye what is coming, which is worth more than a spinner during a 40s wait. */}
      {stage === "summarise" && <Skeleton />}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="mt-10 space-y-8" aria-hidden>
      {[92, 78, 85].map((w, i) => (
        <div key={i} className="space-y-2.5">
          <Bar w={`${w * 0.45}%`} h="h-4" delay={i * 120} />
          <Bar w={`${w}%`} h="h-3" delay={i * 120 + 60} />
          <Bar w={`${w - 18}%`} h="h-3" delay={i * 120 + 120} />
        </div>
      ))}
    </div>
  );
}

function Bar({ w, h, delay }: { w: string; h: string; delay: number }) {
  return (
    <div className={`relative overflow-hidden rounded bg-line/60 ${h}`} style={{ width: w }}>
      <div
        className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent
                   via-canvas/70 to-transparent"
        style={{ animation: `shimmer 1.6s ${delay}ms infinite` }}
      />
    </div>
  );
}
