/** The shapes the Python engine streams over SSE. Kept in one file so the UI and the
 *  backend contract are visible in a single place. */

// "frames" is the screenshot pass. It is a stage the ENGINE reports but the progress bar
// never draws: that bar is a run's four phases against their ETAs, and this is a separate
// job that reports itself inside the result.
export type Stage =
  | "check" | "download" | "transcribe" | "summarise" | "cached" | "done" | "frames";

export type Sentence = { start: number; end: number; text: string };

/** A progress frame, or the final frame carrying the result. */
export type Frame = {
  stage?: Stage;
  pct?: number;
  msg?: string;
  error?: string;
  stopped?: boolean;
  expansions?: Record<string, string>;   // step start second → saved detail
  gpu?: GpuStats;
  gpu_series?: GpuSample[];
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
  // The screenshot pass: one verdict per takeaway, in the order they are read, plus a
  // sentence about how the search itself went. Both arrive with a finished summary and
  // again when a search finishes.
  frames?: Shot[];
  frames_outcome?: string;
  frames_done?: boolean;
};

/** What the screenshot search concluded about ONE takeaway. Three outcomes, deliberately
 *  distinct: a failed search and an empty one both end with no picture, and calling the
 *  first "nothing to show" would hide a broken download behind a tidy sentence.
 *
 *  Named Shot, not Frame: `Frame` in this file is one SSE message. */
export type Shot =
  | { state: "found"; secs: number }
  | { state: "none" }
  | { state: "failed"; why?: string };

export type Takeaway = {
  headline: string;
  body: string;
  seconds: number | null;
  stamp: string | null;
  evidence: string;
  // Saved detail for this step, if it was expanded before. "" means asked-and-nothing,
  // which is different from null (never asked) and must survive a reload as such.
  expansion: string | null;
  // This step's screenshot verdict, or null when nothing has ever looked.
  frame: Shot | null;
};

export type Gist = {
  title: string;
  tldr: string;
  takeaways: Takeaway[];
  videoId: string;
  timings: Record<string, number>;
  duration: number;
  cached: boolean;
  // "" when no screenshot search has ever run for this summary — which is why the button
  // can say "Find screenshots" the first time and report what happened afterwards.
  framesOutcome: string;
};

/** A cited moment inside an answer: the model wrote [MM:SS], the engine verified it
 *  against the transcript and turned it into a link. */
export type Cite = { stamp: string; href: string };

/** What the machine is doing while it works. Temperature only when macmon is installed;
 *  utilisation and GPU-held memory always. */
export type GpuStats = {
  util?: number;
  mem_gb?: number;
  gpu_c?: number;
  cpu_c?: number;
  watts?: number;
  fan_rpm?: number;
};

/** One second of machine state. `c` is GPU °C, `u` utilisation %, `w` watts. */
export type GpuSample = {
  t: number;
  c: number | null;
  cpu: number | null;
  u: number | null;
  w: number | null;
};
