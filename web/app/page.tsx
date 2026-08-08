"use client";

import { useCallback, useRef, useState } from "react";
import type { Frame, Gist, Stage } from "./types";
import { parseGist } from "./parse";
import Preview from "./Preview";
import Progress from "./Progress";
import Result from "./Result";

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
  const [question, setQuestion] = useState("");
  const [stage, setStage] = useState<Stage | null>(null);
  const [pct, setPct] = useState(0);
  const [msg, setMsg] = useState("");
  const [gist, setGist] = useState<Gist | null>(null);
  const [error, setError] = useState("");
  const busy = stage !== null;
  const videoId = parseYouTube(url);
  const badLink = url.trim().length > 0 && !videoId;
  const esRef = useRef<EventSource | null>(null);

  const start = useCallback(
    async (ask: string) => {
      if (!url.trim() || busy) return;
      setGist(null);
      setError("");
      setPct(0);
      setStage("check");
      setMsg(ask ? "answering your question" : "starting");

      const res = await fetch("/api/gist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, ask }),
      });
      const { job } = await res.json();

      const es = new EventSource(`/api/events?job=${job}`);
      esRef.current = es;
      let watchdog: ReturnType<typeof setTimeout>;
      const done = () => {
        clearTimeout(watchdog);
        es.close();
        esRef.current = null;
        setStage(null);
      };
      // WATCHDOG. If the engine dies mid-job — I restarted it under a running job and
      // the page sat on "reading video info" forever — the stream simply goes quiet.
      // EventSource does not consider silence an error, so nothing ever fires and the
      // UI waits indefinitely. Any frame resets this; 90s of silence means something
      // broke and the user deserves to be told rather than left watching a spinner.
      const arm = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => {
          setError("The engine stopped responding. It may have been restarted — try again.");
          done();
        }, 90_000);
      };
      arm();
      es.onmessage = (ev) => {
        arm();
        const f: Frame = JSON.parse(ev.data);
        if (f.pct !== undefined) setPct(f.pct);
        if (f.stage) setStage(f.stage === "cached" ? "summarise" : f.stage);
        if (f.msg) setMsg(f.msg);
        if (f.error) {
          setError(f.error);
          done();
        }
        if (f.markdown !== undefined) {
          const id = parseYouTube(url) ?? url.match(YT_ID)?.[1] ?? "";
          setGist(parseGist(f, id));
          done();
        }
      };
      es.onerror = () => {
        // EventSource auto-reconnects; a job id that no longer exists 404s forever, so
        // treat a hard error as terminal rather than letting it retry in a loop.
        if (es.readyState === EventSource.CLOSED) {
          setError("Lost the connection to the engine — try again.");
          done();
        }
      };
    },
    [url, busy]
  );

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <header className="mb-10">
        <h1 className="text-[13px] font-semibold uppercase tracking-[0.18em] text-soft">
          ytgist
        </h1>
        <p className="mt-2 text-[15px] text-soft">
          Paste a link. Get the argument, not a wall of text.
        </p>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          start("");
        }}
        className="flex gap-2.5"
      >
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          type="url"
          required
          autoFocus
          placeholder="https://youtube.com/watch?v=…"
          className="flex-1 rounded-xl border border-line bg-transparent px-4 py-3 text-[15px]
                     outline-none transition placeholder:text-soft/70
                     focus:border-accent focus:ring-2 focus:ring-accent/20"
        />
        <button
          disabled={busy || !videoId}
          title={!videoId ? "paste a YouTube link first" : undefined}
          className="rounded-xl bg-ink px-6 py-3 text-[15px] font-semibold text-canvas
                     transition-[transform,opacity] duration-200 active:scale-[0.98]
                     disabled:cursor-not-allowed disabled:opacity-30"
        >
          Gist
        </button>
      </form>

      {/* The reason, right where the refusal is. */}
      {badLink && (
        <p className="mt-2 text-[13px] text-accent">
          that doesn&rsquo;t look like a YouTube link
        </p>
      )}
      <Preview videoId={videoId ?? ""} />

      <div className="mt-2.5 flex gap-2.5">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              start(question.trim());
            }
          }}
          placeholder="…or ask something about it"
          className="flex-1 rounded-xl border border-line bg-transparent px-4 py-3 text-[15px]
                     outline-none transition placeholder:text-soft/70
                     focus:border-accent focus:ring-2 focus:ring-accent/20"
        />
        <button
          type="button"
          onClick={() => start(question.trim())}
          disabled={busy || !videoId || !question.trim()}
          className="rounded-xl border border-line px-6 py-3 text-[15px] font-semibold
                     transition hover:border-ink active:scale-[0.98] disabled:opacity-40"
        >
          Ask
        </button>
      </div>

      {busy && <Progress stage={stage} pct={pct} msg={msg} />}

      {error && (
        <p className="mt-8 rounded-xl border border-red-500/30 bg-red-500/5 px-4 py-3 text-[15px] text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      {gist && <Result gist={gist} onRegenerate={() => start("")} />}
    </main>
  );
}
