/** The shapes the Python engine streams over SSE. Kept in one file so the UI and the
 *  backend contract are visible in a single place. */

export type Stage = "check" | "download" | "transcribe" | "summarise" | "cached" | "done";

export type Sentence = { start: number; end: number; text: string };

/** A progress frame, or the final frame carrying the result. */
export type Frame = {
  stage?: Stage;
  pct?: number;
  msg?: string;
  error?: string;
  // final frame only
  title?: string;
  markdown?: string;
  timings?: Record<string, number>;
  duration?: number;
  cached?: boolean;
  sentences?: Sentence[];
};

/** One parsed takeaway: a headline that states the point, a sentence of substance, and
 *  the transcript lines it came from — evidence sits WITH the claim, not in a footnote. */
export type Takeaway = {
  headline: string;
  body: string;
  seconds: number | null;
  stamp: string | null;
  evidence: string;
};

export type Gist = {
  title: string;
  tldr: string;
  takeaways: Takeaway[];
  videoId: string;
  timings: Record<string, number>;
  duration: number;
  cached: boolean;
};
