#!/usr/bin/env python3
"""Five readings of "more detail", on one passage, so the choice can be made by looking.

    python3 expand_ab.py [video_id] [start_seconds] [end_seconds]

Defaults to the Tinkov/Varlamov $100M passage (55:11 → 58:27), which is a good test
because it contains BOTH a claim and the anecdote that proves it — so the variants that
chase specifics and the ones that chase narrative pull in visibly different directions.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gist_prompt                                            # noqa: E402
import model_client                                           # noqa: E402
import ytgist                                                 # noqa: E402

HEADLINE = "Money loses meaning after $100M"
BODY = ("Tinkov claims life quality peaks at $100 million. More money becomes just "
        "numbers. He cannot spend it faster than he earns.")

# Applied to EVERY variant, so the comparison is about shape and not about which one
# happened to guess a name. Parakeet garbles foreign proper nouns, and a model that
# translates a garbled name produces a clean-looking wrong one: «Форс» (his banker) became
# "Forex", «эспан Энио» (Espen Øino) became "Eneio". A wrong name that reads as correct is
# worse than no name (2026-08-08).
NAMES = """
NAMES: the transcript is automatic and mangles foreign names. If a name is not clearly
readable, do NOT guess it and do NOT translate it into something that looks right —
describe the role instead ("his banker", "the yacht designer"). Only write a name you can
read unambiguously.
"""

COMMON = """
- Only what the passage below says. No background, no inference, no knowledge of your own.
- Each sentence under 20 words. One idea per sentence.
- Cite [MM:SS] once or twice, copied exactly from the passage.
- Do not repeat the summary.
"""

NARRATIVE = """
- Only what the passage below says. No background, no inference, no knowledge of your own.
- Cite [MM:SS] once or twice, copied exactly from the passage.
- Do not repeat the summary.
- Write it as PROSE, not as a list of facts in sentence form. Vary the sentence length.
  Join the beats with connectives — so, and then, but, until — so the events follow one
  another instead of sitting side by side.
"""

VARIANTS = {
    "B1 · told plainly": """The summary states a claim. Tell the STORY the speaker used to
make it — what happened, in the order it happened, with the specifics inside the narrative.
One episode. Write it the way you would tell it to someone: a short paragraph that moves,
not a list. 4-6 sentences.""",

    "B2 · with the turn": """The summary states a claim. Tell the EPISODE behind it as a short
paragraph. Every episode has a turn — the moment something changed, stopped, or went wrong.
Build to that turn and end on it. Keep the concrete numbers inside the story where they
belong. 4-6 sentences.""",
}

_OLD = {
    "A · specifics": """Give the DETAIL the summary left out — the concrete specifics that are
actually in the passage: numbers, dates, amounts, sizes, the exact mechanism.
2-5 sentences.""",

    "B · the episode": """The summary states a claim. Tell the STORY the speaker used to make
that claim — what actually happened, in the order it happened, with the specifics inside the
narrative rather than listed. One episode, not a survey of the passage. If the passage
contains no episode, say NOTHING FURTHER.
3-5 sentences.""",

    "C · the reasoning": """Show HOW the speaker gets to this claim — the premise, the step
between, and what follows from it. Name the mechanism, not the conclusion. If the passage
just asserts the claim without reasoning, say NOTHING FURTHER.
2-4 sentences.""",

    "D · his own words": """Quote the 2-3 sentences from the passage that carry the point best,
in the speaker's ORIGINAL language, exactly as transcribed. No paraphrase, no translation,
no commentary. One per line.""",

    "E · adaptive": """Decide what this passage actually offers, then give that:
- if it contains an EPISODE (something that happened), tell that story;
- else if it contains REASONING (a premise leading somewhere), lay out the steps;
- else give the concrete SPECIFICS — numbers, dates, amounts.
Begin with one word on its own line saying which you chose: EPISODE, REASONING or SPECIFICS.
Then 2-5 sentences.""",
}


VARIANTS = {k: v for k, v in VARIANTS.items()}


def main():
    vid = sys.argv[1] if len(sys.argv) > 1 else "XoSMi36OEIE"
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 3311
    end = int(sys.argv[3]) if len(sys.argv) > 3 else 3507

    cached = ytgist.load_cached(vid)
    window = [x for x in cached["sentences"] if start - 5 <= x["start"] <= end]
    transcript = gist_prompt.format_transcript(window)
    print(f"passage {start//60}:{start%60:02d} → {end//60}:{end%60:02d}, "
          f"{len(window)} segments, {len(transcript)} chars\n")

    need = len(transcript) // 2 + 1200
    srv = model_client.Server.acquire(need_tokens=need, model=ytgist.MODELS["dense"],
                                      log=lambda m: print(" ", m))
    out = {}
    with srv:
        for name, task in VARIANTS.items():
            user = (f"This is one passage of a talk. A summary of it reads:\n\n"
                    f"    {HEADLINE}\n    {BODY}\n\n{task}\n\nRules:{NARRATIVE}{NAMES}\n"
                    f"Write in English unless told otherwise above.\n\n"
                    f"PASSAGE\n<<<\n{transcript}\n>>>")
            t0 = time.time()
            text = srv.chat(gist_prompt.expand_system_for(False), user, max_tokens=420, temperature=0.2)
            out[name] = (gist_prompt.verify(text.strip(), cached["sentences"], vid)[0],
                         time.time() - t0)

    for name, (text, took) in out.items():
        print(f"\n{'=' * 78}\n{name}   ({took:.0f}s)\n{'=' * 78}")
        print(gist_prompt.sanitize(text))

    json.dump({k: v[0] for k, v in out.items()},
              open("/tmp/expand_ab.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
