"use client";

import type { GpuSample } from "./types";

/** Thresholds are Apple Silicon's own behaviour, not round numbers: sustained work sits in
 *  the sixties, the fan becomes audible around the seventies, and the nineties is where the
 *  SoC pulls its clocks back — which the ETA would otherwise report as an unexplained
 *  slowdown. */
export function hueFor(c: number | null | undefined): string {
  if (c === undefined || c === null) return "var(--color-soft)";
  if (c >= 90) return "#C0392B";
  if (c >= 75) return "var(--color-accent)";
  if (c >= 60) return "var(--color-soft)";
  return "var(--color-good)";
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
          <span className="text-[11.5px] text-soft/60">GPU {last.u}%</span>
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
          {/* 75°C, where the fan becomes audible — the only gridline worth drawing, because
              it is the one you can hear. */}
          <line
            x1="0"
            x2={width}
            y1={y(75)}
            y2={y(75)}
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
