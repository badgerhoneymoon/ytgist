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
  expansions?: Record<string, string>;   // step start second → saved detail
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
};

export type Takeaway = {
  headline: string;
  body: string;
  seconds: number | null;
  stamp: string | null;
  evidence: string;
  // Saved detail for this step, if it was expanded before. "" means asked-and-nothing,
  // which is different from null (never asked) and must survive a reload as such.
  expansion: string | null;
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

/** A cited moment inside an answer: the model wrote [MM:SS], the engine verified it
 *  against the transcript and turned it into a link. */
export type Cite = { stamp: string; href: string };
