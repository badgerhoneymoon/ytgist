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
import json
import re
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
                "piprop": "thumbnail", "pithumbsize": "320",
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
