"""Compare models on two research steps, side by side: picking the videos and summarising one.

    python tools/compare_steps.py picks <run_id,...> <writer,...>
    python tools/compare_steps.py gists <video_id,...> <writer,...>

A writer is "local" (this engine's model) or a Claude model id ("claude-haiku-5-5").
Picks: the run's own search queries are searched ONCE, and every writer scores that same pool.
Gists: every writer gets the exact prompt ytgist uses, built from the cached transcript, and
the same timestamp check (gist_prompt.verify). "local" reuses the saved summary when it was
made with the current prompt version, so the GPU is only used if it has to be.
Results: ~/.ytgist/research/compare/steps/{picks,gists}/<id>/<writer>.json (kept between runs).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_client  # noqa: E402
import gist_prompt  # noqa: E402
import model_client  # noqa: E402
import research  # noqa: E402
import shed  # noqa: E402
import ytgist  # noqa: E402

OUT = os.path.join(research.DIR, "compare", "steps")


def _save(kind, key, writer, data):
    path = os.path.join(OUT, kind, key, writer + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _have(kind, key, writer):
    return os.path.exists(os.path.join(OUT, kind, key, writer + ".json"))


def picks(runs, writers):
    for rid in runs:
        r = research.load(rid)
        pool_path = os.path.join(OUT, "picks", rid, "_pool.json")
        if os.path.exists(pool_path):
            cands = json.load(open(pool_path, encoding="utf-8"))
        else:
            # runs from before multi-query search have no saved queries: search the topic as typed
            cands = research.search_all(r.get("queries") or [r["topic"]], limit=72 if r.get("wider_at") else 48)
            os.makedirs(os.path.dirname(pool_path), exist_ok=True)
            json.dump(cands, open(pool_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for w in writers:
            if _have("picks", rid, w):
                continue
            usage = {}
            t0 = time.time()
            if w == "local":
                with shed.hold("ytgist: picker comparison (local model)"):
                    chosen, subject = research.pick(r["topic"], cands, 10, writer="local")
            else:
                # count tokens for the cost line: call through _llm's usage hook
                orig = research._llm
                research._llm = lambda wr, *a, **k: orig(wr, *a, **{**k, "usage": usage})
                try:
                    chosen, subject = research.pick(r["topic"], cands, 10, writer=w)
                finally:
                    research._llm = orig
            data = {"topic": r["topic"], "pool": len(cands), "subject": subject,
                    "picks": chosen, "secs": round(time.time() - t0, 1), "usage": usage,
                    "cost_usd": claude_client.total_cost(usage) if usage else None}
            _save("picks", rid, w, data)
            print(rid, w, f"{data['secs']}s", len(chosen), "picks", "subject:", subject,
                  "cost", data["cost_usd"], flush=True)


def gists(vids, writers):
    for vid in vids:
        cached = ytgist.load_cached(vid)
        if not cached:
            print(vid, "no cached transcript")
            continue
        sentences = cached["sentences"]
        user = gist_prompt.GIST_USER.format(
            transcript=gist_prompt.format_transcript(sentences),
            steps=gist_prompt.steps_for(cached.get("duration", 0) / 60)) + gist_prompt.ENGLISH_RULE
        system = gist_prompt.system_for(False)
        for w in writers:
            if _have("gists", vid, w):
                continue
            t0, usage, reused = time.time(), {}, False
            if w == "local":
                saved = ytgist.load_summary(vid, False)
                if saved and saved.get("gist_v") == ytgist.GIST_V:
                    out, reused = saved["text"], True
                else:
                    with shed.hold("ytgist: summary comparison (local model)"):
                        est = len(system + user) // 2 + 1400
                        with model_client.Server.acquire(need_tokens=est, model=ytgist.MODELS["dense"]) as srv:
                            out = srv.chat(system, user)
            else:
                out, usage = claude_client.chat(w, system, user, max_tokens=4000)
            text, dropped = gist_prompt.verify(out, sentences, vid)
            data = {"video": vid, "title": cached.get("title", ""), "minutes": round(cached.get("duration", 0) / 60, 1),
                    "text": text, "invented_timestamps_dropped": dropped, "reused_saved": reused,
                    "secs": None if reused else round(time.time() - t0, 1), "usage": usage,
                    "cost_usd": claude_client.cost(w, usage) if usage else None}
            _save("gists", vid, w, data)
            print(vid, w, f"{data['secs']}s", "dropped", dropped, "cost", data["cost_usd"],
                  "chars", len(text), flush=True)


if __name__ == "__main__":
    kind, ids, ws = sys.argv[1], sys.argv[2].split(","), sys.argv[3].split(",")
    {"picks": picks, "gists": gists}[kind](ids, ws)
