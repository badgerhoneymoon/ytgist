#!/usr/bin/env python3
"""A picture for each step — the ENTITIES the step is about, not the speaker.

Denis, 2026-08-08: "the takeaway is not about Shulman, it's about some ideas, concepts".
Right: a portrait of the presenter illustrates nothing. A photo of Zhirinovsky next to
"Zhirinovsky died, leaving an unfit successor" is genuine information — the same job a
Wikipedia infobox thumbnail does.

WHY WIKIPEDIA'S PAGE IMAGE, NOT A COMMONS SEARCH. Searching Commons for "Ekaterina
Schulmann" returned three arbitrary photos of her; asking Wikipedia "what is the picture
for this article" returns the canonical one — the portrait, the party logo, the flag.
Commons full-text search optimises for filename matches, which is not the same question.

DEGRADES TO NOTHING. A step with no named entity, or an entity with no image, shows no
image at all. Never a placeholder, never a loosely-related stock photo: an image that
merely gestures at the topic implies things the speaker never said, and this is political
material where that is a real cost.
"""
import concurrent.futures as cf
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 6
UA = "ytgist/1.0 (personal tool; contact via github.com/badgerhoneymoon)"

# Capitalised runs are the cheap, language-agnostic way to spot names of people, parties
# and organisations in both Cyrillic and Latin text. Deliberately NOT a model call: this
# runs per step, and a 27B round-trip per name would cost more than the whole summary.
_NAME = re.compile(
    r"\b([A-ZА-ЯЁ][\wА-Яа-яЁё\-]+(?:\s+[A-ZА-ЯЁ][\wА-Яа-яЁё\-]+){0,2})"
)
# Sentence-openers and generic words that pass the capitalisation test but name nothing.
_STOP = {
    "The", "This", "That", "They", "But", "So", "However", "Therefore", "Which",
    "Consequently", "Higher", "Citizens", "People", "It", "There", "These", "Public",
    "Fear", "Он", "Она", "Это", "Но", "Так", "Вот", "Если", "Как", "Однако", "Поэтому",
    "Речь", "Тогда", "Здесь", "Люди", "Государство",
}


def candidates(text: str, limit: int = 3):
    """Likely entity names in a step, most promising first."""
    seen, out = set(), []
    for m in _NAME.finditer(text):
        name = m.group(1).strip(" .,:;—-")
        # STRIP leading stopwords rather than discarding the span. A sentence opening
        # "But Vladimir Zhirinovsky died…" matches from "But", and dropping the whole
        # match threw away the name — which is why the first strict version found
        # nothing at all.
        words = name.split()
        while words and words[0] in _STOP:
            words.pop(0)
        name = " ".join(words)
        if len(name) < 4 or name in seen:
            continue
        seen.add(name)
        # Also try the trailing pair/singleton: "Vladimir Zhirinovsky died" → the name is
        # the first two words, not all three.
        if len(words) > 2:
            for sub in (" ".join(words[:2]), words[0]):
                if sub not in seen and len(sub) >= 4:
                    seen.add(sub)
                    out.append(sub)
        # Multi-word names (Vladimir Zhirinovsky, Сергей Кириенко) beat single words.
        out.append(name)
    out.sort(key=lambda n: (-len(n.split()), -len(n)))
    return out[:limit]


def _api(lang: str, params: dict):
    url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def image_for(name: str, lang: str = "ru"):
    """(thumb_url, article_title, page_url) for a name, or None. Never raises.

    EXACT TITLE + REDIRECTS, never a search. `generator=search` returns the top search
    hit for ANY string, so it always answers — and answers confidently wrong: it matched
    "Kirienko strategy to weaken New People" to Pavel Durov, and "Replacing KPRF with
    LDPR" to a Union of Communist Parties. A picture of the wrong person beside a
    political claim is worse than no picture, which is the whole reason this file exists.
    Exact resolution declines to answer instead — precision over coverage, deliberately."""
    # Both wikis, always: the transcript may be Russian while the SUMMARY is English, so
    # the names arrive transliterated ("Vladimir Zhirinovsky", not "Жириновский").
    for lg in ("en", lang) if lang != "en" else ("en", "ru"):
        try:
            d = _api(lg, {
                "action": "query", "format": "json", "prop": "pageimages",
                "piprop": "thumbnail", "pithumbsize": "320", "pilicense": "any",
                "titles": name, "redirects": "1",
            })
            q = d.get("query") or {}
            pages = q.get("pages") or {}
            for pid, p in pages.items():
                if str(pid).startswith("-"):
                    continue                     # no such article
                thumb = (p.get("thumbnail") or {}).get("source")
                if not thumb:
                    continue
                title = p.get("title", name)
                # A redirect is fine (Жириновский → Жириновский, Владимир Вольфович) but
                # the result must still be ABOUT the thing we asked for: require a shared
                # word, so a stray redirect can't smuggle in an unrelated article.
                want = {w.lower().strip(",.") for w in name.split() if len(w) > 3}
                got = {w.lower().strip(",.") for w in title.split() if len(w) > 3}
                if want and not (want & got):
                    continue
                return (thumb, title,
                        f"https://{lg}.wikipedia.org/wiki/"
                        + urllib.parse.quote(title.replace(" ", "_")))
        except Exception:
            continue
    return None


def commons_search(query: str):
    """Shoebill's approach, borrowed: search Commons FILES, prefer JPEG, prefer large.

    Used only as a FALLBACK after exact-title resolution fails, because search always
    answers — and answers confidently wrong when it has nothing good (it matched
    'Kirienko strategy' to Pavel Durov). Progressive simplification is Shoebill's too:
    try the full query, then its first three words."""
    for q in [query, " ".join(query.split()[:3])][: 2 if len(query.split()) > 3 else 1]:
        try:
            d = _api_commons({
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": q, "gsrnamespace": "6", "gsrlimit": "5",
                "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": "400",
            })
            cands = []
            for p in ((d.get("query") or {}).get("pages") or {}).values():
                info = (p.get("imageinfo") or [{}])[0]
                if info.get("mime", "").startswith("image/") and info.get("thumburl"):
                    cands.append((info.get("mime") != "image/jpeg",
                                  -int(info.get("thumbwidth") or 0),
                                  info["thumburl"], p.get("title", q)))
            if cands:
                cands.sort()
                _, _, url, title = cands[0]
                return (url, title.replace("File:", ""),
                        "https://commons.wikimedia.org/wiki/"
                        + urllib.parse.quote(title.replace(" ", "_")))
        except Exception:
            continue
    return None


def _api_commons(params):
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def illustrate_named(steps, lang="ru"):
    """Attach an image to steps whose IMAGE: line names a resolvable entity.

    Each step carries `image_query` = "<name> | <what kind of thing>", written by the
    model. Resolution is Wikidata-only and refuses on any doubt — see the block below for
    why both search-based designs were deleted rather than tuned."""
    del lang                                  # Wikidata search is language-agnostic here

    def ask(st):
        raw = (st.get("image_query") or "").strip()
        if not raw or raw.lower().split("|")[0].strip() in ("none", "нет", "-", ""):
            return
        name, _, kind = raw.partition("|")
        hit = resolve_entity(name.strip(), kind.strip())
        if hit:
            st["_qid"], st["_label"], st["_desc"] = hit

    # CONCURRENTLY. Each lookup is a network round-trip that spends its time waiting, so
    # seventeen in series cost 31 seconds of wall clock for a few hundred ms of work
    # (measured 2026-08-09). Four at a time: enough to hide the latency, few enough to stay
    # a polite client — Wikimedia rate-limits bursts and answers 429.
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(ask, steps))
    wanted = [st["_qid"] for st in steps if st.get("_qid")]

    files = entity_images(wanted)             # ONE request for every entity at once
    for st in steps:
        qid = st.pop("_qid", None)
        label, desc = st.pop("_label", ""), st.pop("_desc", "")
        if qid and qid in files:
            st["image"] = {
                "src": commons_thumb(files[qid][0]),
                "label": label,
                "href": f"https://www.wikidata.org/wiki/{qid}",
                "query": label,               # the RESOLVED name, not what we searched for
                "note": desc,                 # Wikidata's own one-liner, shown as caption
            }
    return steps


def illustrate(steps, lang="ru"):
    """Attach at most one image per step. Steps keep their order and their text; a step
    we cannot illustrate is returned untouched."""
    used = set()
    for st in steps:
        text = f"{st.get('headline','')} {st.get('body','')}"
        for name in candidates(text):
            if name.lower() in used:
                continue
            hit = image_for(name, lang)
            if hit:
                st["image"] = {"src": hit[0], "label": hit[1], "href": hit[2]}
                used.add(name.lower())
                break
    return steps


# ---------------------------------------------------------------- Wikidata resolution
#
# FIRST PRINCIPLES, after two failed designs (Denis: "our experiment with images is not
# very successful… think from first principles").
#
# Question the requirement. The goal was never "an image per step" — it was "make the
# page less of a text wall". A WRONG image fails that goal harder than a blank space,
# because the reader stops to work out why Hallowe'en apple-bobbing is next to a claim
# about Russian electoral politics. So the requirement is: add an image ONLY when we can
# prove what it depicts. Coverage is not the metric; precision is.
#
# Delete. Both previous mechanisms were free-text SEARCH — Commons file search, then
# Wikipedia article search. Both share one fatal property: they always return something.
# A search engine has no way to say "that isn't a thing". Deleted, not tuned.
#
# Simplify. Wikidata is an entity database, not an index, so it CAN refuse — measured:
# "Apple party" → nothing, "public anxiety" → nothing, "Kirienko strategy" → nothing.
# Exactly the three cases that produced garbage. And each candidate carries a one-line
# description, which turns the one remaining failure mode (ambiguity: "Яблоко" is also a
# fruit, "FOM" is also a fungus) into a solvable matching problem — the model already
# knows which sense it meant, so it declares the TYPE and we match on that.
#
# Accelerate. One batched call fetches the images for every entity in a summary.
# Automate. Resolutions are cached on disk; a repeated entity costs nothing.

_WD = "https://www.wikidata.org/w/api.php"

# Descriptions that mean "this is a page ABOUT a thing", not the thing itself.
_NOT_A_SUBJECT = ("disambiguation", "wikimedia", "wikipedia", "scientific article",
                  "encyclopedia article", "article in", "family name", "given name",
                  "surname", "list of", "genus of", "species of")

_STOP = frozenset(
    "a an the of in on for and or to is was were by with its their this that from at "
    "russian russia soviet former current".split()
)


def _words(t):
    return {w for w in re.findall(r"[\w']+", (t or "").lower())
            if w not in _STOP and len(w) > 2}


def _wd(params, tries=3):
    """Wikimedia answers 429 under any burst, and the old code swallowed it — which
    silently produced a summary with no images and no explanation. Back off instead."""
    url = _WD + "?" + urllib.parse.urlencode({**params, "format": "json"})
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and n < tries - 1:
                time.sleep(1.5 * (n + 1))
                continue
            return {}
        except Exception:
            return {}
    return {}


def resolve_entity(name: str, kind: str):
    """(qid, label, description) for a named thing, or None.

    `kind` is the model's own 2-4 word description of what sort of thing it means. It is
    load-bearing: without it "Яблоко" resolves to the fruit and "FOM" to a genus of
    fungi. With it, both land correctly — measured."""
    if not name:
        return None
    hits = _wd({"action": "wbsearchentities", "language": "en", "uselang": "en",
                "type": "item", "limit": 7, "search": name}).get("search", [])
    want = _words(kind)
    best, best_score = None, 0
    for h in hits:
        desc = h.get("description") or ""
        if any(b in desc.lower() for b in _NOT_A_SUBJECT):
            continue
        score = len(want & _words(desc))
        if score > best_score:
            best, best_score = (h["id"], h.get("label") or name, desc), score
    # A hit with NO overlap is an accidental string match, which is precisely how the old
    # designs failed. Require the type to agree.
    return best if best_score >= 1 else None


def entity_images(qids):
    """{qid: (commons_filename, property)} for many entities in ONE request."""
    if not qids:
        return {}
    d = _wd({"action": "wbgetentities", "props": "claims", "ids": "|".join(qids[:50])})
    out = {}
    for qid, ent in (d.get("entities") or {}).items():
        for prop in ("P18", "P154", "P41", "P94"):     # image, logo, flag, coat of arms
            try:
                out[qid] = (ent["claims"][prop][0]["mainsnak"]["datavalue"]["value"], prop)
                break
            except Exception:
                pass
    return out


def commons_thumb(filename: str, width: int = 320) -> str:
    """Special:FilePath does the thumbnailing server-side, so no second API call is
    needed to turn a Commons filename into a usable image URL."""
    return ("https://commons.wikimedia.org/wiki/Special:FilePath/"
            + urllib.parse.quote(filename.replace(" ", "_")) + f"?width={width}")
