// WHICH ENGINE THIS PAGE TALKS TO, in one place instead of five.
//
// The Mac's own engine by default. The launcher (`./ui`, the .app) points it at the PC (the
// RTX 5090, over Tailscale) when the PC answers, by starting Next with YTGIST_ENGINE set; the
// layout hands that to the browser at request time as window.__YTGIST_ENGINE__.
declare global {
  interface Window { __YTGIST_ENGINE__?: string }
}

const chosen = typeof window === "undefined" ? process.env.YTGIST_ENGINE : window.__YTGIST_ENGINE__;
export const ENGINE = chosen || "http://127.0.0.1:8765";
export const ON_PC = ENGINE.includes("desktop-8rqc3fq");
