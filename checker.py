"""
Originality checker engine.

Mimics the Google Classroom originality report:
  1. Pull the text out of a .docx / .pdf / .txt
  2. Drop the References section, split into sentences
  3. Pick distinctive phrases and search for them (web + academic + your own files)
  4. Download the candidate sources and compare word-by-word (5-word overlaps)
  5. Flag sentences that match, and mark the ones that are quoted + cited
"""

import io
import os
import re
import time
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) OriginalityChecker/1.0"}

STOP = set("""
a an the and or but if of to in on for with as by at from is are was were be been being this that these those
it its into than then there their they them we our you your he she his her i not no can will would should could
may might also such which who whom whose what when where how all any each other some more most many much very
do does did done has have had about over under between through during after before so because while within
""".split())

SHINGLE = 5            # words per overlap chunk (Classroom-style exact phrase matching)
FLAG_THRESHOLD = 0.40  # sentence is flagged if >= 40% of its 5-word chunks appear in one source
MIN_WORDS = 8          # sentences shorter than this are never checked (headings, labels)


# ----------------------------------------------------------------- text extraction

def extract_text(path=None, data=None, filename=None):
    """Return the document text with paragraphs separated by newlines."""
    name = (filename or path or "").lower()
    ext = os.path.splitext(name)[1]
    if data is None:
        with open(path, "rb") as f:
            data = f.read()

    if ext == ".docx":
        import docx
        d = docx.Document(io.BytesIO(data))
        body = d.element.body
        out = []
        # walk the body in order so tables stay where they are in the paper
        for child in body.iterchildren():
            tag = child.tag.split("}")[-1]
            if tag == "p":
                out.append("".join(t.text or "" for t in child.iter() if t.tag.endswith("}t")))
            elif tag == "tbl":
                for row in child.iter():
                    if row.tag.endswith("}tc"):
                        out.append("".join(t.text or "" for t in row.iter() if t.tag.endswith("}t")))
        return "\n".join(out)

    if ext == ".pdf":
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(data))
        pages = [(pg.extract_text() or "") for pg in r.pages]
        text = "\n".join(pages)
        # PDFs break lines mid-sentence: join lines that don't end a sentence
        text = re.sub(r"-\n(?=[a-z])", "", text)
        text = re.sub(r"(?<![.!?:;])\n(?=[a-z(])", " ", text)
        return text

    return data.decode("utf-8", errors="ignore")


# ----------------------------------------------------------------- splitting

REF_HEAD = re.compile(r"^\s*(references|reference list|bibliography|literature cited|works cited)\s*$", re.I)
CITE_RE = re.compile(
    r"\(([A-Z][A-Za-z'\-]+[^()]{0,80}?(\d{4}|n\.d\.)[a-z]?)\)"   # (Santos, 2019) / (Cruz et al., 2020, p. 4)
    r"|[A-Z][A-Za-z'\-]+( et al\.)?( and [A-Z][A-Za-z'\-]+)? \(\d{4}[a-z]?\)"  # Santos (2019)
    r"|\[\d+(?:[,–\-]\s*\d+)*\]"                              # [3] / [3, 5]
)
QUOTE_RE = re.compile(r"[\"“][^\"”]{12,}[\"”]")
SENT_SPLIT = re.compile(r"(?<=[.!?])[\"”)]?\s+(?=[A-Z0-9\"“(])")
ABBREV = re.compile(r"\b(et al|e\.g|i\.e|vs|Dr|Mr|Mrs|Ms|Fig|No|pp?|Engr|Arch|St|Sec|Art)\.$")


def words(s):
    return re.findall(r"[a-z0-9]+", s.lower())


@dataclass
class Sentence:
    idx: int
    para: int
    text: str
    tokens: list
    quoted: bool = False
    cited: bool = False
    checkable: bool = True
    source: int = -1          # index into sources, -1 = no match
    score: float = 0.0


def split_document(text):
    """Return (sentences, excluded_reference_text)."""
    paras = [p.strip() for p in text.split("\n")]
    # cut the References section (only if the heading is in the back half of the paper)
    excluded = ""
    for i, p in enumerate(paras):
        if REF_HEAD.match(p) and i > len(paras) * 0.4:
            excluded = "\n".join(paras[i:])
            paras = paras[:i]
            break

    sents = []
    pnum = 0
    for p in paras:
        if not p:
            continue
        pieces, buf = [], ""
        for piece in SENT_SPLIT.split(p):
            buf = (buf + " " + piece).strip() if buf else piece
            if ABBREV.search(buf.rstrip()):
                continue        # "et al." etc. — keep joining
            pieces.append(buf)
            buf = ""
        if buf:
            pieces.append(buf)
        for s in pieces:
            toks = words(s)
            sent = Sentence(len(sents), pnum, s, toks)
            sent.quoted = bool(QUOTE_RE.search(s))
            sent.cited = bool(CITE_RE.search(s))
            sent.checkable = len(toks) >= MIN_WORDS
            sents.append(sent)
        pnum += 1
    return sents, excluded


def shingles(tokens, n=SHINGLE):
    return {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


# ----------------------------------------------------------------- query selection

def best_phrase(sentence_text, length=9):
    """Most distinctive run of ~9 words in the sentence (fewest stopwords)."""
    raw = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", CITE_RE.sub("", sentence_text))
    if len(raw) <= length:
        return " ".join(raw)
    best, best_score = None, -1
    for i in range(len(raw) - length + 1):
        win = raw[i:i + length]
        score = sum(1 for w in win if w.lower() not in STOP and len(w) > 2)
        if score > best_score:
            best, best_score = win, score
    return " ".join(best)


def pick_queries(sents, budget):
    """Spread `budget` queries evenly across the paper, best sentence per stretch."""
    cands = [s for s in sents if s.checkable]
    if not cands or budget <= 0:
        return []
    if len(cands) <= budget:
        chosen = cands
    else:
        chosen = []
        step = len(cands) / budget
        for k in range(budget):
            chunk = cands[int(k * step):int((k + 1) * step)] or [cands[min(int(k * step), len(cands) - 1)]]
            chosen.append(max(chunk, key=lambda s: sum(1 for t in s.tokens if t not in STOP)))
    return [(s.idx, best_phrase(s.text)) for s in chosen]


# ----------------------------------------------------------------- sources

@dataclass
class Source:
    title: str
    url: str
    kind: str                 # web / academic / my files
    text: str = ""
    hits: int = 0
    words_matched: int = 0
    shingle_set: set = field(default_factory=set, repr=False)


FAILS = Counter()   # host -> failed requests, reported in the notes


def _get(url, params=None, headers=None, timeout=20, tries=3):
    h = dict(UA)
    h.update(headers or {})
    host = url.split("/")[2] if "//" in url else url
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers=h, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            if r.status_code >= 400:
                FAILS[host] += 1
            return r
        except requests.RequestException:
            if attempt == tries - 1:
                FAILS[host] += 1
            time.sleep(0.5)
    return None


def search_brave(query, key):
    r = _get("https://api.search.brave.com/res/v1/web/search",
             params={"q": query, "count": 8},
             headers={"Accept": "application/json", "X-Subscription-Token": key})
    if r is None or r.status_code != 200:
        code = None if r is None else r.status_code
        if code in (401, 403):
            raise RuntimeError("Brave rejected the API key (check it in Settings).")
        if code == 402:
            raise RuntimeError("Brave free credits used up for this month.")
        return []
    out = []
    for item in (r.json().get("web") or {}).get("results", []):
        out.append(Source(item.get("title", ""), item.get("url", ""), "web",
                          text=re.sub("<[^>]+>", "", item.get("description", ""))))
    return out


def _openalex_abstract(inv):
    if not inv:
        return ""
    pos = {}
    for w, idxs in inv.items():
        for i in idxs:
            pos[i] = w
    return " ".join(pos[i] for i in sorted(pos))


def search_openalex(query, key=None):
    params = {"search": query, "per-page": 5}
    if key:
        params["api_key"] = key
    r = _get("https://api.openalex.org/works", params=params)
    if r is None or r.status_code != 200:
        return []
    out = []
    for w in r.json().get("results", []):
        loc = w.get("best_oa_location") or {}
        url = loc.get("pdf_url") or loc.get("landing_page_url") or w.get("doi") or w.get("id", "")
        out.append(Source(w.get("display_name") or "Untitled paper", url, "academic",
                          text=_openalex_abstract(w.get("abstract_inverted_index"))))
    return out


def search_semantic_scholar(query):
    r = _get("https://api.semanticscholar.org/graph/v1/paper/search",
             params={"query": query, "limit": 5, "fields": "title,abstract,url,openAccessPdf"})
    if r is None or r.status_code != 200:
        return []
    out = []
    for p in r.json().get("data", []) or []:
        pdf = (p.get("openAccessPdf") or {}).get("url")
        out.append(Source(p.get("title") or "Untitled paper", pdf or p.get("url", ""), "academic",
                          text=p.get("abstract") or ""))
    return out


def fetch_text(url):
    """Download a page/PDF and return its readable text."""
    if not url or not url.startswith("http"):
        return ""
    r = _get(url, timeout=25, tries=2)
    if r is None or r.status_code != 200:
        return ""
    ctype = r.headers.get("Content-Type", "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            rd = PdfReader(io.BytesIO(r.content))
            return " ".join((pg.extract_text() or "") for pg in rd.pages[:60])
        except Exception:
            return ""
    if "html" in ctype or "text" in ctype or not ctype:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
            t.decompose()
        return soup.get_text(" ", strip=True)
    return ""


def load_library(folder):
    """Your own files: references, related studies, older drafts."""
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    for root, _, files in os.walk(folder):
        for fn in sorted(files):
            if os.path.splitext(fn)[1].lower() in (".docx", ".pdf", ".txt", ".md"):
                p = os.path.join(root, fn)
                try:
                    out.append(Source(fn, p, "my files", text=extract_text(path=p)))
                except Exception:
                    pass
    return out


# ----------------------------------------------------------------- main check

@dataclass
class Result:
    sentences: list
    sources: list
    total_words: int
    flagged_words: int
    cited_words: int
    excluded_refs: str
    notes: list


def run_check(doc_path=None, doc_bytes=None, filename=None, *,
              brave_key=None, use_academic=True, openalex_key=None,
              library_folder=None, library_files=None, web_budget=150, academic_budget=40,
              max_fetch=45, progress=None):
    """progress(fraction 0..1, message) is called as work happens."""
    say = progress or (lambda f, m: None)
    notes = []
    FAILS.clear()

    say(0.02, "Reading your document")
    text = extract_text(doc_path, doc_bytes, filename)
    sents, refs = split_document(text)
    if refs:
        notes.append("References section was excluded from the check (Classroom does the same).")
    checkable = [s for s in sents if s.checkable]
    if not checkable:
        raise ValueError("Couldn't find any text to check. If it's a scanned PDF, export it from Word instead.")

    pool = {}          # url -> Source
    sources_hit = Counter()

    def add(src):
        key = src.url or src.title
        if key not in pool:
            pool[key] = src
        elif len(src.text) > len(pool[key].text):
            pool[key].text = src.text
        pool[key].hits += 1
        sources_hit[key] += 1

    # 1) your own files
    lib = load_library(library_folder)
    for name, data in (library_files or []):      # uploaded reference files (web/phone version)
        try:
            lib.append(Source(name, name, "my files", text=extract_text(data=data, filename=name)))
        except Exception:
            notes.append(f"Couldn't read the reference file {name}.")
    for s in lib:
        add(s)
    if library_folder and not lib:
        notes.append("Your files folder was empty or not found, so it was skipped.")

    # 2) academic databases run in the background, at the same time as web search
    from concurrent.futures import ThreadPoolExecutor, as_completed
    pool_exec = ThreadPoolExecutor(max_workers=8)
    acad_futs = []
    if use_academic:
        aq = [q for _, q in pick_queries(sents, academic_budget)]
        acad_futs += [pool_exec.submit(search_openalex, q, openalex_key) for q in aq]

        def s2_all(qs):            # Semantic Scholar's free limit is ~1 request/second
            out = []
            for q in qs:
                out += search_semantic_scholar(q)
                time.sleep(1.0)
            return out
        acad_futs.append(pool_exec.submit(s2_all, aq[::2]))

    # 3) web search: Brave's Search plan allows 50 requests/second, so run 10 at a time
    if brave_key:
        queries = pick_queries(sents, web_budget)
        web_exec = ThreadPoolExecutor(max_workers=10)
        wfuts = [web_exec.submit(search_brave, q, brave_key) for _, q in queries]
        stopped = None
        for i, f in enumerate(as_completed(wfuts)):
            say(0.05 + 0.50 * (i + 1) / max(1, len(wfuts)), f"Searching the web ({i + 1}/{len(wfuts)})")
            if f.cancelled():
                continue
            try:
                for src in f.result():
                    add(src)
            except Exception as e:
                if not stopped:
                    stopped = str(e)
                    for other in wfuts:
                        other.cancel()
        web_exec.shutdown(wait=False)
        if stopped:
            notes.append(f"Web search stopped early: {stopped}")
    else:
        notes.append("Web search was off (no Brave key added), so websites were not checked.")

    done = 0
    for f in as_completed(acad_futs):
        done += 1
        say(0.55 + 0.05 * done / max(1, len(acad_futs)), "Searching academic papers")
        for src in f.result():
            if src.text or src.url:
                add(src)

    # 4) download the most promising sources (found by the most searches), 10 at a time
    fetchable = [k for k, _ in sources_hit.most_common() if pool[k].kind != "my files"
                 and pool[k].url.startswith("http")]
    fetchable = fetchable[:max_fetch]
    fetch_exec = ThreadPoolExecutor(max_workers=10)
    futs = {fetch_exec.submit(fetch_text, pool[k].url): k for k in fetchable}
    for i, f in enumerate(as_completed(futs)):
        say(0.60 + 0.30 * (i + 1) / max(1, len(futs)), f"Reading matched sources ({i + 1}/{len(futs)})")
        k = futs[f]
        full = f.result()
        if len(full) > len(pool[k].text):
            pool[k].text = full
    fetch_exec.shutdown(wait=False)
    pool_exec.shutdown(wait=False)

    search_hosts = {"api.search.brave.com": "Brave web search", "api.openalex.org": "OpenAlex",
                    "api.semanticscholar.org": "Semantic Scholar"}
    for host, label in search_hosts.items():
        if FAILS.get(host):
            notes.append(f"{label} failed {FAILS[host]} time(s), so some passages may not have been "
                         "checked against it. Check your internet connection and run again.")
    if not use_academic:
        notes.append("Academic paper search was off.")

    # 5) compare
    say(0.92, "Comparing sentences")
    sources = [s for s in pool.values() if s.text]
    for s in sources:
        s.shingle_set = shingles(words(s.text))

    # index shingle -> sources for speed
    index = defaultdict(list)
    for si, s in enumerate(sources):
        for sh in s.shingle_set:
            index[sh].append(si)

    for sent in checkable:
        sh = shingles(sent.tokens)
        if not sh:
            continue
        counts = Counter()
        for x in sh:
            for si in index.get(x, ()):
                counts[si] += 1
        if not counts:
            continue
        si, c = counts.most_common(1)[0]
        frac = c / len(sh)
        if frac >= FLAG_THRESHOLD and c >= 2:
            sent.source, sent.score = si, frac

    # keep only sources that actually matched, ordered by how much they matched
    used = Counter()
    for s in sents:
        if s.source >= 0:
            used[s.source] += len(s.tokens)
    order = [si for si, _ in used.most_common()]
    remap = {old: new for new, old in enumerate(order)}
    final_sources = []
    for old in order:
        src = sources[old]
        src.words_matched = used[old]
        src.shingle_set = set()
        src.text = ""
        final_sources.append(src)
    for s in sents:
        s.source = remap.get(s.source, -1)

    total = sum(len(s.tokens) for s in sents)
    cited = sum(len(s.tokens) for s in sents if s.source >= 0 and s.cited)
    flagged = sum(len(s.tokens) for s in sents if s.source >= 0 and not s.cited)
    say(1.0, "Done")
    return Result(sents, final_sources, total, flagged, cited, refs, notes)
