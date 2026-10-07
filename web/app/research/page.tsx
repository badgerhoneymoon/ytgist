"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { ENGINE, ON_PC } from "../engine";

/** Research mode: a topic → the ~10 most useful videos → one brief across all of them.
 *
 *  The engine does the work (research.py); this page starts a run, shows each video as it
 *  goes, lets you drop one, and renders the brief with every claim linked to its moment.
 */

type Pick = {
  id: string; title: string; channel: string; duration: number; views?: number;
  why: string; status: string; stage?: string; error?: string;
};
type Source = { k: number; id: string; title: string; channel: string; duration: number };
type Cite = { k: number; id: string; secs: number | null; stamp: string | null };
type Step = {
  headline: string; body: string; cites: Cite[]; said: { k: number; stamp: string; text: string }[];
  shot?: { k: number; id: string; secs: number } | null;
};
type Run = {
  id: string; topic: string; status: string; msg: string; created: number;
  picks: Pick[]; error?: string | null; searched?: number;
  report?: {
    markdown: string; sources: Source[]; at: number; unverified_dropped: number;
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
        {t.shot && (
          <a href={`https://youtu.be/${t.shot.id}?t=${t.shot.secs}`} target="_blank" rel="noopener noreferrer"
             title={`V${t.shot.k}: open at this moment`} className="mt-4 block w-fit">
            {/* A plain <img>: served by the engine on another host, which next/image would need configuring for. */}
            <img src={`${ENGINE}/api/frame?v=${t.shot.id}&t=${t.shot.secs}`} alt={`V${t.shot.k} at this moment`}
                 className="max-h-[16rem] w-auto max-w-full rounded-lg border border-line" loading="lazy" />
          </a>
        )}
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

const STATUS: Record<string, string> = {
  queued: "queued", running: "working…", succeeded: "done", failed: "failed", dropped: "left out",
};

export default function ResearchPage() {
  const [topic, setTopic] = useState("");
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [shots, setShots] = useState(true);

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

  async function start() {
    if (topic.trim().length < 3) return;
    setBusy(true); setErr("");
    try {
      const r = await fetch(`${ENGINE}/api/research`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: topic.trim(), n: 10, shots }),
      });
      const d = await r.json();
      if (d.error) setErr(d.error);
      else { setTopic(""); await open(d.id); loadRuns(); }
    } catch {
      setErr(`Engine not reachable at ${ENGINE}`);
    } finally { setBusy(false); }
  }

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
        return { ...s, why: p?.why || "", status: "succeeded", error: "" };
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
            A topic in. The ten most useful videos, and one brief across all of them.
          </p>
        </div>
        <span className="-mt-1 flex shrink-0 items-center gap-1.5 px-1.5 py-1.5 text-[12px] text-soft">
          <span className={`h-1.5 w-1.5 rounded-full ${ON_PC ? "bg-good" : "bg-soft/50"}`} />
          {ON_PC ? "on the PC" : "on this Mac"}
        </span>
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
      <label className="mt-3 flex w-fit items-center gap-2 text-[13.5px] text-soft">
        <input type="checkbox" checked={shots} onChange={(e) => setShots(e.target.checked)} disabled={live || busy}
               className="h-3.5 w-3.5 accent-[var(--color-accent)]" />
        Look for a screenshot for each takeaway (about a minute more per video)
      </label>
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
              <a href={`${ENGINE}/research/${run.id}`} target="_blank" rel="noopener noreferrer"
                 className="shrink-0 text-[13px] text-soft hover:text-accent">Open as page ↗</a>
            )}
          </div>

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

          {rows.length > 0 && (
            <>
              <h3 className="mb-3 mt-10 text-[11.5px] font-semibold uppercase tracking-[0.1em] text-soft">
                {run.report ? "Sources" : `Videos · ${done} of ${picks.length}`}
              </h3>
              <div className="space-y-2">
                {rows.map((p) => (
                  <div key={p.id}
                       className="grid grid-cols-[2.2rem_minmax(0,1fr)_auto] items-baseline gap-3 rounded-xl border border-line bg-white/50 px-4 py-3">
                    <span className="font-mono text-[12px] text-soft">V{p.k}</span>
                    <span className="min-w-0">
                      <a href={`https://youtu.be/${p.id}`} target="_blank" rel="noopener noreferrer"
                         className="text-[14.5px] font-medium text-ink hover:text-accent">{p.title}</a>
                      <span className="block truncate text-[12.5px] text-soft">
                        {p.channel} · {dur(p.duration)}{p.why ? ` · ${p.why}` : ""}
                        {p.status === "failed" && p.error ? ` · ${p.error}` : ""}
                      </span>
                    </span>
                    <span className="flex items-center gap-3 whitespace-nowrap text-[12.5px]">
                      {!run.report && (
                        <span className={p.status === "succeeded" ? "text-good" : p.status === "failed" ? "text-accent" : "text-soft"}>
                          {STATUS[p.status] || p.status}
                        </span>
                      )}
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
    </main>
  );
}
