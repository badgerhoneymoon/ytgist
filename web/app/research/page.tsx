"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { Check, Clock, Copy, Loader2, X } from "lucide-react";
import { ENGINE, switchEngine } from "../engine";
import { EngineLabel, useBusyGate } from "../BusyGate";
import { Shot } from "../Result";

/** Research mode: a topic → the ~10 most useful videos → one brief across all of them.
 *
 *  The engine does the work (research.py); this page starts a run, shows each video as it
 *  goes, lets you drop one, and renders the brief with every claim linked to its moment.
 */

type Pick = {
  id: string; title: string; channel: string; duration: number; views?: number;
  why: string; status: string; stage?: string; error?: string; fit?: string; ts?: number | null;
};
type Source = { k: number; id: string; title: string; channel: string; duration: number; ts?: number | null };
type Cite = { k: number; id: string; secs: number | null; stamp: string | null };
type Step = {
  headline: string; body: string; cites: Cite[]; said: { k: number; stamp: string; text: string }[];
  shot?: { k: number; id: string; secs: number } | null;
};
type Run = {
  id: string; topic: string; status: string; msg: string; created: number;
  picks: Pick[]; error?: string | null; searched?: number; queries?: string[];
  n?: number; subject?: string; wider_at?: number;
  report?: {
    markdown: string; sources: Source[]; at: number; unverified_dropped: number;
    unsupported_dropped?: number; takeaways_dropped?: number;
    removed?: { headline: string; k: number; id: string; secs: number; stamp: string; why: string; takeaway_left_out?: boolean }[];
    tldr?: string; takeaways?: Step[]; extras?: string;
  } | null;
};
type RunRow = { id: string; topic: string; status: string; created: number; videos: number; done: number };

const ACTIVE = ["searching", "picking", "summarising", "combining"];

function dur(s: number) {
  s = Math.round(s || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
}

/** **bold** and [label](https://youtu.be/…) only. Built as elements, never as HTML: the text
 *  comes from strangers' transcripts. */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|\[([^\]]+)\]\((https:\/\/youtu\.be\/[^)]+)\)/g;
  let last = 0, k = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<b key={k++} className="font-semibold text-ink">{m[1]}</b>);
    else out.push(
      <a key={k++} href={m[3]} target="_blank" rel="noopener noreferrer"
         className="whitespace-nowrap font-mono text-[12.5px] text-accent hover:underline">{m[2]}</a>);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function Brief({ md }: { md: string }) {
  const blocks: ReactNode[] = [];
  let items: string[] = [];
  const close = () => {
    if (items.length) {
      const its = items;
      blocks.push(
        <ol key={blocks.length} className="mt-3 list-decimal space-y-3.5 pl-5 text-[15.5px] leading-[1.6] text-body marker:text-soft">
          {its.map((t, i) => <li key={i} className="pl-1">{inline(t)}</li>)}
        </ol>);
      items = [];
    }
  };
  for (const raw of md.split("\n")) {
    const s = raw.trim();
    if (!s) continue;
    const num = s.match(/^\d+[.)]\s+(.*)/);
    if (s.startsWith("TL;DR")) {
      close();
      blocks.push(
        <p key={blocks.length} className="prose-serif rounded-xl border border-line bg-white/60 px-5 py-4 text-[17px] leading-[1.55] text-ink">
          {inline(s.slice(5).trim())}
        </p>);
    } else if (s.startsWith("## ")) {
      close();
      blocks.push(<h2 key={blocks.length} className="mt-9 text-[19px] font-semibold text-ink">{s.slice(3)}</h2>);
    } else if (num) {
      items.push(num[1]);
    } else if (items.length) {
      items[items.length - 1] += " " + s;
    } else {
      blocks.push(<p key={blocks.length} className="mt-3 text-[15.5px] text-body">{inline(s)}</p>);
    }
  }
  close();
  return <div>{blocks}</div>;
}

/** One takeaway, laid out exactly like a step of a single video's gist: number and moments
 *  on the rail, the headline that states the point, a few sentences, then what was said. */
function StepView({ n, t }: { n: number; t: Step }) {
  const [open, setOpen] = useState(true);
  return (
    <li className="group mb-13 grid grid-cols-[3.75rem_minmax(0,1fr)] last:mb-0 max-sm:grid-cols-[minmax(0,1fr)]">
      <div className="col-start-1 flex flex-col items-end gap-1.5 pr-3.5 pt-0.5 max-sm:mb-1 max-sm:flex-row max-sm:flex-wrap max-sm:items-baseline max-sm:gap-2.5 max-sm:pr-0">
        <span className="text-[13px] font-semibold leading-none text-soft transition-colors duration-150 group-hover:text-accent">{n}</span>
        {t.cites.map((c, i) => (
          <a key={i} href={`https://youtu.be/${c.id}${c.secs !== null ? `?t=${c.secs}` : ""}`} target="_blank" rel="noopener noreferrer"
             title="open at this moment"
             className="whitespace-nowrap text-[11.5px] font-medium leading-tight text-soft transition-colors duration-150 hover:text-accent">
            V{c.k}{c.stamp ? <span className="block text-right">{c.stamp}</span> : null}
          </a>
        ))}
      </div>
      <div className="col-start-2 max-sm:col-start-1">
        <h3 className="text-[19.5px] font-semibold leading-[1.28] tracking-[-0.006em] text-ink">{t.headline}</h3>
        <p className="prose-serif mt-3 font-serif text-[19px] leading-[1.6] text-body">{t.body}</p>
        {/* The same screenshot as a single video's step: click for the lightbox, with
            "watch from" inside it, rather than leaving for YouTube (Denis, 7 Oct). */}
        {t.shot && <Shot shot={{ state: "found", secs: t.shot.secs }} videoId={t.shot.id} />}
        {t.said.length > 0 && (
          <div>
            <button onClick={() => setOpen((v) => !v)} aria-expanded={open}
                    className="mt-4 block text-[12.5px] text-soft transition-colors duration-150 hover:text-ink">
              {open ? "hide what was said" : "what was said"}
            </button>
            <div className="grid transition-[grid-template-rows] duration-[220ms]"
                 style={{ gridTemplateRows: open ? "1fr" : "0fr", transitionTimingFunction: "var(--ease-out-expo)" }}>
              <div className="overflow-hidden">
                {t.said.map((q, i) => (
                  <blockquote key={i}
                    className="prose-serif mt-2.5 border-l-2 border-line bg-ink/[0.025] py-3 pl-4 pr-3 font-serif text-[16.5px] leading-[1.52] text-body">
                    <span className="mr-2 font-sans text-[11.5px] font-semibold text-soft">V{q.k} · {q.stamp}</span>
                    {q.text}…
                  </blockquote>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </li>
  );
}

/** Agree / disagree / only one says / watch first: useful, optional, so collapsed. */
function Extras({ md }: { md: string }) {
  const sections = md.split(/^## /m).map((p) => p.trim()).filter(Boolean).map((p) => {
    const [title, ...rest] = p.split("\n");
    const items = rest.map((l) => l.trim()).filter(Boolean).map((l) => l.replace(/^\d+[.)]\s+/, ""));
    return { title: title.trim(), items };
  });
  return (
    <div className="mt-14 border-t border-line">
      {sections.map((s) => (
        <details key={s.title} className="group/x border-b border-line">
          <summary className="flex cursor-pointer list-none items-center justify-between py-3.5 text-[15px] font-semibold text-ink">
            {s.title}
            <span className="text-[12px] font-normal text-soft group-open/x:hidden">{s.items.length} · show</span>
          </summary>
          <ol className="mb-5 list-decimal space-y-2.5 pl-5 text-[15.5px] leading-[1.6] text-body marker:text-soft">
            {s.items.map((t, i) => <li key={i} className="pl-1">{inline(t)}</li>)}
          </ol>
        </details>
      ))}
    </div>
  );
}

/** Where each video is, readable at a glance: an icon, a word, a tinted pill (Denis, 7 Oct). */
/** Copy the whole brief as Markdown, to paste into another AI. It is the engine's own .md
 *  (the same file "Open as page" is built from): TL;DR, takeaways with timestamp links,
 *  quotes, the extras and the source list. Fetched ahead of the click, because Safari only
 *  lets a page write the clipboard straight from the click; a fetch in between can lose it.
 *  Keyed on the report's time by the caller, so a rewritten brief is fetched again.
 */
function CopyBrief({ id }: { id: string }) {
  const [md, setMd] = useState<string | null>(null);
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");

  useEffect(() => {
    let alive = true;
    fetch(`${ENGINE}/research/${id}.md`)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => { if (alive) setMd(t); })
      .catch(() => {});
    return () => { alive = false; };
  }, [id]);

  const copy = async () => {
    try {
      const text: string = md !== null ? md : await (await fetch(`${ENGINE}/research/${id}.md`)).text();
      await navigator.clipboard.writeText(text);
      setState("done");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2000);
  };

  return (
    <button onClick={copy} title="The whole brief as Markdown, with links to every moment"
            className="group inline-flex items-center gap-1.5 text-[13px] text-soft hover:text-accent">
      {state === "done"
        ? <Check size={13} strokeWidth={2.5} className="text-good" />
        : <Copy size={13} strokeWidth={2} />}
      <span className={state === "done" ? "font-medium text-good" : ""}>
        {state === "done" ? "Copied" : state === "failed" ? "Couldn't copy" : "Copy text"}
      </span>
    </button>
  );
}

/** "· 4 exact, 3 close" — how many picks are about the subject itself and how many loosen one
 *  detail. Said out loud so a short list reads as "YouTube has little on this", not a bug. */
function fitLine(run: Run): string {
  const kept = (run.picks ?? []).filter((p) => p.status !== "dropped");
  const fits = kept.filter((p) => p.fit === "fits").length;
  const close = kept.filter((p) => p.fit === "close").length;
  if (!fits && !close) return "";
  const parts = [fits ? `${fits} exact` : "none exact", ...(close ? [`${close} close`] : [])];
  return ` · ${parts.join(", ")}${run.searched ? ` (of ${run.searched} found)` : ""}`;
}

/** Roughly when a video went up, as rough as YouTube's own "2 years ago" (that is where the
 *  date comes from). Two years and older is tinted: on tools, prices and ad rules an old
 *  video can be confidently wrong, and Denis wants to see which ones those are at a glance.
 *  Measured from when the run searched (`now`, seconds), which is when YouTube's label held. */
function Age({ ts, now }: { ts?: number | null; now: number }) {
  if (!ts) return null;
  const days = Math.max(0, (now - ts) / 86400);
  const units: [number, string][] = [[365, "year"], [30, "month"], [7, "week"], [1, "day"]];
  const hit = units.find(([size]) => days >= size);
  const n = hit ? Math.floor(days / hit[0]) : 0;
  const text = hit ? `${n} ${hit[1]}${n > 1 ? "s" : ""} ago` : "today";
  return <span className={days >= 730 ? "font-medium text-accent" : ""}> · {text}</span>;
}

function Status({ s }: { s: string }) {
  const look: Record<string, [string, ReactNode, string]> = {
    succeeded: ["Done", <Check key="i" size={13} strokeWidth={3} />, "bg-good/12 text-good"],
    running: ["Working", <Loader2 key="i" size={13} strokeWidth={2.5} className="animate-spin" />, "bg-accent/12 text-accent"],
    queued: ["Queued", <Clock key="i" size={13} strokeWidth={2.5} />, "bg-ink/[0.06] text-soft"],
    failed: ["Failed", <X key="i" size={13} strokeWidth={3} />, "bg-accent/15 text-accent"],
  };
  const [word, icon, cls] = look[s] ?? [s, null, "bg-ink/[0.06] text-soft"];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12.5px] font-semibold ${cls}`}>
      {icon}{word}
    </span>
  );
}

export default function ResearchPage() {
  const [topic, setTopic] = useState("");
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  // OFF by default (Denis, 7 Oct): screenshots roughly quadruple the time. The estimate next to
  // the box comes from the engine's own measurements, so it is this machine's, not a guess.
  const [shots, setShots] = useState(false);
  const [est, setEst] = useState<{ plain: number; shots: number } | null>(null);
  const [gate, busyDialog] = useBusyGate();
  useEffect(() => {
    fetch(`${ENGINE}/api/limits`).then((r) => r.json()).then((d) => d.research && setEst(d.research)).catch(() => {});
  }, []);
  const mins = (s: number) => (s < 90 ? "about a minute" : `about ${Math.round(s / 60)} min`);

  const loadRuns = useCallback(async () => {
    try {
      const d = await (await fetch(`${ENGINE}/api/research?limit=12`)).json();
      setRuns(d.runs || []);
      return (d.active as string | null) || null;
    } catch {
      setErr(`Engine not reachable at ${ENGINE}`);
      return null;
    }
  }, []);

  const open = useCallback(async (id: string) => {
    try {
      const r = await (await fetch(`${ENGINE}/api/research/${id}`)).json();
      if (!r.error) {
        setRun(r);
        history.replaceState(null, "", `/research?id=${id}`);
        if (!ACTIVE.includes(r.status)) loadRuns();     // a finished run belongs in the list
      }
    } catch { /* the poll will retry */ }
  }, [loadRuns]);

  useEffect(() => {
    (async () => {
      const active = await loadRuns();
      const want = new URLSearchParams(location.search).get("id") || active;
      if (want) open(want);
    })();
  }, [loadRuns, open]);

  // Poll while a run is live; stop as soon as it is finished.
  const runId = run?.id;
  const runStatus = run?.status;
  useEffect(() => {
    if (!runId || !runStatus || !ACTIVE.includes(runStatus)) return;
    const t = setInterval(() => { open(runId); }, 2000);
    return () => clearInterval(t);
  }, [runId, runStatus, open]);

  const start = useCallback(async (topicOverride?: string, shotsOverride?: boolean, skipGate?: boolean) => {
    const t = (topicOverride ?? topic).trim();
    const sh = shotsOverride ?? shots;
    if (t.length < 3) return;
    setBusy(true); setErr("");
    try {
      // Ask first whether the PC's GPU is free; if not, the research can go to this Mac.
      if (!skipGate) {
        const where = await gate("this research");
        if (where === "cancel") return;
        if (where === "mac") {
          switchEngine("mac", `/research?topic=${encodeURIComponent(t)}&shots=${sh ? 1 : 0}&go=1`);
          return;
        }
      }
      const r = await fetch(`${ENGINE}/api/research`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: t, n: 10, shots: sh }),
      });
      const d = await r.json();
      if (d.error) setErr(d.error);
      else { setTopic(""); await open(d.id); loadRuns(); }
    } catch {
      setErr(`Engine not reachable at ${ENGINE}`);
    } finally { setBusy(false); }
  }, [topic, shots, gate, open, loadRuns]);

  // The hand-over from the PC: "Run on this Mac" reloads this window on the Mac's engine with
  // the topic in the address, and it starts here, once, without asking again.
  const handedOver = useRef(false);
  useEffect(() => {
    if (handedOver.current) return;
    const q = new URLSearchParams(location.search);
    const t = q.get("topic");
    if (q.get("go") !== "1" || !t) return;
    handedOver.current = true;
    history.replaceState(null, "", "/research");
    const sh = q.get("shots") === "1";
    setTimeout(() => { setShots(sh); start(t, sh, true); }, 0);
  }, [start]);

  async function post(path: string, body?: object) {
    await fetch(`${ENGINE}${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (run) open(run.id);
  }

  const live = !!run && ACTIVE.includes(run.status);
  const picks = (run?.picks || []).filter((p) => p.status !== "dropped");
  const done = picks.filter((p) => p.status === "succeeded").length;
  const pct = !run ? 0 : run.status === "combining" ? 94 : run.status === "done" ? 100
    : picks.length ? 10 + (80 * done) / picks.length : 6;
  const rows = !run ? [] : run.report
    ? run.report.sources.map((s) => {
        const p = picks.find((x) => x.id === s.id);
        return { ...s, ts: s.ts ?? p?.ts, why: p?.why || "", status: "succeeded", error: "" };
      })
    : picks.map((p, i) => ({ ...p, k: i + 1 }));

  return (
    <main className="mx-auto w-full max-w-[52rem] px-8 pb-28 pt-16 max-sm:px-5">
      <header className="mb-10 flex items-start justify-between gap-6">
        <div>
          <h1 className="text-[12px] font-semibold uppercase tracking-[0.16em] text-soft">
            <Link href="/" className="hover:text-accent">ytgist</Link> · research
          </h1>
          <p className="mt-2 text-[15px] leading-[1.45] text-soft">
            A topic in. Up to ten videos that are about it, and one brief across all of them.
          </p>
        </div>
        <div className="-mt-1 shrink-0"><EngineLabel /></div>
      </header>

      <form onSubmit={(e) => { e.preventDefault(); start(); }} className="flex gap-2">
        <input
          value={topic} onChange={(e) => setTopic(e.target.value)} disabled={live || busy}
          placeholder={live ? "One research at a time" : "e.g. how to train a LoRA for video models"}
          className="min-w-0 flex-1 rounded-xl border border-line bg-white px-4 py-3 text-[16px]
                     outline-none transition-colors focus:border-ink disabled:opacity-50"
        />
        <button
          type="submit" disabled={live || busy || topic.trim().length < 3}
          className="rounded-xl bg-ink px-5 py-3 text-[15px] font-medium text-canvas transition-opacity disabled:opacity-30"
        >
          Research
        </button>
      </form>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13.5px] text-soft">
        <label className="flex w-fit items-center gap-2">
          <input type="checkbox" checked={shots} onChange={(e) => setShots(e.target.checked)} disabled={live || busy}
                 className="h-3.5 w-3.5 accent-[var(--color-accent)]" />
          Include screenshots
        </label>
        {est && (
          <span className="tabular-nums">
            {mins(shots ? est.shots : est.plain)} for 10 videos
            {!shots && <span className="text-soft/70"> · {mins(est.shots)} with screenshots</span>}
          </span>
        )}
      </div>
      {err && <p className="mt-3 text-[13.5px] text-accent">{err}</p>}

      {run && (
        <section className="mt-12" style={{ animation: "rise .35s var(--ease-out-expo)" }}>
          <div className="flex items-baseline justify-between gap-4">
            <h2 className="text-[26px] font-semibold leading-tight text-ink">{run.topic}</h2>
            {live && (
              <button onClick={() => post(`/api/research/${run.id}/cancel`)}
                      className="shrink-0 text-[13px] text-soft hover:text-accent">Stop</button>
            )}
            {run.report && (
              <div className="flex shrink-0 items-center gap-4">
                <CopyBrief key={`${run.id}-${run.report.at}`} id={run.id} />
                <a href={`${ENGINE}/research/${run.id}`} target="_blank" rel="noopener noreferrer"
                   className="text-[13px] text-soft hover:text-accent">Open as page ↗</a>
              </div>
            )}
          </div>

          {run.queries && run.queries.length > 1 && (
            <div className="mt-4">
              <p className="text-[11.5px] font-semibold uppercase tracking-[0.1em] text-soft">
                Searched YouTube {run.queries.length} ways{run.searched ? ` · ${run.searched} videos found` : ""}
              </p>
              <ol className="mt-2 list-decimal space-y-1 pl-5 text-[14px] leading-[1.45] text-body marker:text-soft">
                {run.queries.map((q, i) => (
                  <li key={i} className="pl-1">
                    {q}
                    {i === 0 && <span className="ml-2 text-[12px] text-soft">as you typed</span>}
                    {run.wider_at !== undefined && i === run.wider_at && (
                      <span className="ml-2 text-[12px] text-soft">wider, because too few fit</span>
                    )}
                  </li>
                ))}
              </ol>
              {run.subject && (
                <p className="mt-2.5 text-[13.5px] text-soft">
                  Kept only videos about <span className="font-medium text-ink">{run.subject}</span>
                  {fitLine(run)}
                </p>
              )}
            </div>
          )}

          {run.status !== "done" && (
            <div className="mt-5">
              <div className="h-1.5 overflow-hidden rounded-full bg-line">
                <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${pct}%` }} />
              </div>
              <p className="mt-2 text-[13px] text-soft">
                {run.status === "failed" ? run.error : run.status === "cancelled" ? "Stopped." : run.msg}
              </p>
            </div>
          )}


          {run.report && rows.length > 0 && (
            <div className="mt-6">
              <p className="text-[11.5px] font-semibold uppercase tracking-[0.1em] text-soft">
                Based on {rows.length} video{rows.length > 1 ? "s" : ""}
              </p>
              <ol className="mt-2 divide-y divide-line border-y border-line">
                {rows.map((p) => (
                  <li key={p.id} title={p.why || undefined}
                      className="grid grid-cols-[2.2rem_minmax(0,1fr)] items-baseline gap-x-2 py-2 sm:grid-cols-[2.2rem_minmax(0,1fr)_auto]">
                    <span className="font-mono text-[12px] text-soft">V{p.k}</span>
                    <a href={`https://youtu.be/${p.id}`} target="_blank" rel="noopener noreferrer"
                       className="truncate text-[14px] font-medium text-ink hover:text-accent">{p.title}</a>
                    <span className="whitespace-nowrap text-[12.5px] text-soft max-sm:col-start-2">
                      {p.channel} · {dur(p.duration)}<Age ts={p.ts} now={run.created} />
                    </span>
                  </li>
                ))}
              </ol>
              {!!run.report.unsupported_dropped && (
                <div className="mt-2 text-[12.5px] text-soft">
                  {(() => {
                    const n = run.report.unsupported_dropped ?? 0, t = run.report.takeaways_dropped ?? 0;
                    return `Every citation was checked against the transcript: ${n} didn't back its takeaway and `
                      + `${n > 1 ? "were" : "was"} removed`
                      + (t ? `, and ${t} takeaway${t > 1 ? "s" : ""} with nothing behind ${t > 1 ? "them were" : "it was"} left out` : "")
                      + ".";
                  })()}
                  {!!run.report.removed?.length && (
                    <details className="mt-1.5 group">
                      <summary className="cursor-pointer select-none text-[12.5px] font-medium text-soft hover:text-ink">
                        What was removed, and why
                      </summary>
                      <ul className="mt-2 space-y-1.5 text-[13px] leading-[1.45] text-body">
                        {run.report.removed.map((x, i) => (
                          <li key={i}>
                            <a href={`https://youtu.be/${x.id}?t=${x.secs}`} target="_blank" rel="noopener noreferrer"
                               className="font-mono text-[12px] text-soft hover:text-accent">V{x.k} {x.stamp}</a>
                            {" "}under &ldquo;{x.headline}&rdquo;{x.takeaway_left_out ? " (takeaway left out)" : ""}
                            {x.why ? <span className="text-soft"> · {x.why}</span> : null}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              )}
            </div>
          )}

          {run.report?.takeaways ? (
            <div className="mt-6">
              {run.report.tldr && (
                <p className="prose-serif border-l-[3px] border-accent bg-accent/[0.05] py-3.5 pl-4 pr-3 font-serif text-[21px] leading-[1.52] text-ink">
                  {run.report.tldr}
                </p>
              )}
              <ol className="mt-13 -ml-[3.75rem] max-sm:ml-0">
                {run.report.takeaways.map((t, i) => <StepView key={i} n={i + 1} t={t} />)}
              </ol>
              {run.report.extras && <Extras md={run.report.extras} />}
            </div>
          ) : run.report ? (
            <div className="mt-6">
              <Brief md={run.report.markdown} />
            </div>
          ) : null}

          {!run.report && rows.length > 0 && (
            <>
              <h3 className="mb-3 mt-10 text-[11.5px] font-semibold uppercase tracking-[0.1em] text-soft">
                {`Videos · ${done} of ${picks.length}`}
              </h3>
              <div className="space-y-2">
                {rows.map((p) => (
                  <div key={p.id}
                       className="grid grid-cols-[2.2rem_minmax(0,1fr)_auto] items-center gap-3 rounded-xl border border-line bg-white/50 px-4 py-3">
                    <span className="font-mono text-[12px] text-soft">V{p.k}</span>
                    <span className="min-w-0">
                      <a href={`https://youtu.be/${p.id}`} target="_blank" rel="noopener noreferrer"
                         className="text-[14.5px] font-medium text-ink hover:text-accent">{p.title}</a>
                      <span className="block truncate text-[12.5px] text-soft">
                        {p.channel} · {dur(p.duration)}<Age ts={p.ts} now={run.created} />{p.why ? ` · ${p.why}` : ""}
                        {p.status === "failed" && p.error ? ` · ${p.error}` : ""}
                      </span>
                    </span>
                    <span className="flex items-center gap-3 whitespace-nowrap text-[12.5px]">
                      <Status s={p.status} />
                      {live && p.status !== "succeeded" && (
                        <button title="leave this video out"
                                onClick={() => post(`/api/research/${run.id}/drop`, { video: p.id })}
                                className="text-soft hover:text-accent">✕</button>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      )}

      {runs.length > 0 && (
        <section className="mt-16">
          <h3 className="mb-3 text-[11.5px] font-semibold uppercase tracking-[0.1em] text-soft">Earlier research</h3>
          <div className="divide-y divide-line border-y border-line">
            {runs.map((r) => (
              <button key={r.id} onClick={() => open(r.id)}
                      className="flex w-full items-baseline justify-between gap-4 py-2.5 text-left hover:text-accent">
                <span className="truncate text-[14.5px]">{r.topic}</span>
                <span className="shrink-0 text-[12.5px] text-soft">
                  {r.status === "done" ? `${r.done} videos` : r.status} · {new Date(r.created * 1000).toLocaleDateString()}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}
      {busyDialog}
    </main>
  );
}
