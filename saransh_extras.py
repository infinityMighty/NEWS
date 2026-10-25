"""Saransh helpers: (1) never cut a description mid-sentence, (2) refresh context.json daily."""
import json, os, re

_END = re.compile(r'[.!?।]["”’)\]]*(?=\s|$)')
_ABBR = {"mr", "mrs", "ms", "dr", "st", "no", "vs", "jr", "sr", "inc", "ltd", "co", "prof", "gen", "rs"}
_TAIL = re.compile(r'\s*(\[?(…|\.\.\.)\]?|continue reading.*|read more.*|the post .* appeared first.*)$', re.I)


def _is_real_end(text, m):
    """True if the punctuation at match m ends a sentence (not 'U.S.', 'Dr.', etc.)."""
    word = text[:m.start() + 1].split()[-1] if text[:m.start() + 1].split() else ""
    if re.fullmatch(r'(?:[A-Za-z]\.){2,}', word) or re.fullmatch(r'[A-Z]\.', word):
        return False
    return word.lower().rstrip('.') not in _ABBR


def trim_to_sentence(text, limit=300, min_len=40, reach=150):
    """Return text ending on a complete sentence, at most ~limit chars (up to limit+reach if needed)."""
    text = _TAIL.sub("", re.sub(r"\s+", " ", text or "")).strip()
    if not text:
        return text
    ends = [m.end() for m in _END.finditer(text) if _is_real_end(text, m)]
    inside = [e for e in ends if e <= limit and e >= min_len]
    if len(text) <= limit:
        if ends and ends[-1] >= len(text) - 1:
            return text                      # already ends cleanly
        return text[:inside[-1]].strip() if inside else text.rstrip(",;:- ") + "."
    if inside:
        return text[:inside[-1]].strip()     # last full sentence inside the limit
    ahead = [e for e in ends if limit < e <= limit + reach]
    if ahead:
        return text[:ahead[0]].strip()       # first sentence end just past the limit
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"


def refresh_context(cards, path="context.json", n_new=5, keep=40):
    """Add n_new fresh explainer cards tied to today's top stories; keep the newest `keep`."""
    try:
        existing = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    except Exception as ex:
        print("context.json unreadable, starting fresh:", ex)
        existing = []
    if not os.getenv("ANTHROPIC_API_KEY"):
        return existing
    try:
        import anthropic
        cats = sorted({c["category"] for c in cards})
        tops = [f'[{c["category"]}] {c["headline"]}' for c in cards if c.get("top")][:12]
        have = [x.get("h", "") for x in existing][:keep]
        prompt = (
            f"You are an explainer editor for a news app. Today's top stories:\n" + "\n".join(tops) +
            f"\n\nWrite {n_new} NEW learning cards that give lasting background for understanding these stories "
            "(how something works, why it matters, a short history). Not news - evergreen explainers.\n"
            "Each card is a JSON object with keys:\n"
            ' "c": "Background" or "Did you know",\n'
            f' "cat": one of {cats},\n'
            ' "h": title, max 70 chars,\n'
            ' "s": 1-2 complete sentences, max 45 words,\n'
            ' "tl": exactly 3 [label, text] pairs in time/step order (label: a year, "Step 1" or "Today"; text max 10 words),\n'
            ' "w": one sentence on how this helps follow the news.\n'
            "Use only well-established facts. Avoid exact figures or dates unless you are certain. "
            "Do not repeat these existing titles: " + json.dumps(have, ensure_ascii=False) +
            "\nReturn ONLY a JSON array."
        )
        client = anthropic.Anthropic()
        msg = client.messages.create(model=os.getenv("MODEL", "claude-haiku-5-5"), max_tokens=3000,
                                     messages=[{"role": "user", "content": prompt}])
        new = json.loads(re.search(r"\[.*\]", msg.content[0].text, re.S).group(0))
        seen = {h.lower() for h in have}
        fresh = []
        for d in new:
            tl = d.get("tl")
            ok = (isinstance(d, dict) and all(d.get(k) for k in ("c", "cat", "h", "s", "w"))
                  and isinstance(tl, list) and len(tl) == 3 and all(isinstance(p, list) and len(p) == 2 for p in tl))
            if not ok or d["h"].lower() in seen or d["cat"] not in cats:
                continue
            d["s"] = trim_to_sentence(d["s"], 260)
            seen.add(d["h"].lower())
            fresh.append({k: d[k] for k in ("c", "cat", "h", "s", "tl", "w")})
        merged = (fresh + existing)[:keep]
        json.dump(merged, open(path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print(f"Context: added {len(fresh)} new cards, {len(merged)} total")
        return merged
    except Exception as ex:
        print("Context refresh failed, keeping existing cards:", ex)
        return existing
