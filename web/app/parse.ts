import type { Frame, Gist, Sentence, StepImage, Takeaway } from "./types";

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
