"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import Machine from "./Machine";
import type { GpuSample, Stage } from "./types";

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
/** Seconds this run will spend in a stage, from the engine's own estimate. "model load"
 *  is folded into summarising: it is part of the same wait, and a 6-second sliver is not
 *  a stage worth drawing. */
function secsOf(eta: Record<string, number>, key: Stage): number {
  if (key === "check") return eta["read info"] ?? 0;
  // Model load rides with whichever of the two is actually happening — only one ever is.
  if (key === "summarise") return (eta["summarise"] ?? 0) + (eta["model load"] ?? 0);
  return eta[key] ?? 0;
}

const STEPS: { key: Stage; label: string; weight: number; secs: number }[] = [
  { key: "check", label: "reading video info", weight: 1, secs: 2 },
  { key: "download", label: "downloading audio", weight: 8, secs: 10 },
  { key: "transcribe", label: "transcribing", weight: 10, secs: 12 },
  { key: "summarise", label: "summarising", weight: 40, secs: 45 },
];

export default function Progress({
  stage,
  msg,
  eta,
  gpuSeries,
  phaseAgo = 0,
  onCancel,
}: {
  stage: Stage | null;
  pct: number;
  msg: string;
  eta: Record<string, number> | null;
  /** A second-by-second stream of machine state while the job runs. It explains the fan. */
  gpuSeries?: GpuSample[];
  /** Seconds this phase had ALREADY been running when the page loaded. Non-zero only when
   *  rejoining a run in flight — otherwise the countdown would restart from the full
   *  estimate on every reload while the work carried on underneath. */
  phaseAgo?: number;
  onCancel: () => void;
}) {
  // WHICH STAGES WILL ACTUALLY RUN. A cached transcript skips download and transcription
  // entirely, and the bar used to draw them anyway as near-zero ghosts — three slivers
  // representing work that is not happening (Denis: "it's squeezed the first steps…
  // I would not do this"). The ETA is the authority: a phase it does not mention is a
  // phase this run will not perform.
  const steps = eta
    ? STEPS.filter((s) => secsOf(eta, s.key) > 0)
    : STEPS;
  const secs = steps.map((s) => (eta ? secsOf(eta, s.key) : s.secs));
  const totalEta = eta ? secs.reduce((a, b) => a + b, 0) : 0;

  // Widths are proportional but FLOORED. Pure proportion breaks down the moment one stage
  // is 95% of the work: every other segment shrinks below the width of its own border and
  // the bar stops reading as a sequence at all.
  const floor = totalEta ? totalEta * 0.12 : 0;
  const weights = secs.map((v) => Math.max(v, floor));

  const done = stage === "done";
  const i = done ? steps.length : steps.findIndex((s) => s.key === stage);

  // ONE clock, 0.2s resolution, running for the life of the component. The stage anchor
  // is stamped from inside the same tick — reading a ref during render is forbidden, and
  // a ref read inside an interval callback is not, so the current stage travels there.
  const iRef = useRef(i);
  useEffect(() => {
    iRef.current = i;
  }, [i]);

  const [now, setNow] = useState(0);
  const [mark, setMark] = useState({ i: -1, at: 0 });
  // What each finished phase ACTUALLY took. Once a stage is behind us its estimate is of no
  // further interest — the real number is better information and it is already known.
  const [spent, setSpent] = useState<Record<number, number>>({});
  // Carried only until the first real stage change; after that the local clock is exact.
  const [base, setBase] = useState(phaseAgo);
  useEffect(() => {
    const t0 = performance.now();
    const id = setInterval(() => {
      const t = (performance.now() - t0) / 1000;
      setNow(t);
      setMark((m) => {
        if (m.i === iRef.current) return m;
        // ONLY a real stage change discards the carried time. The mark starts at -1, so the
        // first tick after mount looked like a transition and threw away the phase_elapsed
        // we had just rejoined with — the bar showed the right number for one frame and
        // then reset to zero (Denis, 2026-08-09).
        if (m.i >= 0) {
          setSpent((sp) => ({ ...sp, [m.i]: t - m.at }));
          setBase(0);
        }
        return { i: iRef.current, at: t };
      });
    }, 200);
    return () => clearInterval(id);
  }, []);

  // ANCHORED TO THE REAL STAGE CHANGE, not to a position on the total timeline.
  //
  // The fill used to be `elapsed - (estimated seconds of every earlier stage)`. When the
  // earlier stages ran FASTER than predicted, that difference was negative and the current
  // segment sat stubbornly empty while the countdown ticked down beside it (Denis: "why is
  // the progress not seen for that stage?"). The stage change is a real event and the
  // estimate is not, so the real event is what the clock should hang from.
  const inStage = base + (mark.i === i ? Math.max(0, now - mark.at) : 0);

  // Remaining = what is left of THIS stage, plus the estimates for the ones after it.
  const tail = totalEta ? secs.slice(i + 1).reduce((a, b) => a + b, 0) : 0;
  const thisLeft = totalEta && i >= 0 ? Math.max(0, secs[i] - inStage) : 0;
  const remaining = thisLeft + tail;
  const over = totalEta > 0 && i >= 0 && inStage > secs[i];

  // FALLBACK, for the seconds before the engine reports the video's length and there is
  // no estimate at all: an easing curve that decelerates and never completes. It is a
  // guess, and it is only ever on screen while we have nothing better.
  const [prog, setProg] = useState({ i: -1, w: 0 });
  useEffect(() => {
    if (totalEta || i < 0 || i >= steps.length) return;
    const id = setInterval(() => {
      setProg((p) => {
        const w = p.i === i ? p.w : 0;
        return { i, w: w + (0.97 - w) * (0.3 / Math.max(secs[i], 1)) };
      });
    }, 200);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [i, totalEta]);

  /** How full segment n should be, 0→1. A REPORTED stage always wins over the clock:
   *  real signal beats estimate, so a stage the engine has already left is finished no
   *  matter what the arithmetic says. */
  const fillOf = (n: number) => {
    if (done) return 1;
    if (n < i) return 1;
    if (n > i) return 0;
    if (!totalEta) return prog.i === i ? prog.w : 0;
    return Math.min(inStage / Math.max(secs[n], 1), 0.985);
  };

  return (
    <div className="mt-10">
      {/* PER-PHASE SECONDS, above their own segment (Denis, 2026-08-09). Numbers where
          labels could not go: "47s" is three characters where "downloading audio" is
          seventeen, so it survives a segment that a word could never fit in. Anything under
          a tenth of the track still gets nothing — a truncated number is worse than none,
          and that lesson already cost us one redesign. */}
      {totalEta > 0 && (
        <div className="mb-1.5 flex gap-1">
          {steps.map((s, n) => (
            <div key={s.key} style={{ flex: weights[n] }} className="min-w-0">
              {/* A COUNTDOWN. A timer runs DOWN to zero — I built it counting up the
                  first time, which is a stopwatch, not a timer (Denis, 2026-08-09).
                  The running phase counts its estimate down; a phase already finished shows
                  what it really cost; the ones ahead show the estimate they will count from.
                  Overrunning shows as "+12s" rather than freezing at zero, because a clock
                  stuck on 0.0 while work continues is the same lie as a progress bar
                  parked at 98%. */}
              <span
                className={[
                  "block text-[11px] tabular-nums transition-colors duration-300",
                  weights[n] / totalEta < 0.1 ? "invisible" : "",
                  n === i ? "font-semibold text-accent" : "",
                  n < i || done ? "text-good" : "",
                  n > i && !done ? "text-soft/40" : "",
                ].join(" ")}
              >
                {n === i && !done
                  ? secs[n] - inStage >= 0
                    ? tick(secs[n] - inStage)
                    : `+${tick(inStage - secs[n])}`
                  : spent[n] !== undefined
                    ? tick(spent[n])
                    : tick(secs[n])}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="flex gap-1" aria-label="progress">
        {steps.map((s, n) => {
          const fill = fillOf(n);
          return (
            <div
              key={s.key}
              title={`${s.label} · about ${Math.round(secs[n])}s`}
              className="h-1.5 overflow-hidden rounded-full bg-line/70"
              style={{ flex: weights[n] }}
            >
              {over && n === i ? (
                <div
                  className="h-full w-full rounded-full opacity-70"
                  style={{
                    background:
                      "repeating-linear-gradient(115deg, var(--color-accent) 0 10px, " +
                      "color-mix(in srgb, var(--color-accent) 45%, transparent) 10px 20px)",
                    animation: "drift 900ms linear infinite",
                  }}
                />
              ) : (
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${fill * 100}%`,
                    background: fill >= 1 ? "var(--color-good)" : "var(--color-accent)",
                    transition: "width 500ms var(--ease-out-expo), background 300ms",
                  }}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* ONE line of text, not a label per segment. A 2-second stage gets a 2%-wide
          segment, so a label under it can only ever be "r…" — proportional widths and
          per-segment labels are fundamentally incompatible (Denis: "truncated, it's
          ugly"). The bar already carries the proportions; the words only need to say
          what is happening NOW. */}
      <div className="mt-3 flex items-baseline justify-between gap-4">
        <p className="min-w-0 truncate text-[14px]">
          {/* "done" is a real stage the engine sends just before the result frame. It is
              not in this list, so it used to read as index -1 — blanking the whole bar and
              relabelling it "starting" at the exact moment the run succeeded. */}
          <span className="font-semibold text-accent">
            {done ? "finishing" : (steps[i]?.label ?? "starting")}
          </span>
          {msg && <span className="text-soft"> · {msg}</span>}
        </p>
        <span className="flex shrink-0 items-center gap-2.5 text-[12.5px] tabular-nums text-soft/70">
          {!!gpuSeries?.length && <Machine series={gpuSeries} width={110} height={20} />}
          {done
            ? "done"
            : over
              ? `over estimate · ${clock(now)}`
              : totalEta > 0
                ? left(remaining)
                : clock(now)}
          {/* Stop matters most on the longest runs, which is exactly when the page looks
              most stuck. It tells the ENGINE to stop, not just this tab — otherwise the
              run keeps the model locked and the next one queues behind work nobody
              wants (Denis, 2026-08-08). */}
          <button
            onClick={onCancel}
            title="stop this run"
            className="flex items-center gap-1 rounded-full border border-line px-2 py-0.5
                       text-[11.5px] font-medium text-soft transition-colors duration-150
                       hover:border-red-300 hover:bg-red-50 hover:text-red-700
                       focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <X size={11} strokeWidth={2.5} />
            Stop
          </button>
        </span>
      </div>

      {(stage === "summarise" || done) && <Skeleton />}
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

/** A phase clock: seconds under a minute, then m:ss. Tenths below ten seconds, because at
 *  that scale a bare "2s" sitting still looks broken where "2.4s" visibly moves. */
function tick(secs: number): string {
  if (secs < 10) return `${secs.toFixed(1)}s`;
  if (secs < 60) return `${Math.round(secs)}s`;
  return `${Math.floor(secs / 60)}:${String(Math.round(secs % 60)).padStart(2, "0")}`;
}

/** Elapsed, in units a person reads at a glance. */
function clock(secs: number): string {
  const m = Math.floor(secs / 60);
  return m ? `${m}m ${Math.round(secs % 60)}s` : `${Math.round(secs)}s`;
}

/** Time remaining, phrased the way a person would say it — and never a countdown that
 *  hits zero while you are still waiting. */
function left(secs: number): string {
  if (secs <= 5) return "almost done";
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return m ? `~${m}m ${s}s left` : `~${s}s left`;
}
