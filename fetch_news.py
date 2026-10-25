"""Saransh: 25 categories x 15 cards: RSS -> dedupe -> rank -> LLM summaries -> docs/news.json"""
import feedparser, json, os, re, html, time, socket, datetime as dt
from calendar import timegm
from urllib.parse import urlparse
from saransh_extras import trim_to_sentence, refresh_context

socket.setdefaulttimeout(10)
feedparser.USER_AGENT = "Mozilla/5.0 (compatible; SaranshBot/1.0)"
G = lambda p: f"https://www.theguardian.com/{p}/rss"
H = lambda p: f"https://www.thehindu.com/{p}/feeder/default.rss"
B = lambda p: f"https://feeds.bbci.co.uk/{p}/rss.xml"
N = lambda i: f"https://feeds.npr.org/{i}/rss.xml"

# category -> feeds (several per category so 5 cards are reachable; dead feeds are skipped & reported)
CATS = {
 "World": [B("news/world"), G("world"), "https://www.aljazeera.com/xml/rss/all.xml"],
 "India": [H("news/national"), "https://indianexpress.com/section/india/feed/", B("news/world/asia/india")],
 "Politics": [G("politics"), B("news/politics"), N(1014)],
 "Governance": ["https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", "https://indianexpress.com/section/political-pulse/feed/", G("global-development")],
 "Finance": [H("business/markets"), H("business/Economy"), G("business/economics")],
 "Business": [B("news/business"), H("business"), G("business")],
 "Education": [G("education"), H("education"), B("news/education")],
 "Environment": [G("environment"), "https://news.mongabay.com/feed/", "https://www.downtoearth.org.in/rss/all"],
 "Climate & Energy": ["https://www.carbonbrief.org/feed", G("environment/climate-crisis"), G("environment/energy")],
 "Science": [B("news/science_and_environment"), "https://www.sciencedaily.com/rss/top.xml", "https://www.nature.com/nature.rss"],
 "Space": ["https://www.nasa.gov/feed/", G("science/space"), "https://www.space.com/feeds/all"],
 "Technology": [B("news/technology"), G("technology"), H("sci-tech")],
 "AI": [G("technology/artificialintelligence"), "https://www.technologyreview.com/topic/artificial-intelligence/feed", "https://venturebeat.com/category/ai/feed/", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"],
 "Health": [B("news/health"), "https://www.who.int/rss-feeds/news-english.xml", H("sci-tech/health")],
 "Law & Justice": [G("law"), "https://www.livelaw.in/rss/", "https://www.barandbench.com/feed"],
 "Society": [G("society"), H("society"), B("news/uk")],
 "Literature": [G("books"), H("books"), N(1032)],
 "Arts & Culture": [G("culture"), G("artanddesign"), H("entertainment/art")],
 "Film & TV": [G("film"), G("tv-and-radio"), H("entertainment/movies")],
 "Music": [G("music"), H("entertainment/music"), N(1039)],
 "Sports": [B("sport"), G("sport"), H("sport")],
 "Food": [G("food"), H("life-and-style/food"), N(1053)],
 "Travel": [G("travel"), H("life-and-style/travel"), B("travel")],
 "Lifestyle": [G("lifeandstyle"), H("life-and-style")],
 "Opinion": [G("commentisfree"), H("opinion"), "https://indianexpress.com/section/opinion/feed/"],
}
NAMES = {"bbci.co.uk": "BBC", "theguardian.com": "The Guardian", "thehindu.com": "The Hindu", "aljazeera.com": "Al Jazeera",
 "indianexpress.com": "Indian Express", "npr.org": "NPR", "pib.gov.in": "PIB", "mongabay.com": "Mongabay",
 "downtoearth.org.in": "Down To Earth", "carbonbrief.org": "Carbon Brief", "sciencedaily.com": "ScienceDaily",
 "nature.com": "Nature", "nasa.gov": "NASA", "space.com": "Space.com", "technologyreview.com": "MIT Tech Review",
 "who.int": "WHO", "livelaw.in": "LiveLaw", "barandbench.com": "Bar & Bench", "venturebeat.com": "VentureBeat", "theverge.com": "The Verge"}
PER_CAT, TOP_N, TOP_PER_CAT, MAX_AGE_H, CHUNK = 15, 12, 2, 168, 15   # 15 cards/category, 7-day window, 15 stories per LLM call
MODEL = os.getenv("MODEL", "claude-haiku-5-5")
PROMPT = ("You are a neutral news editor. For each story write: headline (max 90 chars), "
 "summary (2-3 complete sentences, max 60 words, ONLY facts present in the input - add nothing; always end on a full stop). "
 "Return ONLY a JSON array of {id, headline, summary}.\nStories:\n")

clean = lambda t: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(t or ""))).strip()
words = lambda t: {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 3}
def source_of(url):
    h = (urlparse(url).hostname or "").lower()
    return next((n for d, n in NAMES.items() if h.endswith(d)), h)

def collect():
    out, now = [], time.time()
    for cat, urls in CATS.items():
        for url in urls:
            got = 0
            try:
                for e in feedparser.parse(url).entries[:30]:
                    t = e.get("published_parsed") or e.get("updated_parsed")
                    ts = timegm(t) if t else now
                    if now - ts > MAX_AGE_H * 3600 or not e.get("title"): continue
                    out.append(dict(category=cat, source=source_of(url), title=clean(e.get("title")),
                                    snippet=clean(e.get("summary"))[:700], url=e.get("link", ""), ts=ts)); got += 1
            except Exception as ex:
                print("  error", url, ex)
            if not got: print(f"  EMPTY/FAILED: [{cat}] {url}")
    return out

def cluster(items):  # same story from several outlets -> one group
    groups = []
    for it in sorted(items, key=lambda i: -i["ts"]):
        w = words(it["title"])
        for g in groups:
            if len(w & g["w"]) / max(1, len(w | g["w"])) >= 0.5:
                g["srcs"].add(it["source"]); break
        else:
            groups.append(dict(w=w, item=it, srcs={it["source"]}))
    now = time.time()
    for g in groups:
        g["score"] = len(g["srcs"]) * 2 + max(0, 1 - (now - g["item"]["ts"]) / (MAX_AGE_H * 3600))
    return groups

def select(groups):
    """PER_CAT best per category; TOP_N best overall (max TOP_PER_CAT per category) are flagged top."""
    order = list(CATS)
    by = {c: [] for c in order}
    for g in sorted(groups, key=lambda g: -g["score"]):
        c = g["item"]["category"]
        if len(by[c]) < PER_CAT: by[c].append(g)
    chosen = [g for c in order for g in by[c]]
    top, cnt = [], {}
    for g in sorted(chosen, key=lambda g: -g["score"]):
        c = g["item"]["category"]
        if cnt.get(c, 0) < TOP_PER_CAT and len(top) < TOP_N:
            cnt[c] = cnt.get(c, 0) + 1; top.append(g)
    ids = {id(g) for g in top}
    ordered = top + [g for g in chosen if id(g) not in ids]   # top first, then by category
    cards = []
    for g in ordered:
        it = g["item"]
        cards.append(dict(category=it["category"], headline=it["title"],
            summary=trim_to_sentence(it["snippet"], 260), raw=it["snippet"],   # raw is removed before saving
            source=it["source"] + (f" +{len(g['srcs'])-1}" if len(g["srcs"]) > 1 else ""), url=it["url"], top=id(g) in ids,
            published=dt.datetime.fromtimestamp(it["ts"], dt.timezone.utc).isoformat()))
    return cards

def summarise(cards):
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY - using raw snippets"); return
    import anthropic
    client = anthropic.Anthropic()
    for s in range(0, len(cards), CHUNK):
        part = cards[s:s + CHUNK]
        src = [dict(id=i, title=c["headline"], snippet=c["raw"][:600]) for i, c in enumerate(part)]
        for attempt in range(2):   # one retry if the model reply is cut off or not valid JSON
            try:
                msg = client.messages.create(model=MODEL, max_tokens=4000, messages=[{"role": "user", "content": PROMPT + json.dumps(src)}])
                for d in json.loads(re.search(r"\[.*\]", msg.content[0].text, re.S).group(0)):
                    if isinstance(d.get("id"), int) and 0 <= d["id"] < len(part):
                        for k in ("headline", "summary"):
                            if d.get(k): part[d["id"]][k] = d[k]
                break
            except Exception as ex:
                print(f"LLM chunk {s//CHUNK} attempt {attempt+1} failed:", ex)

if __name__ == "__main__":
    items = collect()
    if not items: raise SystemExit("No items fetched - keeping previous news.json")
    cards = select(cluster(items))
    summarise(cards)
    for c in cards:   # final safety pass: every description ends on a complete sentence
        c["summary"] = trim_to_sentence(c["summary"], 300)
        c.pop("raw", None)
    ctx = refresh_context(cards)   # adds ~5 fresh learning cards daily to context.json, keeps newest 40
    os.makedirs("docs", exist_ok=True)
    json.dump(dict(updated=dt.datetime.now(dt.timezone.utc).isoformat(), items=cards, context=ctx),
              open("docs/news.json", "w"), indent=1, ensure_ascii=False)
    print("Wrote", len(cards), "cards;", sum(c["top"] for c in cards), "in Top brief")
    for c in CATS: print(f"  {c}: {sum(x['category']==c for x in cards)}")
