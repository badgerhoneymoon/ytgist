import type { Cite, Frame, Gist, Sentence, StepImage, Takeaway } from "./types";

/** The model returns text; the UI wants structure. Parsing here — rather than asking the
 *  model for JSON — keeps the prompt about WRITING WELL instead of about syntax, and a
 *  malformed line degrades to a plain takeaway instead of failing the whole response.
 *
 *  Expected shape (see gist_prompt.py):
 *      TL;DR one sentence
 *      **Headline that states the point** [MM:SS]
 *      One sentence of substance.
 */
export function parseGist(f: Frame, videoId: string): Gist {
  // Prefer the unrendered text: f.markdown has already had ** turned into <b>.
  const raw = (f.raw ?? f.markdown ?? "").replace(/\r/g, "");
  const sentences: Sentence[] = f.sentences ?? [];

  const tldrMatch = raw.match(/^\s*TL;?DR[:\s]*(.+)$/im);
  const tldr = tldrMatch ? tldrMatch[1].trim() : "";

  const images: (StepImage | null)[] = f.images ?? [];
  const expansions = f.expansions ?? {};
  const takeaways: Takeaway[] = [];
  // Split on the bold headlines; everything until the next one is that takeaway's body.
  const parts = raw.split(/\*\*(.+?)\*\*/g);
  for (let i = 1; i < parts.length; i += 2) {
    const headlineRaw = parts[i].trim();
    const after = (parts[i + 1] ?? "").trim();

    // The timestamp arrives as a full markdown LINK — [02:49](https://youtu.be/…?t=169) —
    // because the engine verifies each stamp and rewrites it. Matching only the [02:49]
    // part left the "(https://youtu.be/…)" stranded in the middle of the sentence, which
    // is exactly what shipped. Consume the whole link, keep the label.
    const linkRe = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]\([^)]*\)/;
    const stampRe = /\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?/;
    const fromHead = headlineRaw.match(linkRe) ?? headlineRaw.match(stampRe);
    const fromBody = after.slice(0, 60).match(linkRe) ?? after.slice(0, 14).match(stampRe);
    const stamp = fromHead?.[1] ?? fromBody?.[1] ?? null;

    const headline = headlineRaw
      .replace(linkRe, "")
      .replace(stampRe, "")
      .replace(/[\[\]]/g, "")
      .trim();
    const body = after
      .replace(linkRe, "")                       // link first — it contains a stamp
      .replace(/^\s*\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*/, "")
      .replace(/^\s*\(https?:\/\/[^)]*\)\s*/, "")   // any stray bare URL remnant
      .split(/\n\s*\n/)[0]
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .join(" ")
      .trim();

    const seconds = stamp ? toSeconds(stamp) : null;
    takeaways.push({
      headline,
      body,
      stamp,
      seconds,
      evidence: seconds === null ? "" : evidenceAt(sentences, seconds),
      // The engine builds this list by splitting the SAME "**" markers, so index i
      // of images is index i of takeaways.
      expansion:
        seconds !== null && String(seconds) in expansions ? expansions[String(seconds)] : null,
      image: images[(i - 1) / 2] ?? null,
    });
  }

  return {
    title: f.title ?? "",
    tldr,
    takeaways,
    videoId,
    timings: f.timings ?? {},
    duration: f.duration ?? 0,
    cached: !!f.cached,
  };
}

export function toSeconds(stamp: string): number {
  const p = stamp.split(":").map(Number);
  return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 60 + p[1];
}

export function fmt(seconds: number): string {
  const t = Math.floor(seconds);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
           : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** The speaker's own words around a cited moment. Showing them does NOT prove the claim —
 *  a wrong claim can quote a real moment — but it makes checking free, which is the honest
 *  version of "cited" and what Perplexity actually offers too. */
function evidenceAt(sentences: Sentence[], seconds: number): string {
  const near = sentences.filter((s) => s.start >= seconds - 8 && s.start <= seconds + 22);
  return near.map((s) => s.text.trim()).join(" ").slice(0, 480);
}


/** An answer, split into paragraphs with its citations lifted out as links.
 *
 *  The engine has already verified every stamp against the transcript and rewritten it as
 *  a markdown link, so anything still here points at a real moment. Splitting on that link
 *  syntax — rather than matching the bare [MM:SS] — is what stops the URL leaking into the
 *  prose, which is exactly the bug the takeaway parser had.
 */
/** Prose with verified [MM:SS] links, split into paragraphs with the citations lifted out.
 *
 *  Shared by answers and by step expansions. Rendering either as a plain string dumps the
 *  raw markdown — "[55:43](https://youtu.be/XoSMi36OEIE?t=3343)" — into the middle of a
 *  sentence, which is precisely how it shipped (Denis, 2026-08-08). Consuming the WHOLE
 *  link, not just the [MM:SS], is what keeps the URL out of the text.
 */
export function parseCited(text: string): (string | Cite)[][] {
  const raw = text.replace(/\r/g, "").replace(/\*\*/g, "").trim();
  const linkRe = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]\(([^)]*)\)/g;

  return raw
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((para) => {
      const parts: (string | Cite)[] = [];
      let last = 0;
      for (const m of para.matchAll(linkRe)) {
        const before = para.slice(last, m.index);
        if (before) parts.push(before);
        parts.push({ stamp: m[1], href: m[2] } as Cite);
        last = (m.index ?? 0) + m[0].length;
      }
      const rest = para.slice(last);
      if (rest) parts.push(rest);
      return parts;
    });
}
