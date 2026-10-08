"""Write the same research briefs with different models, for a blind comparison.

    python tools/compare_writers.py <run_id,run_id,...> <writer,writer,...>

A writer is "local" (the model this engine runs) or a Claude model id such as
"claude-sonnet-5-5". Every writer gets the same video summaries, the same prompt and the same
citation check (done by that writer). Results go to ~/.ytgist/research/compare/<run>/<writer>.json;
existing files are kept, so the script can be re-run to fill gaps.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import research  # noqa: E402
import shed  # noqa: E402

OUT = os.path.join(research.DIR, "compare")


def main(runs, writers):
    for rid in runs:
        r = research.load(rid)
        if not r:
            print(rid, "no such run")
            continue
        for w in writers:
            path = os.path.join(OUT, rid, w + ".json")
            if os.path.exists(path):
                continue
            t0 = time.time()
            try:
                # Each writer checks its own citations, as in the 8 Oct comparison.
                if w == "local":
                    with shed.hold("ytgist: brief comparison (local model)"):
                        rep = research.combine(r, writer="local", checker="local")
                else:
                    rep = research.combine(r, writer=w, checker=w)
            except Exception as e:      # one failure must not stop the others
                print(rid, w, "FAILED", e, flush=True)
                continue
            rep["secs"] = round(time.time() - t0, 1)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=1)
            print(rid, w, f"{rep['secs']}s", len(rep["takeaways"]), "takeaways",
                  "cost", rep.get("cost_usd"), "unsupported", rep.get("unsupported_dropped"),
                  "cut", rep.get("takeaways_dropped"), flush=True)


if __name__ == "__main__":
    main(sys.argv[1].split(","), sys.argv[2].split(","))
