// WHICH ENGINE THIS PAGE TALKS TO, in one place instead of five.
//
// The Mac's own engine by default. The launcher (`./ui`, the .app) points it at a remote engine
// (a PC with an NVIDIA GPU, configured in ~/.ytgist/remote) when that answers, by starting Next
// with YTGIST_ENGINE set; the layout hands that to the browser at request time as
// window.__YTGIST_ENGINE__.
declare global {
  interface Window { __YTGIST_ENGINE__?: string }
}

const chosen = typeof window === "undefined" ? process.env.YTGIST_ENGINE : window.__YTGIST_ENGINE__;
const LOCAL = "http://127.0.0.1:8765";
export const ENGINE = chosen || LOCAL;
/** True when a remote engine (the PC) is doing the work; the page says so. */
export const ON_PC = ENGINE !== LOCAL;
