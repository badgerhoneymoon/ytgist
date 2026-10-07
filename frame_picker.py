#!/usr/bin/env python3
"""Let the model choose the screenshot. The only module that knows the model can see.

A frame grabbed blindly at a takeaway's timestamp is worse than no frame: on the many
videos that are one person talking, it staples fifteen identical headshots to fifteen
written claims. The August images feature was removed for exactly that, and it was
Wikimedia stock — at least these would be from the video.

So the frame is CHOSEN, by something that looks, with "nothing here" as a real answer:

    storyboard sheet (free, 3x3 tiles of 320x180)  →  "which cell, or NONE?"
                                                   →  720p grab of that one second
                                                   →  does it match the tile it came from?

MEASURED, on an M4 Max (2026-10-02):
  · a sheet verdict costs ~6s; a 720p grab ~6s more
  · talking-head podcast, 14 takeaways: 0 picks — correct
  · code-heavy lecture, 6 probes: 6 picks, all genuinely code

THE PROMPT ORDER IS LOAD-BEARING AND COUNTER-INTUITIVE. Naming the claim BEFORE describing
the cells produced NONE on a sheet that was wall-to-wall Python — 0/6. Asked whether a cell
PROVES a sentence, the model judges whether it can read it, and at 320x180 nothing is
readable, so it honestly declines. Describe first, decide second: 6/6. Do not "simplify"
this into one question.
"""
import os
import re

TILE_RE = re.compile(r"^\**(?:cell\s*)?(\d{1,2})\**[.:]?$", re.I)
NONE_RE = re.compile(r"^\**none\**[.!]?$", re.I)


def question(cells: int, per_tile: float, first_secs: float, headline: str,
             avoid=()) -> str:
    span = f"{int(first_secs) // 60}:{int(first_secs) % 60:02d}"
    # ALREADY SPOKEN FOR. Two takeaways minutes apart landed on the same cell of the same
    # sheet and got the same picture twice (Denis, 2026-10-02) — reasonably, since each
    # question was asked as if it were the only one. Saying which cells are taken costs a
    # line and removes the commonest duplicate.
    taken = (f"Cells {', '.join(str(c + 1) for c in sorted(avoid))} are already used for "
             "other points in this summary — do not choose them.\n\n" if avoid else "")
    return (
        f"A {cells}-cell grid of video thumbnails, numbered 1-{cells} left to right, top to "
        f"bottom, about {per_tile:.0f} seconds apart starting at {span}.\n\n"
        "First: what is actually visible in each cell? Say the SUBJECT — the object, "
        "machine, device, screen, slide, diagram, chart, code, experiment, place or "
        "demonstration — not whether its text is legible, since a tile is only 320x180.\n\n"
        f"Then, given the claim \"{headline}\", name the single cell that best SHOWS WHAT "
        "THE CLAIM IS ABOUT: the thing itself, being used, demonstrated, measured or drawn. "
        "Prefer a close view of the subject over a wide shot, and the subject over anyone "
        "describing it.\n\n"
        + taken +
        "Answer NONE only if every cell is a person talking, a logo or a title card with "
        "none of the subject visible, or if the only cells that fit are already used. A "
        "portrait of a speaker adds nothing to a written argument — but the thing they are "
        "talking about does.\n\n"
        "Reply with one line of what you see, then a final line that is the cell number "
        "alone, or the single word NONE."
    )


def verdict(answer: str, cells: int):
    """(cell, what_it_said) — cell is an index, None for "nothing here", or -1 for "it
    never got as far as deciding".

    THREE OUTCOMES, because the third one was silently wearing the second one's clothes.
    The decision is the last line, but a describe-then-decide answer that runs out of
    tokens ends mid-description, and reading that as NONE turned a truncated reply into a
    confident "there is nothing in this video" — which is how a film full of robot hands
    reported two screenshots out of seven (Denis, 2026-10-02).
    """
    lines = [l.strip() for l in answer.splitlines() if l.strip()]
    if not lines:
        return -1, ""
    saw = lines[0][:120]
    for line in reversed(lines):
        if NONE_RE.match(line):
            return None, saw
        m = TILE_RE.match(line)
        if m:
            n = int(m.group(1))
            if 1 <= n <= cells:
                return n - 1, saw
            break
    return -1, saw          # it described cells and never reached a verdict


def question_wide(cells: int, headline: str, spans: list) -> str:
    """The same question, asked of a sheet from ELSEWHERE in the video.

    A claim made at 00:40 is often illustrated at 04:10 — the speaker says what the hand
    can do, and the demonstration comes later (Denis, 2026-10-02). So when the moment's own
    sheet has nothing, the rest of the video is still worth asking about; the only thing
    that changes is that the timestamps are no longer "around here", so the answer must be
    judged on the subject alone.
    """
    where = ", ".join(spans)
    return (
        f"A {cells}-cell grid of video thumbnails from elsewhere in the same video "
        f"({where}), numbered 1-{cells} left to right, top to bottom.\n\n"
        "First: what is actually visible in each cell? Say the SUBJECT — the object, "
        "machine, device, screen, slide, diagram, chart, code, experiment, place or "
        "demonstration.\n\n"
        f"Then, given the claim \"{headline}\", name the single cell that SHOWS WHAT THE "
        "CLAIM IS ABOUT — the thing itself, being used, demonstrated, measured or drawn.\n\n"
        "These frames are from a different moment than the claim, so only answer with a "
        "cell if it genuinely shows that subject. Answer NONE if none of them do, or if "
        "they are only people talking, logos or title cards.\n\n"
        "Reply with one line of what you see, then a final line that is the cell number "
        "alone, or the single word NONE."
    )


# ----------------------------------------------------------------- does it match?
#
# The tile's second is a GUESS (yt-dlp derives storyboard timing from duration/frame_count
# rather than the real interval), so the grabbed frame is compared against the tile it was
# chosen from. A confidently wrong screenshot is the exact failure this feature exists to
# avoid, so a frame that does not match its tile is thrown away rather than shown.
#
# The comparison is a 16x9 greyscale signature and a mean absolute difference — no model
# call, no dependency. It is deliberately loose: the same shot re-encoded at 720p against a
# 320x180 tile differs everywhere in detail and nowhere in layout.
# 0-255 per cell. A frame from a DIFFERENT part of the video scores far above this; the
# same scene a few seconds off scores in the forties, because a 320x180 thumbnail and a
# 720p frame of a moving subject never agree in detail. The check exists to catch a frame
# from the wrong place, not to demand pixel identity — being stricter than this threw away
# correct screenshots of a fast-cut film (measured, 2026-10-02).
MATCH_TOLERANCE = 60


def _signature(path: str, cols: int = 16, rows: int = 9):
    """A tiny greyscale thumbprint: 144 bytes, straight out of ffmpeg.

    ffmpeg cannot decode YouTube's storyboard WebP — that is what sips is for — but both
    things being compared here are already JPEGs it reads happily, and asking it for raw
    grey pixels is one call with nothing to parse.
    """
    import subprocess
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path,
                        "-vf", f"scale={cols}:{rows}", "-pix_fmt", "gray",
                        "-frames:v", "1", "-f", "rawvideo", "-"],
                       capture_output=True)
    return list(r.stdout) if r.returncode == 0 and len(r.stdout) == cols * rows else None


def best_match(candidates: dict, tile_path: str):
    """(path, matched) — the candidate frame closest to the tile that was picked.

    `candidates` is {offset: path}. Ties go to the offset nearest the asked-for second,
    since that is still the best guess at the moment the model chose.
    """
    tile = _signature(tile_path)
    paths = [candidates[k] for k in sorted(candidates, key=lambda k: abs(k))]
    if not tile:
        return (paths[0] if paths else None), True      # no evidence against it
    best, best_d = None, None
    for p in paths:
        sig = _signature(p)
        if not sig or len(sig) != len(tile):
            continue
        d = sum(abs(x - y) for x, y in zip(sig, tile)) / len(tile)
        if best_d is None or d < best_d:
            best, best_d = p, d
    if best is None:
        return (paths[0] if paths else None), True
    return best, best_d <= MATCH_TOLERANCE


def matches(frame_path: str, tile_path: str) -> bool:
    """Is the grabbed frame the same moment as the tile that was picked?

    Unknown counts as YES: when the signature cannot be computed there is no evidence
    AGAINST the frame, and refusing every picture because a thumbnail decoder failed would
    be its own silent failure.
    """
    a, b = _signature(frame_path), _signature(tile_path)
    if not a or not b or len(a) != len(b):
        return True
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) <= MATCH_TOLERANCE


# ----------------------------------------------------------------- the pass itself

def tile_jpeg(sheet_path: str, cell: int, cols: int, rows: int, dest: str):
    """Crop one cell out of a sheet, for the match check. sips crops from the top-left."""
    import shutil
    import subprocess
    if not shutil.which("sips"):     # the PC (Linux): the same crop with Pillow
        try:
            from PIL import Image
            im = Image.open(sheet_path)
            tw, th = im.width // cols, im.height // rows
            x, y = (cell % cols) * tw, (cell // cols) * th
            im.crop((x, y, x + tw, y + th)).convert("RGB").save(dest, "JPEG", quality=90)
        except Exception:
            return None
        sig = _signature(dest)
        return None if (not sig or max(sig) - min(sig) < 4) else dest
    from_sips = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", sheet_path],
                               capture_output=True, text=True).stdout
    nums = [int(n) for n in re.findall(r"pixel(?:Width|Height): (\d+)", from_sips)]
    if len(nums) != 2:
        return None
    sw, sh = nums
    tw, th = sw // cols, sh // rows
    x, y = (cell % cols) * tw, (cell // cols) * th
    # --cropOffset is the TOP-LEFT of the crop, in pixels, as y then x. Treating it as an
    # offset from the centre produced a solid black rectangle for every tile — and since
    # the match check compares the grabbed frame against this, every correct frame was
    # measured against nothing, failed, and was reported as "nothing here". That is why a
    # film full of robot hands came back with two screenshots (Denis, 2026-10-02).
    r = subprocess.run(["sips", "-c", str(th), str(tw), "--cropOffset", str(y), str(x),
                        sheet_path, "--out", dest], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dest):
        return None
    # A tile that came out blank is not evidence about anything; saying so lets the caller
    # skip the comparison instead of trusting a black square.
    sig = _signature(dest)
    if sig and max(sig) - min(sig) < 4:
        return None
    return dest


# Nine descriptions and a verdict. At 120 it ran out of tokens around cell 7 on anything
# busier than a lecture slide, and the verdict never arrived.
ANSWER_TOKENS = 400


def looks_like(sig, kept, distance: int = 20) -> bool:
    """Is this frame one we have already shown?

    Cells are not the only way to repeat yourself: two different cells of the same sheet,
    five seconds apart, were the same shot of the same hand (Denis, 2026-10-02). Comparing
    the FRAMES catches that, where comparing their timestamps or their cells cannot. The
    threshold is deliberately tighter than the tile check — this asks "is this the same
    picture", not "is this the same scene".
    """
    return any(sig and k and len(sig) == len(k)
               and sum(abs(a - b) for a, b in zip(sig, k)) / len(sig) <= distance
               for k in kept)


def pick(srv, sheet_path: str, headline: str, cells: int, per_tile: float,
         first_secs: float, avoid=()):
    """(cell, what it saw) — cell is an index, None for nothing, -1 for no verdict.

    A reply that never reached a verdict is asked ONCE more, for the verdict alone. The
    describe-first order is what makes the model willing to pick at all, so the fix for a
    truncated description is a second question, not a shorter one.
    """
    return ask(srv, sheet_path, question(cells, per_tile, first_secs, headline, avoid), cells)


def ask(srv, sheet_path: str, q: str, cells: int):
    """Put one question to one sheet, and insist on getting a verdict back."""
    sheet = open(sheet_path, "rb").read()
    answer = srv.look(sheet, q, max_tokens=ANSWER_TOKENS)
    cell, saw = verdict(answer, cells)
    if cell == -1:
        again = srv.look(sheet, q + "\n\nAnswer with the cell number alone, or NONE.",
                         max_tokens=ANSWER_TOKENS)
        cell, saw2 = verdict(again, cells)
        saw = saw or saw2
    return cell, saw
