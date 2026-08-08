import type { Stage } from "./types";

/** Named stages, not a bare percentage. "40%" tells you nothing; "transcribing, with
 *  checking and downloading already ticked" tells you where you are and what is left. */
const STEPS: { key: Stage; label: string }[] = [
  { key: "check", label: "reading video info" },
  { key: "download", label: "downloading audio" },
  { key: "transcribe", label: "transcribing" },
  { key: "summarise", label: "summarising" },
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

  return (
    <div className="mt-9">
      <div className="h-1 overflow-hidden rounded-full bg-line">
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-700 ease-out"
          style={{ width: `${pct}%` }}
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
                "flex items-center gap-1.5 rounded-full border px-3 py-1 text-[13px] transition-all duration-300",
                done && "border-good/40 bg-good/10 text-good",
                now && "border-accent bg-accent font-semibold text-canvas",
                !done && !now && "border-line text-soft/60",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              {done && <span className="text-[11px] font-bold">✓</span>}
              {now && (
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
              )}
              {s.label}
            </span>
          );
        })}
      </div>

      {msg && <p className="mt-3 text-[14px] text-soft">{msg}…</p>}
    </div>
  );
}
