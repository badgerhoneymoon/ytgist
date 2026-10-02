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

TILE_RE = re.compile(r"^(?:cell\s*)?(\d{1,2})\.?$", re.I)


def question(cells: int, per_tile: float, first_secs: float, headline: str) -> str:
    span = f"{int(first_secs) // 60}:{int(first_secs) % 60:02d}"
    return (
        f"A {cells}-cell grid of video thumbnails, numbered 1-{cells} left to right, top to "
        f"bottom, about {per_tile:.0f} seconds apart starting at {span}.\n\n"
        "First: what kind of thing fills most of each cell? Judge the KIND — slide, chart, "
        "diagram, code editor, terminal, screen share, demo, or a person talking — not "
        "whether its text is legible, since a tile is only 320x180.\n\n"
        f"Then, given the claim \"{headline}\", name the single best cell to show beside it, "
        "or NONE if every cell is only a person or a room. A picture of someone's face adds "
        "nothing to a written argument.\n\n"
        "Reply with one line of what you see, then a final line that is the cell number "
        "alone, or the single word NONE."
    )


def verdict(answer: str, cells: int):
    """(cell_index_or_None, what_it_said). The decision is the LAST line; the first line is
    what it saw, which is worth keeping for the log when a pick looks wrong."""
    lines = [l.strip() for l in answer.splitlines() if l.strip()]
    if not lines:
        return None, ""
    m = TILE_RE.match(lines[-1])
    if not m:
        return None, lines[0][:120]
    n = int(m.group(1))
    if not 1 <= n <= cells:
        return None, lines[0][:120]
    return n - 1, lines[0][:120]


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
MATCH_TOLERANCE = 46          # 0-255 per cell; a different scene scores far above this


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
    import subprocess
    from_sips = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", sheet_path],
                               capture_output=True, text=True).stdout
    nums = [int(n) for n in re.findall(r"pixel(?:Width|Height): (\d+)", from_sips)]
    if len(nums) != 2:
        return None
    sw, sh = nums
    tw, th = sw // cols, sh // rows
    x, y = (cell % cols) * tw, (cell // cols) * th
    # --cropOffset is relative to the CENTRE for sips' crop, so offset from there.
    r = subprocess.run(["sips", "-c", str(th), str(tw),
                        "--cropOffset", str(int(y + th / 2 - sh / 2)),
                        str(int(x + tw / 2 - sw / 2)),
                        sheet_path, "--out", dest], capture_output=True, text=True)
    return dest if r.returncode == 0 and os.path.exists(dest) else None


def pick(srv, sheet_path: str, headline: str, cells: int, per_tile: float,
         first_secs: float):
    """(cell or None, what it saw). Raises ModelError only if the server itself fails."""
    answer = srv.look(open(sheet_path, "rb").read(),
                      question(cells, per_tile, first_secs, headline))
    return verdict(answer, cells)
