"use client";

import { useEffect, useState } from "react";

export type HistoryRow = {
  id: string;
  title: string;
  duration: number;
  segments: number;
  has_summary: boolean;
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
}: {
  onPick: (id: string) => void;
  refreshKey: number;
}) {
  const [rows, setRows] = useState<HistoryRow[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    fetch(`${ENGINE}/api/history`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setRows)
      .catch(() => {});
  }, [refreshKey]);

  if (!rows.length) return null;

  const shown = open ? rows : rows.slice(0, 4);

  return (
    <section className="mt-10">
      <h2 className="text-[11.5px] font-semibold uppercase tracking-[0.1em] text-soft">
        Library · {rows.length}
      </h2>

      <ul className="mt-3 divide-y divide-line/70 border-y border-line/70">
        {shown.map((r) => (
          <li key={r.id}>
            <button
              onClick={() => onPick(r.id)}
              className="group flex w-full items-center gap-3.5 py-2.5 text-left
                         transition-colors duration-150 hover:bg-ink/[0.02]
                         focus-visible:outline-2 focus-visible:outline-offset-[-2px]
                         focus-visible:outline-accent"
            >
              <img
                src={`https://i.ytimg.com/vi/${r.id}/default.jpg`}
                alt=""
                className="h-9 w-16 shrink-0 rounded-md bg-line object-cover"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[14.5px] leading-snug text-ink">
                  {r.title}
                </span>
                <span className="mt-0.5 block text-[12.5px] text-soft">
                  {Math.round(r.duration / 60)} min · {r.segments} segments
                  {r.has_summary ? " · summary saved" : " · transcript only"}
                </span>
              </span>
              <span
                className="shrink-0 text-[12px] font-medium text-soft opacity-0 transition-opacity
                           duration-150 group-hover:opacity-100"
              >
                {r.has_summary ? "open" : "summarise"} →
              </span>
            </button>
          </li>
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
