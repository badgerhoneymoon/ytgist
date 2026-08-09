"use client";

import { useEffect, useState } from "react";

type Meta = { id: string; title: string; author: string; thumb: string };
/** Duration and this video's own estimate, from the engine. oEmbed does not carry length,
 *  and length is the one property that changes the wait by an order of magnitude. */
type Cost = {
  id: string;
  duration: number;
  eta: number;
  cached: boolean;
  hasEn: boolean;
  hasNative: boolean;
  /** yt-dlp's title. A fallback for oEmbed, which answers 502 often enough to matter — the
   *  card sat on "loading…" for a video whose title the engine already had. */
  title: string;
};

const ENGINE = "http://127.0.0.1:8765";

function hms(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  if (m >= 60) return `${Math.floor(m / 60)} h ${m % 60} min`;
  return m ? `${m} min ${s.toString().padStart(2, "0")} sec` : `${s} sec`;
}

/** The video, the moment you paste the link.
 *
 *  Two jobs. It confirms the app UNDERSTOOD your link — which is the real question behind
 *  "why isn't the button working" — and it gives the eye something while the 40-second
 *  summary runs. YouTube's oEmbed needs no key and no quota, so this costs one small
 *  request and nothing else. */
export default function Preview({
  videoId,
  native = false,
}: {
  videoId: string;
  native?: boolean;
}) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [cost, setCost] = useState<Cost | null>(null);

  useEffect(() => {
    // No setMeta(null) here. Clearing state synchronously at the top of an effect causes
    // a cascading render, and it is not needed: stamping the id ONTO the meta lets render
    // ignore a result that belongs to the previous link, which is the actual requirement.
    if (!videoId) return;
    let alive = true;
    fetch(`${ENGINE}/api/probe?v=${videoId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (alive && d?.duration) {
          setCost({
            id: videoId, duration: d.duration, eta: d.eta, cached: !!d.cached,
            hasEn: !!d.has_en, hasNative: !!d.has_native,
            title: d.title ?? "",
          });
        }
      })
      .catch(() => {});
    fetch(`${ENGINE}/api/oembed?v=${videoId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (alive && d?.title) {
          setMeta({
            id: videoId,
            title: d.title,
            author: d.author_name ?? "",
            thumb: d.thumbnail_url ?? `https://i.ytimg.com/vi/${videoId}/mqdefault.jpg`,
          });
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [videoId]);

  if (!videoId) return null;
  // Only the meta that belongs to THIS link — otherwise pasting a new URL briefly shows
  // the previous video's title.
  const m = meta?.id === videoId ? meta : null;
  const c = cost?.id === videoId ? cost : null;

  return (
    <a
      href={`https://youtu.be/${videoId}`}
      target="_blank"
      rel="noopener"
      className="mt-4 flex items-center gap-4 rounded-xl border border-line p-2.5
                 transition hover:border-ink/25 animate-[fadeUp_.35s_cubic-bezier(.16,1,.3,1)]"
    >
      <img
        src={m?.thumb ?? `https://i.ytimg.com/vi/${videoId}/mqdefault.jpg`}
        alt=""
        className="h-[54px] w-24 shrink-0 rounded-lg object-cover bg-line"
      />
      <div className="min-w-0">
        <p className="truncate text-[14.5px] font-medium leading-snug">
          {m?.title || c?.title || "loading…"}
        </p>
        {m?.author && (
          <p className="mt-0.5 truncate text-[13px] text-soft">{m.author}</p>
        )}
        {c && (
          <p className="mt-1 text-[13px] text-soft">
            <span className="font-medium text-ink">{hms(c.duration)}</span>
            {" · "}
            {(native ? c.hasNative : c.hasEn) ? (
              <span className="text-good">already summarised — opens instantly</span>
            ) : (
              <>
                {c.cached ? "already transcribed, " : ""}
                about {c.eta < 90 ? `${c.eta}s` : `${Math.round(c.eta / 60)} min`} to
                summarise
              </>
            )}
          </p>
        )}
      </div>
    </a>
  );
}
