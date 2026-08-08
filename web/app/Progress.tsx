"use client";

import { useEffect, useState } from "react";
import type { Stage } from "./types";

/** ONE segmented track, not a bar plus a row of pills.
 *
 *  The previous version showed a progress bar AND four coloured chips AND a status line —
 *  three indicators competing to say the same thing, which is why it read as noisy
 *  (Denis: "a bit weird"). Now the stages ARE the bar: each one is a segment sized to how
 *  long it actually takes, so the shape itself tells you that summarising is most of the
 *  wait. It is also the same visual object as the timing bar in the finished result —
 *  prediction and outcome in the same language.
 *
 *  Weights are MEASURED (ytgist.py): read info ~1s, download ~8s, transcribe ~10s,
 *  summarise ~40s.
 */
const STEPS: { key: Stage; label: string; weight: number; secs: number }[] = [
  { key: "check", label: "reading video info", weight: 1, secs: 2 },
  { key: "download", label: "downloading audio", weight: 8, secs: 10 },
  { key: "transcribe", label: "transcribing", weight: 10, secs: 12 },
  { key: "summarise", label: "summarising", weight: 40, secs: 45 },
];
const TOTAL = STEPS.reduce((a, s) => a + s.weight, 0);

export default function Progress({
  stage,
  msg,
}: {
  stage: Stage | null;
  pct: number;
  msg: string;
}) {
  const i = STEPS.findIndex((s) => s.key === stage);
  // How far INTO the current stage we are, 0→1. Summarising emits no intermediate signal
  // for ~40s, so a literal bar would freeze and read as a hang. This eases toward the end
  // of the current segment without ever completing it: the segment boundary is real, only
  // the motion inside it is estimated, and the next real frame always wins.
  const [within, setWithin] = useState(0);

  useEffect(() => {
    setWithin(0);
    if (i < 0) return;
    const id = setInterval(() => {
      setWithin((w) => w + (0.97 - w) * (0.3 / STEPS[i].secs));
    }, 200);
    return () => clearInterval(id);
  }, [i]);

  return (
    <div className="mt-10">
      <div className="flex gap-1" aria-label="progress">
        {STEPS.map((s, n) => {
          const done = n < i;
          const now = n === i;
          const fill = done ? 1 : now ? within : 0;
          return (
            <div
              key={s.key}
              className="h-1.5 overflow-hidden rounded-full bg-line/70"
              style={{ flex: s.weight }}
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: `${fill * 100}%`,
                  background: done ? "var(--color-good)" : "var(--color-accent)",
                  transition: "width 500ms var(--ease-out-expo), background 300ms",
                }}
              />
            </div>
          );
        })}
      </div>

      {/* Labels sit under their own segment, so the width IS the cost. Only the running
          one is emphasised — done stages recede rather than shouting a green tick. */}
      <div className="mt-2.5 flex gap-1">
        {STEPS.map((s, n) => (
          <div key={s.key} style={{ flex: s.weight }} className="min-w-0">
            <span
              className={[
                "block truncate text-[12px] transition-colors duration-300",
                n < i ? "text-soft/50" : "",
                n === i ? "font-semibold text-accent" : "",
                n > i ? "text-soft/35" : "",
              ].join(" ")}
            >
              {s.label}
            </span>
          </div>
        ))}
      </div>

      {msg && <p className="mt-4 text-[14px] text-soft">{msg}…</p>}

      {stage === "summarise" && <Skeleton />}
    </div>
  );
}

/** A skeleton in the SHAPE of the answer — numbered rail, headline, three body lines.
 *  During a 40-second wait it tells the eye what is coming, which beats a spinner. */
function Skeleton() {
  return (
    <div className="mt-12 space-y-13" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="grid grid-cols-[3.75rem_minmax(0,1fr)]">
          <div className="flex justify-end pr-3.5">
            <Bar w="14px" h="h-3" delay={i * 140} />
          </div>
          <div className="space-y-3">
            <Bar w={`${58 - i * 6}%`} h="h-4" delay={i * 140 + 40} />
            <div className="space-y-2 pt-1">
              <Bar w="94%" h="h-3" delay={i * 140 + 90} />
              <Bar w="88%" h="h-3" delay={i * 140 + 140} />
              <Bar w="61%" h="h-3" delay={i * 140 + 190} />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function Bar({ w, h, delay }: { w: string; h: string; delay: number }) {
  return (
    <div className={`relative overflow-hidden rounded bg-line/50 ${h}`} style={{ width: w }}>
      <div
        className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent
                   via-canvas/80 to-transparent"
        style={{ animation: `shimmer 1.8s ${delay}ms infinite` }}
      />
    </div>
  );
}
