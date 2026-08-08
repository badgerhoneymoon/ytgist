"use client";

import { useEffect, useState } from "react";

type Meta = { title: string; author: string; thumb: string };

/** The video, the moment you paste the link.
 *
 *  Two jobs. It confirms the app UNDERSTOOD your link — which is the real question behind
 *  "why isn't the button working" — and it gives the eye something while the 40-second
 *  summary runs. YouTube's oEmbed needs no key and no quota, so this costs one small
 *  request and nothing else. */
export default function Preview({ videoId }: { videoId: string }) {
  const [meta, setMeta] = useState<Meta | null>(null);

  useEffect(() => {
    if (!videoId) {
      setMeta(null);
      return;
    }
    let alive = true;
    fetch(`/api/oembed?v=${videoId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (alive && d?.title) {
          setMeta({
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

  return (
    <a
      href={`https://youtu.be/${videoId}`}
      target="_blank"
      rel="noopener"
      className="mt-4 flex items-center gap-4 rounded-xl border border-line p-2.5
                 transition hover:border-ink/25 animate-[fadeUp_.35s_cubic-bezier(.16,1,.3,1)]"
    >
      <img
        src={meta?.thumb ?? `https://i.ytimg.com/vi/${videoId}/mqdefault.jpg`}
        alt=""
        className="h-[54px] w-24 shrink-0 rounded-lg object-cover bg-line"
      />
      <div className="min-w-0">
        <p className="truncate text-[14.5px] font-medium leading-snug">
          {meta?.title ?? "loading…"}
        </p>
        {meta?.author && (
          <p className="mt-0.5 truncate text-[13px] text-soft">{meta.author}</p>
        )}
      </div>
    </a>
  );
}
