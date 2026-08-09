"use client";

import type { GpuSample } from "./types";

/** FOUR STATES, FOUR HUES — and they have to be told apart at a glance.
 *
 *  The first attempt used grey for working and then accent-orange and red for the top two
 *  bands, which read as one colour twice: "why are the two last ones all red?" (Denis,
 *  2026-08-09). Grey was worse than wrong — it is the page's colour for "inert", so a
 *  working machine looked switched off.
 *
 *  Green → amber → orange → red is the ordering everyone already knows, and the thresholds
 *  are Apple Silicon's own behaviour rather than round numbers: sustained work sits in the
 *  sixties, the fan becomes audible around eighty, and past ninety the SoC pulls its clocks
 *  back — which the ETA would otherwise report as an unexplained slowdown.
 */
export const FAN_AUDIBLE = 80;

export function hueFor(c: number | null | undefined): string {
  if (c === undefined || c === null) return "var(--color-soft)";
  if (c >= 90) return "#B3261E";                 // throttling
  if (c >= FAN_AUDIBLE) return "var(--color-accent)";  // fan audible
  if (c >= 60) return "#C8971B";                 // working
  return "var(--color-good)";                    // cool
}

/** The machine, while it works.
 *
 *  Denis, 2026-08-09: "my MacBook is getting hot and I hear the fan" — and nothing on
 *  screen explained why. A number alone answers "how hot"; the line answers "and is it
 *  still climbing", which is the question you actually have while waiting.
 *
 *  Drawn as a plain SVG path rather than a chart library: one series, no axes, no legend,
 *  no interaction. Anything more would be a chart pretending a sparkline's job is bigger
 *  than it is.
 */
export default function Machine({
  series,
  width = 200,
  height = 34,
}: {
  series: GpuSample[];
  width?: number;
  height?: number;
}) {
  const pts = series.filter((s) => s.c !== null);
  const last = pts.at(-1);
  const c = last?.c ?? null;
  const hue = hueFor(c);

  // A FIXED WINDOW, not min-to-max. Auto-scaling makes a one-degree wobble look like a
  // crisis and a genuine climb look flat; 30-100°C keeps every reading comparable to every
  // other, which is the entire point of watching it.
  const LO = 30;
  const HI = 100;
  const y = (v: number) => height - ((Math.min(Math.max(v, LO), HI) - LO) / (HI - LO)) * height;

  const n = Math.max(pts.length - 1, 1);
  const d = pts
    .map((s, i) => `${i === 0 ? "M" : "L"}${(i / n) * width},${y(s.c as number)}`)
    .join(" ");
  const area = pts.length > 1 ? `${d} L${width},${height} L0,${height} Z` : "";

  return (
    <span className="flex items-center gap-3">
      <span className="flex items-baseline gap-1.5 tabular-nums">
        <span className="text-[15px] font-semibold" style={{ color: hue }}>
          {c ?? "—"}°
        </span>
        {last?.u !== null && last?.u !== undefined && (
          // "GPU 98%" left it ambiguous — percent of what? It is how BUSY the GPU is
          // (Denis asked, 2026-08-09), so the word does the work the symbol could not.
          <span className="text-[11.5px] text-soft/60">{last.u}% busy</span>
        )}
        {!!last?.w && <span className="text-[11.5px] text-soft/60">{last.w} W</span>}
      </span>

      {pts.length > 1 && (
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          className="overflow-visible"
          aria-label={`GPU temperature, now ${c} degrees`}
        >
          {/* The one gridline worth drawing: where the fan becomes audible. It is the only
              threshold you can hear. */}
          <line
            x1="0"
            x2={width}
            y1={y(FAN_AUDIBLE)}
            y2={y(FAN_AUDIBLE)}
            stroke="var(--color-line)"
            strokeWidth="1"
            strokeDasharray="2 3"
          />
          {area && <path d={area} fill={hue} opacity="0.10" />}
          <path d={d} fill="none" stroke={hue} strokeWidth="1.5" strokeLinejoin="round" />
          <circle cx={width} cy={y(c as number)} r="2.5" fill={hue} />
        </svg>
      )}
    </span>
  );
}
