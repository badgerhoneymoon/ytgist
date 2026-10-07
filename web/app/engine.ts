// WHICH ENGINE THIS PAGE TALKS TO, in one place instead of five.
//
// The Mac's own engine by default. The launcher (`./ui`, the .app) points it at a remote engine
// (a PC with an NVIDIA GPU, configured in ~/.ytgist/remote) when that answers, by starting Next
// with YTGIST_ENGINE set; the layout hands that to the browser at request time as
// window.__YTGIST_PC__, and as window.__YTGIST_ENGINE__ unless this window was switched to the
// Mac because the PC was busy (sessionStorage, so it lasts until the window is closed).
declare global {
  interface Window { __YTGIST_ENGINE__?: string; __YTGIST_PC__?: string }
}

const server = typeof window === "undefined";
const chosen = server ? process.env.YTGIST_ENGINE : window.__YTGIST_ENGINE__;
export const LOCAL = "http://127.0.0.1:8765";
/** The PC's engine when the launcher found one, whether or not this window uses it. */
export const PC = (server ? process.env.YTGIST_ENGINE : window.__YTGIST_PC__) || "";
export const ENGINE = chosen || LOCAL;
/** True when a remote engine (the PC) is doing the work; the page says so. */
export const ON_PC = ENGINE !== LOCAL;
/** The PC is there, but this window was switched to the Mac. */
export const SWITCHED = !!PC && !ON_PC;
export const SWITCH_KEY = "ytgist-on";

/** Move this window to the Mac (or back to the PC) and load `next`. A full load, because
 *  every fetch on the page reads ENGINE once, at import. */
export function switchEngine(to: "mac" | "pc", next?: string) {
  try {
    if (to === "mac") sessionStorage.setItem(SWITCH_KEY, "mac");
    else sessionStorage.removeItem(SWITCH_KEY);
  } catch { /* storage refused: the reload just lands on the launcher's choice */ }
  window.location.href = next ?? window.location.pathname;
}
