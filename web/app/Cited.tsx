"use client";

import type { Cite } from "./types";

/** Prose with its citations as small inline links. The timestamp sits where the claim is
 *  made, in the sans face so it reads as an instrument rather than part of the sentence. */
export default function Cited({
  paragraphs,
  className = "",
}: {
  paragraphs: (string | Cite)[][];
  className?: string;
}) {
  return (
    <div className={`prose-serif space-y-3.5 font-serif leading-[1.6] text-body ${className}`}>
      {paragraphs.map((para, i) => (
        <p key={i}>
          {para.map((part, j) =>
            typeof part === "string" ? (
              part
            ) : (
              <a
                key={j}
                href={part.href}
                target="_blank"
                rel="noopener"
                title="open at this moment"
                className="mx-[1px] rounded px-[4px] py-[1px] font-sans text-[12.5px]
                           font-medium tabular-nums text-accent/90 transition-colors duration-150
                           hover:bg-accent/10 hover:text-accent focus-visible:outline-2
                           focus-visible:outline-offset-1 focus-visible:outline-accent"
              >
                {part.stamp}
              </a>
            ),
          )}
        </p>
      ))}
    </div>
  );
}
