"use client";

import { useState } from "react";
import { ChevronDown, Loader2, RefreshCw, RotateCcw } from "lucide-react";
import type { Gist, Takeaway } from "./types";
import Cited from "./Cited";
import { parseCited } from "./parse";

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
  onRetranscribe,
}: {
  gist: Gist;
  onRegenerate: () => void;
  onRetranscribe: () => void;
}) {
  const total = Object.values(gist.timings).reduce((a, b) => a + b, 0);

  return (
    <article className="mt-13" style={{ animation: "rise .5s var(--ease-out-expo)" }}>
      <h2 className="col-start-2 text-[33px] font-semibold leading-[1.14] tracking-[-0.02em]">
        {gist.title}
      </h2>

      {gist.tldr && (
        <p className="prose-serif col-start-2 mt-6 border-l-[3px] border-accent bg-accent/[0.05]
                      py-3.5 pl-4 pr-3 font-serif text-[21px] leading-[1.52] text-ink">
          {gist.tldr}
        </p>
      )}

      <ol className="col-span-full mt-13">
        {gist.takeaways.map((t, i) => (
          <Step
            key={i}
            n={i + 1}
            t={t}
            videoId={gist.videoId}
            // The step's span is from its own timestamp to the NEXT one — the argument's
            // own structure decides the window, not a guess about how much to read.
            until={gist.takeaways[i + 1]?.seconds ?? null}
          />
        ))}
      </ol>

      {/* The ONLY element allowed to break the left edge into the rail — that single
          deliberate misalignment is what visually terminates the article. */}
      <footer className="col-span-full mt-20 border-t border-line pt-6">
        {/* A summary opened from the library did NO work, so it has no timings — and a
            cost breakdown of nothing rendered as an empty bar reading "0.0s · Infinity×
            faster than watching". Zero work is its own state, not a degenerate case of
            the chart. */}
        {total > 0 ? (
          <>
            <div className="flex h-1.5 overflow-hidden rounded-full bg-line">
              {Object.entries(gist.timings).map(([k, v]) => (
                <div
                  key={k}
                  title={`${k} ${dur(v)}`}
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
                  {k} <b className="font-semibold text-ink">{dur(v)}</b>
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
          </>
        ) : (
          <p className="text-[13px] text-soft">
            opened from your library — nothing had to be recomputed
          </p>
        )}

        {/* Icons because two bordered text pills in the same weight read as one grey
            smear (Denis: "very pale and faceless"). The icon carries the verb, so the
            label can stay short and the two actions stop looking interchangeable. */}
        <div className="mt-6 flex flex-wrap gap-2">
          <button
            onClick={onRegenerate}
            title="new summary from the transcript already on disk"
            className="group flex items-center gap-2 rounded-lg border border-line px-3.5 py-2
                       text-[13px] font-medium text-ink transition-colors duration-150
                       hover:border-accent hover:bg-accent/[0.06] hover:text-accent
                       focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <RefreshCw size={14} strokeWidth={2} className="text-soft group-hover:text-accent" />
            New summary
          </button>
          <button
            onClick={onRetranscribe}
            title="download and transcribe the audio again, then summarise"
            className="group flex items-center gap-2 rounded-lg border border-line px-3.5 py-2
                       text-[13px] font-medium text-soft transition-colors duration-150
                       hover:border-ink hover:text-ink
                       focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <RotateCcw size={14} strokeWidth={2} className="text-soft/70 group-hover:text-ink" />
            Re-transcribe
          </button>
        </div>
      </footer>
    </article>
  );
}

/** Seconds are the wrong unit past about a minute — "367.4s" makes you do arithmetic to
 *  find out it is six minutes (Denis, 2026-08-08). Sub-minute keeps one decimal, because
 *  at that scale the tenths are the interesting part. */
function dur(secs: number): string {
  if (secs < 60) return `${secs < 10 ? secs.toFixed(1) : Math.round(secs)}s`;
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.round(secs % 60);
  if (h) return `${h}h ${m}m`;
  return s ? `${m}m ${s}s` : `${m}m`;
}

const COLORS: Record<string, string> = {
  "read info": "#8E877C",
  download: "#6C8EA4",
  transcribe: "#4E8C6A",
  "model load": "#B08A3E",
  summarise: "#C2571A",
};

const ENGINE = "http://127.0.0.1:8765";

function Step({
  n,
  t,
  videoId,
  until,
}: {
  n: number;
  t: Takeaway;
  videoId: string;
  until: number | null;
}) {
  // OPEN BY DEFAULT (Denis, 2026-08-08). The evidence is the reason to trust the claim;
  // hiding it behind a click made the summary something you take on faith, which is the
  // opposite of the point. Collapsing stays available for when you just want the spine.
  const [open, setOpen] = useState(true);

  // MORE DETAIL, on demand, from this step's own passage. A takeaway is deliberately three
  // short sentences; sometimes you want the number, the name or the exception it dropped.
  // "" means asked-and-there-is-nothing, which is a real answer the model is allowed to
  // give — the alternative is padding, and padding here is invention.
  const [more, setMore] = useState<string | null>(t.expansion);
  const [loading, setLoading] = useState(false);

  const expand = async () => {
    if (loading || more !== null || t.seconds === null) return;
    setLoading(true);
    try {
      const r = await fetch(`${ENGINE}/api/expand`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video: videoId, start: t.seconds, end: until,
          headline: t.headline, body: t.body,
        }),
      });
      const d = await r.json();
      setMore(d.error ? `` : (d.text ?? ""));
    } catch {
      setMore("");
    } finally {
      setLoading(false);
    }
  };

  return (
    <li className="group mb-13 grid grid-cols-[3.75rem_minmax(0,1fr)] last:mb-0
                   max-sm:grid-cols-[minmax(0,1fr)] [&>div]:after:block
                   [&>div]:after:clear-both [&>div]:after:content-['']">
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
        <h3 className="text-[19.5px] font-semibold leading-[1.28] tracking-[-0.006em] text-ink">
          {t.headline}
        </h3>

        {/* FLOATED, not stacked. A block image between headline and body would break the
            argument in half every time one resolves; floating lets the prose close around
            it, so a step with a picture and a step without still read as the same object.
            Small on purpose — these are logos and portraits that identify a subject, not
            illustrations that carry meaning. */}
        {t.image && (
          <figure className="float-right ml-5 mt-3 mb-1 w-[96px] max-sm:w-[76px]">
            <a href={t.image.href} target="_blank" rel="noopener" title={t.image.label}>
              <img
                src={t.image.src}
                alt={t.image.label}
                loading="lazy"
                className="w-full rounded-md border border-line bg-canvas object-contain
                           transition-opacity duration-150 hover:opacity-85"
              />
            </a>
            <figcaption className="mt-1.5 text-[11px] leading-[1.35] text-soft/70">
              {t.image.query}
            </figcaption>
          </figure>
        )}

        <p className="prose-serif mt-3 font-serif text-[19px] leading-[1.6] text-body">
          {t.body}
        </p>

        {/* ONE ACTION ROW. These were stacked — two identical uppercase micro-labels,
            "MORE DETAIL" directly above "HIDE SOURCE" — which read as a pile of section
            headings rather than two things you can press (Denis, 2026-08-08). They are
            both actions on this step, so they sit on one line and are told apart by what
            they do: expanding ADDS text and gets the accent; the quote toggle only hides
            something already on screen and stays quiet. */}
        {(t.seconds !== null || t.evidence) && (
          <div className="mt-3.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            {t.seconds !== null && more === null && (
              <button
                onClick={expand}
                disabled={loading}
                className="group/exp flex items-center gap-1.5 rounded-full border border-accent/25
                           bg-accent/[0.06] px-2.5 py-1 text-[11.5px] font-semibold text-accent
                           transition-colors duration-150 hover:bg-accent/[0.13]
                           disabled:opacity-60 focus-visible:outline-2
                           focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {loading ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <ChevronDown
                    size={12}
                    strokeWidth={2.5}
                    className="transition-transform duration-200 group-hover/exp:translate-y-[1px]"
                  />
                )}
                {loading ? "reading the passage…" : "More detail"}
              </button>
            )}

            {t.evidence && (
              <button
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                className="text-[12.5px] text-soft transition-colors duration-150 hover:text-ink
                           focus-visible:outline-2 focus-visible:outline-offset-2
                           focus-visible:outline-accent"
              >
                {open ? "hide the quote" : "what was said"}
              </button>
            )}
          </div>
        )}

        {more !== null &&
          (more ? (
            <div
              className="mt-3.5 border-l-2 border-accent/30 pl-4"
              style={{ animation: "rise .35s var(--ease-out-expo)" }}
            >
              <Cited paragraphs={parseCited(more)} className="text-[17px]" />
            </div>
          ) : (
            <p className="mt-2.5 text-[12.5px] text-soft/80">
              nothing further in this passage — the summary already has it
            </p>
          ))}

        {t.evidence && (
          /* clear-right so a floated image narrows the BODY (where wrapping reads as
             intentional) but never the quote, whose ragged right edge against a
             half-width block just looks broken. */
          <div className="clear-right">
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
                             font-serif text-[16.5px] leading-[1.52] text-body"
                >
                  {t.evidence}…
                </blockquote>
              </div>
            </div>
          </div>
        )}
      </div>
    </li>
  );
}
