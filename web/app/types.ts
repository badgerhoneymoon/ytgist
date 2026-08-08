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
  stopped?: boolean;
  eta?: Record<string, number>;   // seconds per remaining phase, sent once length is known
  video_minutes?: number;
  // final frame only
  title?: string;
  markdown?: string;
  raw?: string;          // unrendered — what the UI parses
  timings?: Record<string, number>;
  duration?: number;
  cached?: boolean;
  sentences?: Sentence[];
  images?: (StepImage | null)[];   // index-aligned with the takeaways
};

/** A Wikipedia page image for the thing a step names. Optional by design: the model says
 *  "IMAGE: none" for feelings, trends and statistics, and an unresolvable name gets
 *  nothing rather than a confident wrong picture. */
export type StepImage = {
  src: string;
  label: string;
  href: string;
  query: string;
};

/** One parsed takeaway: a headline that states the point, a sentence of substance, and
 *  the transcript lines it came from — evidence sits WITH the claim, not in a footnote. */
export type Takeaway = {
  headline: string;
  body: string;
  seconds: number | null;
  stamp: string | null;
  evidence: string;
  image: StepImage | null;
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
