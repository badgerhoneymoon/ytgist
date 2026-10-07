"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Frame, GpuSample, Gist, Stage } from "./types";
import { parseGist } from "./parse";
import History from "./History";
import Preview from "./Preview";
import Progress from "./Progress";
import Result from "./Result";
import { ENGINE, switchEngine } from "./engine";
import { EngineLabel, useBusyGate } from "./BusyGate";
import Link from "next/link";

// DIRECT to the engine, NOT through Next's rewrite: the rewrite buffers server-sent
// events, so a job would finish server-side while the page waited forever without
// receiving a single frame.

const YT_ID = /(?:v=|youtu\.be\/|\/shorts\/|\/embed\/|\/live\/)([A-Za-z0-9_-]{11})/;
const YT_HOSTS = /^(www\.|m\.|music\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)$/;

/** Is this a YouTube link we can actually handle? Drives the button state AND the
 *  preview, so "disabled" always has a visible reason next to it — a button that
 *  silently refuses clicks is the worst way to say "that link is wrong". */
function parseYouTube(raw: string): string | null {
  const s = raw.trim();
  if (!s) return null;
  try {
    const u = new URL(s.startsWith("http") ? s : `https://${s}`);
    if (!YT_HOSTS.test(u.hostname)) return null;
    const fromPath = u.hostname.endsWith("youtu.be")
      ? u.pathname.slice(1).split("/")[0]
      : null;
    const id = fromPath || u.searchParams.get("v") || u.pathname.split("/")[2] || "";
    return /^[A-Za-z0-9_-]{11}$/.test(id) ? id : null;
  } catch {
    return null;
  }
}

export default function Home() {
  const [url, setUrl] = useState("");
  const [stage, setStage] = useState<Stage | null>(null);
  const [pct, setPct] = useState(0);
  const [msg, setMsg] = useState("");
  const [gist, setGist] = useState<Gist | null>(null);
  const [error, setError] = useState("");
  // THE SCREENSHOT PASS IS NOT A RUN, and must not drive the run's progress bar: that bar
  // draws four known stages against their own ETAs, and a fifth it has never heard of
  // renders as a gap. It reports itself inside the result, next to the button that started
  // it — the same place "More detail" reports.
  const [shotMsg, setShotMsg] = useState("");
  const [shotBusy, setShotBusy] = useState(false);
  const busy = stage !== null;

  const videoId = parseYouTube(url);
  const badLink = url.trim().length > 0 && !videoId;
  const esRef = useRef<EventSource | null>(null);
  // Which kind of job the stream is carrying. A ref, not state: the SSE handler is created
  // once and would otherwise read whatever this was when it was built.
  const shotJobRef = useRef(false);
  const jobRef = useRef<string>("");

  const cancel = useCallback(() => {
    const job = jobRef.current;
    if (job) {
      fetch(`${ENGINE}/api/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job }),
      }).catch(() => {});
    }
    esRef.current?.close();
    esRef.current = null;
    setStage(null);
    setMsg("");
  }, []);

  const goHome = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    setStage(null);
    setPct(0);
    setMsg("");
    setGist(null);
    setError("");
    setUrl("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const [libKey, setLibKey] = useState(0);   // bumped after a run so the library refreshes
  const [libCount, setLibCount] = useState(0);
  // OFF by default: Denis reads the argument faster in English even when the source is
  // Russian, and the evidence quotes stay verbatim either way. On, for when the wording
  // itself is the point.
  const [native, setNative] = useState(false);
  // Screenshots as part of the RUN, chosen before it starts. The pass itself is the same
  // one the button under a finished summary triggers; this only decides whether the run
  // goes on to do it without being asked again.
  const [shots, setShots] = useState(false);
  const [gate, busyDialog] = useBusyGate();
  const [eta, setEta] = useState<Record<string, number> | null>(null);
  const [phaseAgo, setPhaseAgo] = useState(0);
  const [gpuSeries, setGpuSeries] = useState<GpuSample[]>([]);
  // MEASURED, not asserted. These numbers used to be hard-coded from my own guesses; the
  // engine has been recording the real ones all along, so it serves them (Denis, 2026-08-09).
  const [limits, setLimits] = useState<
    { bands: { label: string; secs: number }[]; max_hours: number } | null
  >(null);
  useEffect(() => {
    fetch(`${ENGINE}/api/limits`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setLimits)
      .catch(() => {});
  }, []);

  /** The engine is the source of truth; the stream is only the fast path.
   *
   *  A dropped EventSource was being treated as failure — "Lost the connection to the
   *  engine" — while the job carried on perfectly well and the summary landed in the
   *  library (Denis, 2026-08-09). SSE gives liveness, not truth. So before declaring
   *  anything broken, ask the engine what actually happened; and while a run is in flight,
   *  poll quietly underneath the stream so a lost frame costs nothing. */
  const settle = useCallback(async (): Promise<"done" | "running" | "gone"> => {
    try {
      // Follow OUR job: another queued video may already be running when this one
      // finishes. The global current snapshot belongs only to reload discovery.
      // Fall back while the previous engine is still live before its next restart.
      let c;
      if (jobRef.current) {
        const r = await fetch(`${ENGINE}/api/jobs/${jobRef.current}`);
        if (r.ok) {
          const j = await r.json();
          if (["failed", "cancelled", "interrupted"].includes(j.status)) {
            setError(j.error ?? "");
            setShotBusy(false);
            setShotMsg("");
            shotJobRef.current = false;
            setStage(null);
            return "done";
          }
          if (j.kind === "frames" && j.status === "succeeded") {
            const f = j.result as Frame;
            setGist((g) => g ? {
              ...g, framesOutcome: f.frames_outcome ?? "",
              takeaways: g.takeaways.map((t, i) => ({ ...t, frame: f.frames?.[i] ?? t.frame })),
            } : g);
            setShotBusy(false);
            setShotMsg("");
            shotJobRef.current = false;
            setStage(null);
            return "done";
          }
          c = { job: j.id, url: j.request.url, result: j.result, frame: j.event };
        }
      }
      c ??= await (await fetch(`${ENGINE}/api/current`)).json();
      if (!c?.job) return "gone";
      if (c.result) {
        const id = parseYouTube(c.url ?? "") ?? "";
        setGist(parseGist(c.result as Frame, id));
        setLibKey((k) => k + 1);
        setStage(null);
        setError("");
        return "done";
      }
      const f = (c.frame ?? {}) as Frame;
      if (f.pct !== undefined) setPct(f.pct);
      if (f.eta) setEta(f.eta);
      if (f.msg) setMsg(f.msg);
      if (f.stage) setStage(f.stage === "cached" ? "summarise" : f.stage);
      return "running";
    } catch {
      return "gone";
    }
  }, []);

  /** Attach to a job already in flight, or pick up one that finished while we were away.
   *
   *  The WORK always survived a reload — it runs in its own thread inside a detached engine,
   *  so closing the window or quitting the app never touched it. Only the view was lost: the
   *  page forgot the job id and had no way to ask (Denis, 2026-08-09). */
  const attach = useCallback((job: string, target: string) => {
    jobRef.current = job;
    const es = new EventSource(`${ENGINE}/api/events?job=${job}`);
    esRef.current = es;
    let watchdog: ReturnType<typeof setTimeout>;
    // The quiet backstop: even with the stream silent, this keeps the bar moving and
    // catches the result if a frame is missed.
    const poll = setInterval(async () => {
      if ((await settle()) !== "running") clearInterval(poll);
    }, 5_000);
    const done = () => {
      clearTimeout(watchdog);
      clearInterval(poll);
      es.close();
      esRef.current = null;
      setStage(null);
    };
    const arm = () => {
      clearTimeout(watchdog);
      watchdog = setTimeout(async () => {
        if ((await settle()) === "running") return arm();   // alive, just quiet
        setError("The engine stopped responding. It may have been restarted — try again.");
        done();
      }, 150_000);
    };
    arm();
    es.onmessage = (ev) => {
      arm();
      const f: Frame = JSON.parse(ev.data);
      // ONE HANDLER FOR BOTH KINDS OF JOB. A second EventSource handler is what once left
      // the machine readout invisible on fresh runs; the difference between a summary and
      // a screenshot pass belongs in a few lines here, not in two subscriptions.
      //
      // The same "frames" stage arrives from both, and WHO ASKED decides where it shows:
      // a pass the result's own button started reports inside the result, while a run that
      // was told to look for screenshots reports in the progress bar, where the rest of
      // that run's phases are.
      if (f.stage === "frames" && shotJobRef.current) {
        setShotMsg(f.msg ?? "");
        return;
      }
      if (f.frames_done) {
        setGist((g) =>
          g
            ? {
                ...g,
                framesOutcome: f.frames_outcome ?? "",
                takeaways: g.takeaways.map((t, i) => ({ ...t, frame: f.frames?.[i] ?? t.frame })),
              }
            : g,
        );
        setShotBusy(false);
        setShotMsg("");
        shotJobRef.current = false;
        done();
        return;
      }
      if (f.error || f.stopped) {
        // Cleared unconditionally: clearing what is already clear costs nothing, and the
        // alternative is reading state this callback closed over long ago.
        setShotBusy(false);
        setShotMsg("");
        shotJobRef.current = false;
      }
      if (f.pct !== undefined) setPct(f.pct);
      if (f.stage) setStage(f.stage === "cached" ? "summarise" : f.stage);
      if (f.msg) setMsg(f.msg);
      if (f.eta) setEta(f.eta);
      if (f.gpu_series?.length) setGpuSeries((g) => [...g, ...f.gpu_series!].slice(-240));
      if (f.error) {
        setError(f.error);
        done();
      }
      if (f.stopped) done();
      if (f.markdown !== undefined) {
        const id = parseYouTube(target) ?? target.match(YT_ID)?.[1] ?? "";
        setGist(parseGist(f, id));
        setLibKey((k) => k + 1);
        done();
      }
    };
    es.onerror = async () => {
      if (es.readyState !== EventSource.CLOSED) return;   // it reconnects on its own
      const what = await settle();
      if (what === "running") return;                     // the poll keeps it alive
      if (what === "done") { done(); return; }
      setError("Lost the connection to the engine — try again.");
      done();
    };
  }, [settle]);

  // On load, ask what the engine is doing. A run in flight restores the progress bar from
  // its cumulative state; one that finished while the page was closed opens as a result.
  useEffect(() => {
    if (new URLSearchParams(location.search).get("go") === "1") return;   // a hand-over: see below
    fetch(`${ENGINE}/api/current`)
      .then((r) => (r.ok ? r.json() : null))
      .then((c) => {
        if (!c?.job) return;
        setUrl(c.url ?? "");
        setNative(!!c.native);
        if (c.result) {
          const id = parseYouTube(c.url ?? "") ?? "";
          setGist(parseGist(c.result as Frame, id));
          return;
        }
        const f = (c.frame ?? {}) as Frame;
        if (f.pct !== undefined) setPct(f.pct);
        if (f.eta) setEta(f.eta);
        setMsg(f.msg ?? "reconnecting");
        setPhaseAgo(c.phase_elapsed ?? 0);
        setStage(f.stage === "cached" ? "summarise" : (f.stage ?? "check"));
        attach(c.job, c.url ?? "");
      })
      .catch(() => {});
  }, [attach]);

  /** Look for a screenshot worth showing beside each takeaway. A second pass over a
   *  summary that already exists — never part of a run, so a video with no slides pays
   *  nothing and a failed search cannot cost a summary. */
  const findShots = useCallback(async () => {
    if (!gist || busy || shotBusy) return;
    setShotBusy(true);
    shotJobRef.current = true;
    setShotMsg("starting");
    setError("");
    try {
      const res = await fetch(`${ENGINE}/api/frames`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: `https://www.youtube.com/watch?v=${gist.videoId}`,
          video: gist.videoId,
          native,
        }),
      });
      const { job } = await res.json();
      if (!job) throw new Error("no job");
      attach(job, `https://www.youtube.com/watch?v=${gist.videoId}`);
    } catch {
      setShotBusy(false);
      shotJobRef.current = false;
      setShotMsg("");
      setError("Couldn't start the screenshot search — is the engine running?");
    }
  }, [attach, busy, gist, native, shotBusy]);

  const start = useCallback(
    async (
      mode: "" | "regen" | "refresh" = "",
      urlOverride?: string,
      nativeOverride?: boolean,
      shotsOverride?: boolean,
      skipGate?: boolean,
    ) => {
      const target = (urlOverride ?? url).trim();
      if (!target || busy) return;
      const nat = nativeOverride ?? native;
      const sh = shotsOverride ?? shots;
      // A new gist asks first whether the PC's GPU is free; if not, it can go to this Mac.
      if (!mode && !skipGate) {
        const where = await gate("this video");
        if (where === "cancel") return;
        if (where === "mac") {
          switchEngine("mac", `/?url=${encodeURIComponent(target)}&native=${nat ? 1 : 0}&shots=${sh ? 1 : 0}&go=1`);
          return;
        }
      }
      setGist(null);
      setError("");
      setPct(0);
      setStage("check");
      setEta(null);
      setPhaseAgo(0);
      setGpuSeries([]);
      setMsg("starting");

      const res = await fetch(`${ENGINE}/api/gist`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: target,
          native: nat,
          shots: sh,
          regen: mode === "regen",       // new summary, transcript reused
          refresh: mode === "refresh",   // download and transcribe again too
        }),
      });
      const { job } = await res.json();
      attach(job, target);
    },
    [url, busy, native, shots, attach, gate]
  );

  // The hand-over: "Run on this Mac" reloads this window on the Mac's engine with the video in
  // the address, and it starts here, once, without asking again.
  const handedOver = useRef(false);
  useEffect(() => {
    if (handedOver.current) return;
    const q = new URLSearchParams(location.search);
    const u = q.get("url");
    if (q.get("go") !== "1" || !u) return;
    handedOver.current = true;
    history.replaceState(null, "", "/");
    const nat = q.get("native") === "1", sh = q.get("shots") === "1";
    setTimeout(() => {
      setUrl(u); setNative(nat); setShots(sh);
      start("", u, nat, sh, true);
    }, 0);
  }, [start]);

  return (
    <main
      className="mx-auto grid w-full max-w-[57rem] px-8 pb-28 pt-16
                 [grid-template-columns:3.75rem_minmax(0,1fr)]
                 max-sm:[grid-template-columns:minmax(0,1fr)] max-sm:px-5
                 [&>*]:col-start-2 max-sm:[&>*]:col-start-1"
    >
      <header className="mb-10 flex items-start justify-between gap-6">
        <div>
          <h1>
            <button
              onClick={goHome}
              title="start over"
              className="text-[12px] font-semibold uppercase tracking-[0.16em] text-soft
                         transition-colors duration-150 hover:text-accent
                         focus-visible:outline-2 focus-visible:outline-offset-4
                         focus-visible:outline-accent"
            >
              ytgist
            </button>
          </h1>
          <p className="mt-2 text-[15px] leading-[1.45] text-soft">
            Paste a link. Get the argument, not a wall of text.
          </p>
        </div>

        <div className="-mt-1 flex shrink-0 items-center gap-2">
        <EngineLabel />
        <Link
          href="/research"
          title="a topic in: ten videos, one brief"
          className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-1.5
                     text-[12.5px] font-medium text-soft transition-colors duration-150
                     hover:border-ink hover:text-ink focus-visible:outline-2
                     focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden fill="none"
               stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <circle cx="6" cy="6" r="4" /><path d="M9 9l3 3" />
          </svg>
          Research
        </Link>
        {libCount > 0 && (
          <button
            onClick={() =>
              document.getElementById("library")?.scrollIntoView({ behavior: "smooth" })
            }
            title="everything you have summarised"
            className="group flex shrink-0 items-center gap-2 rounded-lg border border-line
                       px-2.5 py-1.5 text-[12.5px] font-medium text-soft
                       transition-colors duration-150 hover:border-ink hover:text-ink
                       focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden fill="none"
                 stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M2 3.5h10M2 7h10M2 10.5h6" />
            </svg>
            Library
            <span className="tabular-nums text-soft/60 group-hover:text-soft">{libCount}</span>
          </button>
        )}
        </div>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          start();
        }}
        className="flex flex-wrap gap-2.5"
      >
        {/* A real checkbox, hidden but present: it keeps the label click, the space-bar
            toggle and the focus ring for free, where a div-with-onClick loses all three. */}
        <label className="group flex w-full cursor-pointer select-none items-center gap-2.5
                          text-[13.5px] text-soft">
          <input
            type="checkbox"
            checked={native}
            onChange={(e) => setNative(e.target.checked)}
            className="peer sr-only"
          />
          <span
            className="relative h-[18px] w-[30px] shrink-0 rounded-full bg-line transition-colors
                       duration-200 peer-checked:bg-accent
                       peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2
                       peer-focus-visible:outline-accent"
          >
            <span
              className="absolute left-[2px] top-[2px] h-[14px] w-[14px] rounded-full bg-canvas
                         shadow-sm transition-transform duration-200 peer-checked:translate-x-3
                         group-has-[:checked]:translate-x-3"
              style={{ transitionTimingFunction: "var(--ease-out-expo)" }}
            />
          </span>
          <span className="transition-colors duration-150 group-hover:text-ink">
            Keep the takeaways in the video&rsquo;s own language
          </span>
        </label>

        {/* SCREENSHOTS, DECIDED UP FRONT. The button under a finished summary stays — it is
            how an already-transcribed video gets pictures — but choosing before the run is
            how you avoid coming back to press it (Denis, 2026-10-02). */}
        <label className="group flex w-full cursor-pointer select-none items-center gap-2.5
                          text-[13.5px] text-soft">
          <input
            type="checkbox"
            checked={shots}
            onChange={(e) => setShots(e.target.checked)}
            className="peer sr-only"
          />
          <span
            className="relative h-[18px] w-[30px] shrink-0 rounded-full bg-line transition-colors
                       duration-200 peer-checked:bg-accent
                       peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2
                       peer-focus-visible:outline-accent"
          >
            <span
              className="absolute left-[2px] top-[2px] h-[14px] w-[14px] rounded-full bg-canvas
                         shadow-sm transition-transform duration-200 peer-checked:translate-x-3
                         group-has-[:checked]:translate-x-3"
              style={{ transitionTimingFunction: "var(--ease-out-expo)" }}
            />
          </span>
          <span className="transition-colors duration-150 group-hover:text-ink">
            Look for a screenshot for each takeaway
          </span>
        </label>

        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          type="url"
          required
          autoFocus
          placeholder="https://youtube.com/watch?v=…"
          className="flex-1 rounded-xl border border-line bg-transparent px-4 py-3 text-[15px]
                     outline-none transition-[border-color,box-shadow] duration-150
                     placeholder:text-soft/60 hover:border-line
                     focus:border-accent focus:shadow-[0_0_0_3px_rgba(194,87,26,0.14)]"
          style={{ transitionTimingFunction: "var(--ease-out-expo)" }}
        />
        <button
          disabled={busy || !videoId}
          title={!videoId ? "paste a YouTube link first" : undefined}
          className="rounded-xl bg-ink px-6 py-3 text-[15px] font-semibold text-canvas
                     transition-[transform,opacity,box-shadow] duration-150
                     hover:shadow-[0_2px_10px_rgba(34,30,26,0.18)] active:scale-[0.985]
                     disabled:cursor-not-allowed disabled:opacity-25 disabled:shadow-none
                     focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          style={{ transitionTimingFunction: "var(--ease-out-expo)" }}
        >
          Gist
        </button>

      </form>

      {/* The honest cost, before you commit to it. Length changes the wait by an order of
          magnitude and nothing on the page said so — a 90-minute interview looks exactly
          like a 5-minute clip until you are four minutes in. */}
      {limits && (
        <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-soft/80">
          {limits.bands.map((b, n) => (
            <span key={b.label} className="flex items-center gap-1.5">
              <i
                className={`inline-block h-1.5 w-1.5 rounded-full ${
                  n === 0 ? "bg-good" : n === 1 ? "bg-accent/70" : "bg-line"
                }`}
              />
              {b.label}{" "}
              <b className="font-medium text-soft">
                {b.secs < 90 ? `~${Math.round(b.secs)}s` : `~${Math.round(b.secs / 60)} min`}
              </b>
            </span>
          ))}
          <span className="flex items-center gap-1.5">
            <i className="inline-block h-1.5 w-1.5 rounded-full bg-line" />
            over {limits.max_hours} h <b className="font-medium text-soft">refused</b>
          </span>
        </p>
      )}

      {/* The reason, right where the refusal is. */}
      {badLink && (
        <p className="mt-2 text-[13px] text-accent">
          that doesn&rsquo;t look like a YouTube link
        </p>
      )}
      <Preview videoId={videoId ?? ""} native={native} />

      {busy && <Progress stage={stage} pct={pct} msg={msg} eta={eta} gpuSeries={gpuSeries} phaseAgo={phaseAgo} onCancel={cancel} />}

      {error && (
        <p className="mt-8 rounded-xl border border-red-500/30 bg-red-500/5 px-4 py-3 text-[15px] text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      {gist && (
        <Result
          gist={gist}
          native={native}
          onRegenerate={() => start("regen")}
          onRetranscribe={() => start("refresh")}
          onFindShots={findShots}
          shotBusy={shotBusy}
          shotMsg={shotMsg}
          onCancelShots={cancel}
        />
      )}

      {!busy && (
        <History
          refreshKey={libKey}
          onCount={setLibCount}
          onPick={(id, wantNative) => {
            const u = `https://www.youtube.com/watch?v=${id}`;
            setUrl(u);
            setNative(wantNative);      // keep the toggle honest about what is on screen
            setGist(null);
            window.scrollTo({ top: 0, behavior: "smooth" });
            start("", u, wantNative);
          }}
        />
      )}
      {busyDialog}
    </main>
  );
}
