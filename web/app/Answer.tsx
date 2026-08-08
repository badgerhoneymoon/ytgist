"use client";

import type { Answer as AnswerT, Cite } from "./types";

/** An answer to a question about the video.
 *
 *  This did not exist. The Ask box has been wired to the engine the whole time, the engine
 *  has been answering, and the page had nowhere to put the result — `parseGist` only knows
 *  how to render `**bold headlines**`, so an answer arrived and rendered as a title above
 *  an empty page (Denis, 2026-08-08: "where would I get an answer — do we have UX for
 *  that?").
 *
 *  An answer is PROSE, not an argument, so it gets prose treatment: the question in the
 *  reader's own words, the answer in the serif reading face, and every [MM:SS] the model
 *  cited turned into a link back into the video. Answers stack rather than replace, and
 *  they never displace the summary — asking a follow-up should not cost you the thing you
 *  were reading.
 */
export default function Answer({ answer }: { answer: AnswerT }) {
  return (
    <article
      className="mt-8 border-l-[3px] border-ink/15 pl-5"
      style={{ animation: "rise .4s var(--ease-out-expo)" }}
    >
      <p className="text-[13px] font-semibold uppercase tracking-[0.09em] text-soft">
        You asked
      </p>
      <p className="mt-1.5 text-[17px] font-semibold leading-[1.35] tracking-[-0.005em] text-ink">
        {answer.question}
      </p>

      <Cited paragraphs={answer.paragraphs} className="mt-4 text-[18px]" />
    </article>
  );
}

/** Prose with its citations as small inline links. The timestamp sits where the claim is
 *  made, in the sans face so it reads as an instrument rather than part of the sentence. */
export function Cited({
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
