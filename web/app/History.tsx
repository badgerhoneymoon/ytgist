"use client";

import { useEffect, useState } from "react";

export type HistoryRow = {
  id: string;
  title: string;
  duration: number;
  segments: number;
  has_summary: boolean;
  has_en: boolean;
  has_native: boolean;
  is_english: boolean;
  at: number;
};

const ENGINE = "http://127.0.0.1:8765";

/** Everything ytgist has ever transcribed, newest first.
 *
 *  It exists because the expensive artefact is the TRANSCRIPT and it was already being
 *  kept — but there was no way to get back to it. Loading a row costs nothing when a
 *  summary is saved, and skips the download+transcribe entirely when it isn't.
 */
export default function History({
  onPick,
  refreshKey,
  onCount,
}: {
  onPick: (id: string, native: boolean) => void;
  refreshKey: number;
  onCount?: (n: number) => void;
}) {
  const [rows, setRows] = useState<HistoryRow[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    fetch(`${ENGINE}/api/history`)
      .then((r) => (r.ok ? r.json() : []))
      .then((r: HistoryRow[]) => {
        setRows(r);
        onCount?.(r.length);
      })
      .catch(() => {});
    // onCount is a setState fn — stable — so refreshKey alone is the right trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  if (!rows.length) return null;

  const shown = open ? rows : rows.slice(0, 4);

  return (
    <section id="library" className="mt-10 scroll-mt-6">
      <h2 className="text-[11.5px] font-semibold uppercase tracking-[0.1em] text-soft">
        Library · {rows.length}
      </h2>

      <ul className="mt-3 divide-y divide-line/70 border-y border-line/70">
        {shown.map((r) => (
          <Row key={r.id} r={r} onPick={onPick} />
        ))}
      </ul>

      {rows.length > 4 && (
        <button
          onClick={() => setOpen((v) => !v)}
          className="mt-2.5 text-[12.5px] text-soft transition-colors duration-150 hover:text-ink"
        >
          {open ? "show fewer" : `show all ${rows.length}`}
        </button>
      )}
    </section>
  );
}

/** One saved video, in one of two shapes.
 *
 *  ONE VERSION → THE WHOLE ROW IS THE BUTTON. Chips exist so you can pick a language; when
 *  an English video has no second version to offer, a chip is just a smaller target
 *  guarding a decision that does not exist (Denis, 2026-08-08).
 *
 *  TWO VERSIONS → chips, and the row itself is inert. Making both clickable is what read as
 *  broken the first time round — "when I hover it's not hoverable, it's part of the same
 *  strip". Exactly one thing is pressable in each shape.
 */
function Row({
  r,
  onPick,
}: {
  r: HistoryRow;
  onPick: (id: string, native: boolean) => void;
}) {
  const choose = !r.is_english; // a Russian video can exist in English AND in the original

  const body = (
    <>
      <img
        src={`https://i.ytimg.com/vi/${r.id}/default.jpg`}
        alt=""
        className="h-9 w-16 shrink-0 rounded-md bg-line object-cover"
      />
      <span className="min-w-0 flex-1 text-left">
        <span className="block truncate text-[14.5px] leading-snug text-ink">{r.title}</span>
        <span className="mt-0.5 block text-[12.5px] text-soft">
          {Math.round(r.duration / 60)} min
          {!r.has_summary && " · not summarised yet"}
        </span>
      </span>
    </>
  );

  if (!choose) {
    return (
      <li>
        <button
          onClick={() => onPick(r.id, false)}
          title={r.has_en ? "open this summary" : "summarise this video"}
          className="group flex w-full items-center gap-3.5 rounded-lg px-2 py-2.5
                     transition-colors duration-150 hover:bg-ink/[0.035]
                     focus-visible:outline-2 focus-visible:outline-offset-[-2px]
                     focus-visible:outline-accent"
        >
          {body}
          {/* Decoration on a row that IS the button, so it can never look separately
              clickable — it only leans in on hover. */}
          <span
            aria-hidden
            className="shrink-0 pr-1 text-[15px] text-soft/40 transition-all duration-200
                       group-hover:translate-x-0.5 group-hover:text-accent"
            style={{ transitionTimingFunction: "var(--ease-out-expo)" }}
          >
            →
          </span>
        </button>
      </li>
    );
  }

  return (
    <li className="flex items-center gap-3.5 py-2.5 pl-2 pr-1">
      {body}
      {/* A FILLED chip means that version is on disk and opens instantly; an outlined one
          means it does not exist yet and clicking makes it. So the chips show what you have
          AND are how you get what you don't, without a second control to explain it. */}
      <span className="flex shrink-0 items-center gap-1.5">
        <Chip
          label="EN"
          title={r.has_en ? "open the English version" : "make the English version"}
          ready={r.has_en}
          onClick={() => onPick(r.id, false)}
        />
        <Chip
          label="Original"
          title={
            r.has_native
              ? "open the original-language version"
              : "make it in the video's own language"
          }
          ready={r.has_native}
          onClick={() => onPick(r.id, true)}
        />
      </span>
    </li>
  );
}

function Chip({
  label,
  title,
  ready,
  onClick,
}: {
  label: string;
  title: string;
  ready: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={[
        "rounded-full border px-2.5 py-1 text-[12px] font-medium",
        "transition-colors duration-150",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        ready
          ? "border-accent/25 bg-accent/[0.09] text-accent hover:bg-accent/[0.16]"
          : "border-dashed border-line text-soft/70 hover:border-ink/40 hover:text-ink",
      ].join(" ")}
    >
      {label}
    </button>
  );
}
