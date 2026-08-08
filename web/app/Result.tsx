"use client";

import { useState } from "react";
import type { Gist, Takeaway } from "./types";

/** The reading surface. Three rules, each from a round of Denis's feedback:
 *
 *  1. The numbered headline carries the point — skimming only those gets the argument.
 *  2. The body keeps its DEPTH but in short sentences. Round 2 cut content and that was
 *     the wrong axis: "too concise… almost impossible to figure out".
 *  3. Evidence sits AFTER the body, never between the headline and it — putting it in
 *     the middle is half of why the first version read choppy.
 */
export default function Result({
  gist,
  onRegenerate,
}: {
  gist: Gist;
  onRegenerate: () => void;
}) {
  const total = Object.values(gist.timings).reduce((a, b) => a + b, 0);

  return (
    <article className="mt-12 animate-[fadeUp_.5s_ease-out]">
      <style>{`@keyframes fadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}`}</style>

      <h2 className="text-[22px] font-semibold leading-snug tracking-[-0.01em]">
        {gist.title}
      </h2>

      {gist.tldr && (
        <p className="mt-5 border-l-[3px] border-accent bg-accent/[0.06] px-4 py-3.5 text-[17px] leading-relaxed text-body">
          {gist.tldr}
        </p>
      )}

      <ol className="mt-9 space-y-8">
        {gist.takeaways.map((t, i) => (
          <Step key={i} n={i + 1} t={t} videoId={gist.videoId} />
        ))}
      </ol>

      <footer className="mt-12 border-t border-line pt-5">
        <div className="flex h-1.5 overflow-hidden rounded-full bg-line">
          {Object.entries(gist.timings).map(([k, v]) => (
            <div
              key={k}
              title={`${k} ${v}s`}
              style={{ width: `${(v / total) * 100}%`, background: COLORS[k] ?? "#999" }}
            />
          ))}
        </div>
        <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-soft">
          {Object.entries(gist.timings).map(([k, v]) => (
            <span key={k} className="flex items-center gap-1.5">
              <i
                className="inline-block h-2 w-2 rounded-[2px]"
                style={{ background: COLORS[k] ?? "#999" }}
              />
              {k} <b className="font-semibold tabular-nums text-ink">{v}s</b>
            </span>
          ))}
          <span className="ml-auto tabular-nums">
            {total.toFixed(1)}s
            {gist.duration > 0 && ` · ${Math.round(gist.duration / total)}× faster than watching`}
          </span>
        </div>
        {/* An ABSENT phase is information: without this the bar looks broken. */}
        {gist.cached && (
          <p className="mt-2 text-[13px] text-soft">
            transcript was cached — download and transcription skipped
          </p>
        )}
        <button
          onClick={onRegenerate}
          className="mt-5 rounded-lg border border-line px-3.5 py-1.5 text-[13px] text-soft
                     transition hover:border-ink hover:text-ink"
        >
          Regenerate
        </button>
      </footer>
    </article>
  );
}

const COLORS: Record<string, string> = {
  "read info": "#8E877C",
  download: "#6C8EA4",
  transcribe: "#4E8C6A",
  "model load": "#B08A3E",
  summarise: "#C2571A",
};

function Step({ n, t, videoId }: { n: number; t: Takeaway; videoId: string }) {
  const [open, setOpen] = useState(false);

  return (
    <li className="group relative pl-11">
      <span
        className="absolute left-0 top-0.5 flex h-7 w-7 items-center justify-center rounded-full
                   border border-line text-[13px] font-semibold tabular-nums text-soft
                   transition group-hover:border-accent group-hover:text-accent"
      >
        {n}
      </span>

      <h3 className="text-[17px] font-semibold leading-snug tracking-[-0.01em]">
        {t.headline}
        {t.stamp && t.seconds !== null && (
          <a
            href={`https://youtu.be/${videoId}?t=${t.seconds}`}
            target="_blank"
            rel="noopener"
            className="ml-2 align-middle text-[12px] font-normal tabular-nums text-soft
                       underline decoration-dotted underline-offset-4 transition hover:text-accent"
          >
            {t.stamp}
          </a>
        )}
      </h3>

      <p className="mt-1.5 text-[15.5px] leading-[1.7] text-body">{t.body}</p>

      {t.evidence && (
        <>
          <button
            onClick={() => setOpen((v) => !v)}
            className="mt-2 text-[12.5px] text-soft underline decoration-dotted
                       underline-offset-4 transition hover:text-ink"
          >
            {open ? "hide what was said" : "what was said"}
          </button>
          {open && (
            <blockquote
              className="mt-2 rounded-r-lg border-l-2 border-line bg-ink/[0.03] px-4 py-3
                         text-[14px] leading-relaxed text-body animate-[fadeUp_.25s_ease-out]"
            >
              {t.evidence}…
            </blockquote>
          )}
        </>
      )}
    </li>
  );
}
