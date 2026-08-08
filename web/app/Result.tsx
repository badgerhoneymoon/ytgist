"use client";

import { useState } from "react";
import type { Gist, Takeaway } from "./types";

/** The reading surface.
 *
 *  THE RAIL is the structural idea: a 60px left column carries the step number AND its
 *  timestamp, right-aligned to a shared edge, while every piece of prose on the page —
 *  header, title, TL;DR, bodies — starts at one single left edge. Metadata lives outside
 *  the reading column instead of interrupting it, which is what makes a numbered argument
 *  scan like an argument rather than a list.
 *
 *  Earned the hard way, in order:
 *   1. The bold headline must STATE THE POINT — skimming only headlines gets the argument.
 *   2. Bodies keep their DEPTH in short sentences. Cutting content was the wrong axis:
 *      "too concise… almost impossible to figure out".
 *   3. Evidence sits AFTER the body, never between headline and body.
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
    <article className="mt-13" style={{ animation: "rise .5s var(--ease-out-expo)" }}>
      <h2 className="col-start-2 text-[30px] font-semibold leading-[1.16] tracking-[-0.02em]">
        {gist.title}
      </h2>

      {gist.tldr && (
        <p className="prose-serif col-start-2 mt-6 border-l-[3px] border-accent bg-accent/[0.05]
                      py-3.5 pl-4 pr-3 font-serif text-[20px] leading-[1.5] text-ink">
          {gist.tldr}
        </p>
      )}

      <ol className="col-span-full mt-13">
        {gist.takeaways.map((t, i) => (
          <Step key={i} n={i + 1} t={t} videoId={gist.videoId} />
        ))}
      </ol>

      {/* The ONLY element allowed to break the left edge into the rail — that single
          deliberate misalignment is what visually terminates the article. */}
      <footer className="col-span-full mt-20 border-t border-line pt-6">
        <div className="flex h-1.5 overflow-hidden rounded-full bg-line">
          {Object.entries(gist.timings).map(([k, v]) => (
            <div
              key={k}
              title={`${k} ${v}s`}
              className="transition-[width] duration-500"
              style={{ width: `${(v / total) * 100}%`, background: COLORS[k] ?? "#999" }}
            />
          ))}
        </div>

        <div className="mt-3.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] font-medium text-soft">
          {Object.entries(gist.timings).map(([k, v]) => (
            <span key={k} className="flex items-center gap-1.5">
              <i
                className="inline-block h-2 w-2 rounded-[2px]"
                style={{ background: COLORS[k] ?? "#999" }}
              />
              {k} <b className="font-semibold text-ink">{v}s</b>
            </span>
          ))}
          <span className="ml-auto">
            {total.toFixed(1)}s
            {gist.duration > 0 &&
              ` · ${Math.round(gist.duration / total)}× faster than watching`}
          </span>
        </div>

        {/* An ABSENT phase is information: without this line the bar reads as broken. */}
        {gist.cached && (
          <p className="mt-2 text-[13px] text-soft">
            transcript was cached — download and transcription skipped
          </p>
        )}

        <button
          onClick={() => download(gist)}
          className="mt-6 mr-2 rounded-lg border border-line px-3.5 py-2 text-[13px] font-medium text-soft
                     transition-colors duration-150 hover:border-ink hover:text-ink
                     focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Download .md
        </button>
        <button
          onClick={onRegenerate}
          className="mt-6 rounded-lg border border-line px-3.5 py-2 text-[13px] font-medium text-soft
                     transition-colors duration-150 hover:border-ink hover:text-ink
                     focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Regenerate
        </button>
      </footer>
    </article>
  );
}

/** The summary as a markdown file — headline, body, and a clickable timestamp per step,
 *  so it drops straight into notes without losing the links back to the video. */
function download(gist: Gist) {
  const lines = [`# ${gist.title}`, ""];
  if (gist.tldr) lines.push(`> ${gist.tldr}`, "");
  gist.takeaways.forEach((t, i) => {
    const link = t.seconds !== null
      ? ` — [${t.stamp}](https://youtu.be/${gist.videoId}?t=${t.seconds})` : "";
    lines.push(`## ${i + 1}. ${t.headline}${link}`, "", t.body, "");
    if (t.evidence) lines.push(`> ${t.evidence}…`, "");
  });
  lines.push("---", `Source: https://youtu.be/${gist.videoId}`);
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${gist.title.slice(0, 60).replace(/[^\p{L}\p{N} .-]/gu, "")}.md`;
  a.click();
  URL.revokeObjectURL(a.href);
}

const COLORS: Record<string, string> = {
  "read info": "#8E877C",
  download: "#6C8EA4",
  transcribe: "#4E8C6A",
  "model load": "#B08A3E",
  summarise: "#C2571A",
};

function Step({ n, t, videoId }: { n: number; t: Takeaway; videoId: string }) {
  // OPEN BY DEFAULT (Denis, 2026-08-08). The evidence is the reason to trust the claim;
  // hiding it behind a click made the summary something you take on faith, which is the
  // opposite of the point. Collapsing stays available for when you just want the spine.
  const [open, setOpen] = useState(true);

  return (
    <li className="group mb-13 grid grid-cols-[3.75rem_minmax(0,1fr)] last:mb-0
                   max-sm:grid-cols-[minmax(0,1fr)]">
      {/* RAIL: number over timestamp, right-aligned to a shared edge. Both are metadata,
          so both live outside the reading column. */}
      <div className="col-start-1 flex flex-col items-end pr-3.5 pt-0.5
                      max-sm:mb-1 max-sm:flex-row max-sm:items-baseline max-sm:gap-2.5 max-sm:pr-0">
        <span className="text-[13px] font-semibold leading-none text-soft transition-colors
                         duration-150 group-hover:text-accent">
          {n}
        </span>
        {t.stamp && t.seconds !== null && (
          <a
            href={`https://youtu.be/${videoId}?t=${t.seconds}`}
            target="_blank"
            rel="noopener"
            title="open at this moment"
            className="mt-2 text-[12.5px] font-medium leading-none text-soft transition-colors max-sm:mt-0
                       duration-150 hover:text-accent focus-visible:outline-2
                       focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            {t.stamp}
          </a>
        )}
      </div>

      <div className="col-start-2 max-sm:col-start-1">
        <h3 className="text-[18px] font-semibold leading-[1.3] tracking-[-0.006em] text-ink">
          {t.headline}
        </h3>

        <p className="prose-serif mt-3 font-serif text-[17.5px] leading-[1.62] text-body">
          {t.body}
        </p>

        {t.evidence && (
          <>
            <button
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              className="mt-3 text-[11.5px] font-semibold uppercase tracking-[0.08em] text-soft
                         transition-colors duration-150 hover:text-ink
                         focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              {open ? "hide source" : "what was said"}
            </button>

            {/* The grid-rows 0fr→1fr trick animates to CONTENT HEIGHT without measuring
                it — no max-height guess that clips long quotes or lags on short ones. */}
            <div
              className="grid transition-[grid-template-rows] duration-[220ms]"
              style={{
                gridTemplateRows: open ? "1fr" : "0fr",
                transitionTimingFunction: "var(--ease-out-expo)",
              }}
            >
              <div className="overflow-hidden">
                <blockquote
                  className="prose-serif mt-2.5 border-l-2 border-line bg-ink/[0.025] py-3 pl-4 pr-3
                             font-serif text-[15px] leading-[1.5] text-body"
                >
                  {t.evidence}…
                </blockquote>
              </div>
            </div>
          </>
        )}
      </div>
    </li>
  );
}
