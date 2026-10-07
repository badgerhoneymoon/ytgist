"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { LOCAL, ON_PC, PC, SWITCHED, switchEngine } from "./engine";

/** BEFORE A JOB GOES TO THE PC, ASK WHETHER ITS GPU IS FREE.
 *
 *  A busy GPU used to mean a silent wait behind someone's render (Denis, 7 Oct: "check if the
 *  PC is busy and give a confirmation pop-up: run it on the Mac?"). Busy = someone else's
 *  booking, a ComfyUI render nobody booked, or a research run of our own (the engine's
 *  GET /api/gpu). Not busy, or no answer within 2.5 s, and the job just goes to the PC.
 *  The Mac is only offered when its own engine answers.
 */
export type Where = "here" | "mac" | "cancel";

async function getJson(url: string, ms: number) {
  const r = await fetch(url, { signal: AbortSignal.timeout(ms) });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

export function useBusyGate(): [(what: string) => Promise<Where>, ReactNode] {
  const [ask, setAsk] = useState<{ text: string; what: string; macUp: boolean } | null>(null);
  const resolver = useRef<((w: Where) => void) | null>(null);

  const gate = useCallback(async (what: string): Promise<Where> => {
    if (!ON_PC || !PC) return "here";
    let b: { busy?: boolean; text?: string };
    try { b = await getJson(`${PC}/api/gpu`, 2500); } catch { return "here"; }
    if (!b.busy) return "here";
    let macUp = false;
    try { await getJson(`${LOCAL}/api/limits`, 1500); macUp = true; } catch { /* stays false */ }
    return new Promise<Where>((resolve) => {
      resolver.current = resolve;
      setAsk({ text: b.text || "Someone else is using the GPU.", what, macUp });
    });
  }, []);

  const answer = useCallback((w: Where) => {
    setAsk(null);
    resolver.current?.(w);
    resolver.current = null;
  }, []);

  useEffect(() => {
    if (!ask) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") answer("cancel"); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ask, answer]);

  const dialog = ask && (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/25 p-5 backdrop-blur-[2px]"
         onClick={() => answer("cancel")}>
      <div role="dialog" aria-modal="true" aria-labelledby="busy-title"
           onClick={(e) => e.stopPropagation()}
           className="w-full max-w-[27rem] rounded-2xl border border-line bg-canvas p-6 shadow-2xl"
           style={{ animation: "rise .25s var(--ease-out-expo)" }}>
        <h2 id="busy-title" className="flex items-center gap-2 text-[17px] font-semibold text-ink">
          <span className="h-2 w-2 rounded-full bg-accent" />
          The PC is busy
        </h2>
        <p className="mt-2.5 text-[14.5px] leading-[1.5] text-body">{ask.text}</p>
        <p className="mt-2 text-[13.5px] leading-[1.5] text-soft">
          {ask.macUp
            ? `Run ${ask.what} on this Mac instead? It's slower, and it goes into the Mac's library. Or it waits here and starts when the GPU is free.`
            : `This Mac's engine isn't running, so ${ask.what} can only wait for the PC. It starts when the GPU is free.`}
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-2.5">
          {ask.macUp && (
            <button autoFocus onClick={() => answer("mac")}
                    className="rounded-xl bg-ink px-4 py-2.5 text-[14px] font-medium text-canvas hover:opacity-90">
              Run on this Mac
            </button>
          )}
          <button autoFocus={!ask.macUp} onClick={() => answer("here")}
                  className="rounded-xl border border-line px-4 py-2.5 text-[14px] font-medium text-ink hover:border-ink">
            Wait for the PC
          </button>
          <button onClick={() => answer("cancel")} className="px-2 py-2.5 text-[14px] text-soft hover:text-ink">
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
  return [gate, dialog];
}

const noSubscribe = () => () => {};

/** "● on the PC" / "● on this Mac", plus "use the PC" when this window was switched away from
 *  it. Rendered after hydration only: the server can't see a window's switch, and a label
 *  that disagrees with the server's HTML would be a hydration error. */
export function EngineLabel() {
  const hydrated = useSyncExternalStore(noSubscribe, () => true, () => false);
  if (!hydrated) return <span className="invisible px-1.5 py-1.5 text-[12px]">on the PC</span>;
  return (
    <span
      title={ON_PC ? "transcribing and summarising on the RTX 5090"
        : SWITCHED ? "you chose this Mac while the PC was busy" : "the PC is off, so this Mac does the work"}
      className="flex items-center gap-1.5 rounded-lg px-1.5 py-1.5 text-[12px] text-soft"
    >
      <span className={`h-1.5 w-1.5 rounded-full ${ON_PC ? "bg-good" : "bg-soft/50"}`} />
      {ON_PC ? "on the PC" : "on this Mac"}
      {SWITCHED && (
        <button onClick={() => switchEngine("pc")}
                className="ml-1 font-medium text-ink underline decoration-line underline-offset-2 hover:text-accent">
          use the PC
        </button>
      )}
    </span>
  );
}
