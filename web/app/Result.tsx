"use client";

import { useEffect, useState } from "react";
import {
  Check,
  ChevronDown,
  Copy,
  Image as ImageIcon,
  Loader2,
  Maximize2,
  RefreshCw,
  RotateCcw,
} from "lucide-react";
import type { Gist, Takeaway } from "./types";
import Cited from "./Cited";
import { parseCited } from "./parse";
import { ENGINE } from "./engine";

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
  native,
  onRegenerate,
  onRetranscribe,
  onFindShots,
  onCancelShots,
  shotBusy = false,
  shotMsg = "",
}: {
  gist: Gist;
  native: boolean;
  onRegenerate: () => void;
  onRetranscribe: () => void;
  onFindShots?: () => void;
  onCancelShots?: () => void;
  shotBusy?: boolean;
  shotMsg?: string;
}) {
  const total = Object.values(gist.timings).reduce((a, b) => a + b, 0);

  // Detail expanded in THIS session lives inside each Step. Copying from `gist` alone
  // would silently drop a passage he had just pulled up — so expansions are reported
  // back here, and the clipboard carries what is actually on screen.
  const [expanded, setExpanded] = useState<Record<number, string>>({});

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
            native={native}
            onExpanded={(text) => setExpanded((e) => ({ ...e, [i]: text }))}
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

            </div>

            {gist.duration > 0 && (
              <>
                <p className="mt-7 flex items-baseline gap-2.5">
                  <span className="text-[34px] font-semibold leading-none tracking-[-0.02em] text-ink">
                    {Math.round(gist.duration / total)}×
                  </span>
                  <span className="text-[15px] leading-tight text-soft">
                    faster than watching it
                  </span>
                </p>
                <p className="mt-1.5 text-[13.5px] text-soft/80">
                  {Math.round(gist.duration / 60)} min of video, read in {dur(total)}
                </p>
              </>
            )}

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
        {/* THE SCREENSHOT PASS SAYS WHAT HAPPENED, always. "Nothing worth showing" is a
            real answer — most videos are a person talking — and it has to be told apart
            from a search that broke, or a failed download hides behind a tidy sentence. */}
        {onFindShots && (
          <div className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-2">
            {shotBusy ? (
              <>
                <span className="flex items-center gap-2 text-[13px] font-medium text-accent">
                  <Loader2 size={14} className="animate-spin" />
                  looking for screenshots{shotMsg ? ` · ${shotMsg}` : ""}
                </span>
                {onCancelShots && (
                  <button
                    onClick={onCancelShots}
                    className="text-[12.5px] font-medium text-soft underline-offset-2 hover:underline"
                  >
                    stop
                  </button>
                )}
              </>
            ) : (
              <>
                <button
                  onClick={onFindShots}
                  title="let the model look through the video's thumbnails for a frame worth showing"
                  className="group flex items-center gap-2 rounded-lg border border-line px-3.5 py-2
                             text-[13px] font-medium text-ink transition-colors duration-150
                             hover:border-accent hover:bg-accent/[0.06] hover:text-accent
                             focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  <ImageIcon size={14} strokeWidth={2} className="text-soft group-hover:text-accent" />
                  {gist.framesOutcome ? "Look again" : "Find screenshots"}
                </button>
                {gist.framesOutcome && (
                  <span className="text-[12.5px] text-soft">{gist.framesOutcome}</span>
                )}
              </>
            )}
          </div>
        )}

        <div className="mt-6 flex flex-wrap gap-2">
          <CopyButton markdown={() => toMarkdown(gist, expanded)} />
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

/** The screenshot for one step, when there is one.
 *
 *  Only a FOUND frame draws anything. "Nothing here" is the ordinary answer on most
 *  videos and printing it fifteen times would be noise where the picture would have been;
 *  the footer already reports the search as a whole. A FAILURE does speak up, quietly,
 *  because a search that broke is not the same as a video with nothing in it — and the
 *  difference is invisible from the outside unless something says so.
 *
 *  The image is loaded from the engine, not the Next server: the frames live in the
 *  engine's cache, and it is the thing that knows where. Which also means these pictures
 *  exist on this Mac only — the reason Copy stays text.
 */
function Shot({ shot, videoId }: { shot: Takeaway["frame"]; videoId: string }) {
  const [open, setOpen] = useState(false);
  if (!shot) return null;
  if (shot.state === "failed") {
    return (
      <p className="mt-3 text-[12.5px] text-soft/70">
        couldn&rsquo;t fetch a screenshot for this step
      </p>
    );
  }
  if (shot.state !== "found") return null;
  const src = `${ENGINE}/api/frame?v=${videoId}&t=${shot.secs}`;
  return (
    <>
      {/* IN THE COLUMN IT IS AN ILLUSTRATION; FULL SIZE IT IS EVIDENCE. A slide or a
          terminal at reading-column width is a picture of text you cannot read — and a
          frame worth keeping is exactly one you want to read (Denis, 2026-10-02). So the
          click opens the 720p frame at the size of the window instead of leaving for
          YouTube, which is one line further down. */}
      <button
        onClick={() => setOpen(true)}
        title="see it full size"
        className="group/shot relative mt-4 block w-full overflow-hidden rounded-lg border
                   border-line focus-visible:outline-2 focus-visible:outline-offset-2
                   focus-visible:outline-accent"
      >
        {/* A plain <img>: served by the ENGINE on another port, which next/image would need
            configuring for and would gain nothing — 130 KB JPEGs already sized by the grab. */}
        <img src={src} alt="" loading="lazy" className="block w-full" />
        <span className="pointer-events-none absolute right-2 top-2 flex items-center gap-1
                         rounded-md bg-black/55 px-1.5 py-1 text-[11px] font-medium text-white
                         opacity-0 transition-opacity duration-150 group-hover/shot:opacity-100">
          <Maximize2 size={11} strokeWidth={2.5} />
          full size
        </span>
      </button>
      {open && (
        <Lightbox src={src} videoId={videoId} secs={shot.secs} onClose={() => setOpen(false)} />
      )}
    </>
  );
}

/** The screenshot, as large as the window allows.
 *
 *  Escape and a click anywhere outside close it: a viewer you have to aim at to leave is a
 *  trap. The moment stays reachable from inside — looking closely at a frame is exactly
 *  when you decide to go and watch it.
 */
function Lightbox({
  src,
  videoId,
  secs,
  onClose,
}: {
  src: string;
  videoId: string;
  secs: number;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // The page behind must not scroll while this is open — scrolling what you cannot see
    // is how you lose your place in the argument.
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <div
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-3 bg-black/80
                 p-4 sm:p-8"
    >
      <img
        src={src}
        alt=""
        onClick={(e) => e.stopPropagation()}
        className="max-h-[85vh] max-w-full rounded-lg object-contain shadow-2xl"
      />
      <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-[12.5px] text-white/70">
        <a
          href={`https://youtu.be/${videoId}?t=${secs}`}
          target="_blank"
          rel="noopener"
          onClick={(e) => e.stopPropagation()}
          className="underline-offset-2 hover:text-white hover:underline"
        >
          watch from {stampOf(secs)}
        </a>
        <span>click anywhere or press Esc to close</span>
      </div>
    </div>
  );
}

function stampOf(secs: number): string {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

/** THE WHOLE SUMMARY AS MARKDOWN — what you would want in your notes, not what the
 *  screen happens to contain.
 *
 *  Timestamps become real links, because a pasted "12:34" is dead text somewhere else and
 *  the link back into the video is half of why the summary is trustworthy. Evidence stays
 *  a blockquote and expanded detail is marked as such, so the model's claim and the
 *  speaker's own words never merge into one undifferentiated paragraph after a paste.
 */
function toMarkdown(gist: Gist, expanded: Record<number, string>): string {
  const out: string[] = [`# ${gist.title}`];
  if (gist.videoId) out.push(`https://youtu.be/${gist.videoId}`);
  if (gist.tldr) out.push(`**${gist.tldr}**`);

  gist.takeaways.forEach((t, i) => {
    const link =
      t.stamp && t.seconds !== null
        ? ` — [${t.stamp}](https://youtu.be/${gist.videoId}?t=${t.seconds})`
        : "";
    out.push(`## ${i + 1}. ${t.headline}${link}`);
    if (t.body) out.push(t.body);
    if (t.evidence) out.push(quote(t.evidence));
    // "" is asked-and-nothing — a real answer on screen, but nothing to paste.
    const more = expanded[i] ?? t.expansion;
    if (more) out.push(`*More detail:* ${more}`);
  });

  return out.join("\n\n") + "\n";
}

/** A multi-line quote needs the marker on EVERY line or the paste collapses into prose. */
function quote(s: string): string {
  return s
    .trim()
    .split("\n")
    .map((l) => `> ${l}`)
    .join("\n");
}

/** Copy, and SAY SO. A clipboard write is invisible — without the state change you press
 *  it twice and still do not know whether it worked. The failure has to be visible for the
 *  same reason: a browser can refuse the clipboard, and a button that silently does
 *  nothing is worse than one that admits it.
 */
function CopyButton({ markdown }: { markdown: () => string }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(markdown());
      setState("done");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2000);
  };

  return (
    <button
      onClick={copy}
      title="the whole summary as Markdown — timestamps as links, quotes intact"
      className="group flex items-center gap-2 rounded-lg border border-line px-3.5 py-2
                 text-[13px] font-medium text-ink transition-colors duration-150
                 hover:border-accent hover:bg-accent/[0.06] hover:text-accent
                 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {state === "done" ? (
        <Check size={14} strokeWidth={2.5} className="text-accent" />
      ) : (
        <Copy size={14} strokeWidth={2} className="text-soft group-hover:text-accent" />
      )}
      {state === "done" ? "Copied" : state === "failed" ? "Couldn't copy" : "Copy text"}
    </button>
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
  images: "#7A6E9E",
};


function Step({
  n,
  t,
  videoId,
  native,
  until,
  onExpanded,
}: {
  n: number;
  t: Takeaway;
  videoId: string;
  native: boolean;
  until: number | null;
  onExpanded: (text: string) => void;
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
          video: videoId, start: t.seconds, end: until, native,
          headline: t.headline, body: t.body,
        }),
      });
      const d = await r.json();
      const text = d.error ? `` : (d.text ?? "");
      setMore(text);
      onExpanded(text);
    } catch {
      setMore("");
    } finally {
      setLoading(false);
    }
  };

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
        <h3 className="text-[19.5px] font-semibold leading-[1.28] tracking-[-0.006em] text-ink">
          {t.headline}
        </h3>

        <p className="prose-serif mt-3 font-serif text-[19px] leading-[1.6] text-body">
          {t.body}
        </p>

        <Shot shot={t.frame} videoId={videoId} />

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

          </div>
        )}

        {more !== null &&
          (more ? (
            <div
              className="mt-3.5 border-l-2 border-accent/30 pl-4"
              style={{ animation: "rise .35s var(--ease-out-expo)" }}
            >
              <Cited paragraphs={parseCited(more)} className="text-[19px]" />
            </div>
          ) : (
            <p className="mt-2.5 text-[12.5px] text-soft/80">
              nothing further in this passage — the summary already has it
            </p>
          ))}

        {t.evidence && (
          <div>
            <button
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              className="mt-4 block text-[12.5px] text-soft transition-colors duration-150
                         hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2
                         focus-visible:outline-accent"
            >
              {open ? "hide the quote" : "what was said"}
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
