#!/usr/bin/env python3
"""
Manual lookup for the chat page, degrading gracefully by what's on disk.

Buddy reads the PORTABLE BUNDLE FORMAT directly (PDF -> chunks -> vectors,
written by bundle_builder.py), so answering needs nothing but the bundle
files on disk.

Four tiers, best available wins. describe_tier() explains the current one
to the user, because "why are my answers worse today" should be answerable
from the UI:

  hybrid       bundle + an      - vector similarity fused with BM25. Finds
               embedder           paraphrases ("get rid of the hum" ->
                                  noise reduction). The embedder is the one
                                  Settings chose (embedder.py): Buddy's own
                                  model, Ollama or another server.
  bundle-bm25  bundle, no       - BM25 over chunks.jsonl. Keyword-only, but
               embedder           chunks are cleaned and pre-cited, so it
                                  still beats raw page text.
  text-bm25    manual.txt only  - BM25 over whole pages, citations parsed
                                  out of the running footer at query time.
  none         nothing built    - the page tells the user how to fix it.

On every tier the best twenty candidates are looked at again (_rerank: how
much of the question each covers), and on the bundle tiers the chosen chunks
are handed over with their context (_with_context: neighbouring hits as one
passage, the best ones with the chunk that follows). page_passages reads
whole pages, for the agent's read_manual_page.

Vector decode mirrors bundle_builder.py's writer exactly: b"RMS1" | int32 N |
int32 dim | N float32 scales | N*dim int8 components.
"""

from __future__ import annotations

import json
import math
import re
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# --- bundle format (bundle_builder.py writes it with these) -----------------

VECTOR_MAGIC = b"RMS1"
QUERY_PREFIX = "task: search result | query: "
DOC_PREFIX = "title: {title} | text: "
EMBED_MODEL = "embeddinggemma"
RRF_K = 60
# How alike an embedder's vector for a stored chunk must be to the stored one
# for its queries to be trusted against the bundle. The same model gives
# 0.9996 or better (int8 storage, Q8_0 weights); a different one well under 0.9.
MATCH = 0.98

BUNDLE_CHUNKS = "chunks.jsonl"
BUNDLE_VECTORS = "vectors-int8-512.bin"

# 127.0.0.1, never "localhost": on Windows the name resolves to ::1 first,
# and the failed IPv6 connect costs a flat ~2.0s before falling back to IPv4.
# Measured: 2.09s per embed via localhost vs 0.046s here.
OLLAMA_HOST = "http://127.0.0.1:11434"

TIER_HYBRID = "hybrid"
TIER_BUNDLE_BM25 = "bundle-bm25"
TIER_TEXT_BM25 = "text-bm25"
TIER_NONE = "none"

TIER_BLURB = {
    TIER_HYBRID: "Semantic + keyword search over the built bundle.",
    TIER_BUNDLE_BM25: (
        "Keyword search over the built bundle. Set up semantic search in "
        "Settings > AI > Manual search for answers that find what you mean."
    ),
    TIER_TEXT_BM25: (
        "Keyword search over the plain text extract. Build the bundle "
        "for chunk-level citations and semantic search."
    ),
    TIER_NONE: "No manual data found – answers will not be grounded.",
}

FOOTER_MARK = "|Chapter"
FOOTER_RE = re.compile(
    r"^(?P<section>[A-Za-z][\w &/,.'’-]*?)\s*\|\s*"
    r"Chapter\s*(?P<num>\d+)\s*(?P<title>.+?)\s*$"
)
TOKEN_RE = re.compile(r"[a-z0-9]+")

# Re-ranking (ManualRetriever._rerank): how many fused candidates are looked
# at again, and how much covering the question's words, its word pairs and
# having them in the heading each lift one.
RERANK_POOL = 20
RERANK_COVERAGE = 0.35
RERANK_PHRASE = 0.25
RERANK_HEADING = 0.15
CONTINUED = 2           # this many of the best hits bring the chunk that follows them
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it my of on or the to what when "
    "where which why with you your".split())


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


@dataclass
class Passage:
    """One retrieved excerpt, always carrying enough to cite it."""

    text: str
    page: int | None = None
    chapter_no: int | None = None
    chapter_title: str = ""
    section: str = ""
    score: float = 0.0
    index: int = -1         # its chunk's place in the bundle; -1 when it isn't one chunk

    def citation(self) -> str:
        bits = []
        if self.chapter_no is not None:
            bits.append(f"Chapter {self.chapter_no}")
        if self.chapter_title:
            bits.append(self.chapter_title)
        if self.page is not None:
            bits.append(f"p.{self.page}")
        return " – ".join(bits) if bits else "DaVinci Resolve manual"


class BM25:
    """Okapi BM25 with the standard parameters (k1 1.5, b 0.75)."""

    def __init__(self, docs_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n = len(docs_tokens)
        self.lengths = [len(d) for d in docs_tokens]
        self.avg_len = (sum(self.lengths) / self.n) if self.n else 0.0
        # Counted with Counter (in C): a loop of dict.get here was most of
        # the manual's load time, on the UI thread, at every launch.
        self.freqs: list[dict[str, int]] = [Counter(tokens) for tokens in docs_tokens]
        df: Counter[str] = Counter()
        for tf in self.freqs:
            df.update(tf.keys())
        self.idf = {
            t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()
        }

    def search(self, query: str, limit: int = 50) -> list[tuple[int, float]]:
        terms = [t for t in tokenize(query) if t in self.idf]
        if not terms:
            return []
        scores: list[tuple[int, float]] = []
        for i, tf in enumerate(self.freqs):
            s = 0.0
            for t in terms:
                f = tf.get(t)
                if not f:
                    continue
                denom = f + self.k1 * (
                    1 - self.b + self.b * self.lengths[i] / (self.avg_len or 1)
                )
                s += self.idf[t] * f * (self.k1 + 1) / denom
            if s > 0:
                scores.append((i, s))
        scores.sort(key=lambda x: -x[1])
        return scores[:limit]


class ManualRetriever:
    """Picks the best tier available at construction time.

    Construct once and reuse - loading the bundle parses ~10 MB and builds a
    BM25 index, which is fine once at page load but not per keystroke.
    """

    def __init__(self, bundle_dir: str | Path | None, text_path: str | Path | None, embedder=None):
        self.bundle_dir = Path(bundle_dir) if bundle_dir else None
        self.text_path = Path(text_path) if text_path else None
        self.embedder = embedder
        self.tier = TIER_NONE
        self.why = ""               # why it isn't semantic, for describe_tier
        self.chunks: list[dict] = []
        self.dim = 0
        self._vectors: bytes = b""
        self._scales: list[float] = []
        self._bm25: BM25 | None = None
        self._verified = False

        if self._load_bundle():
            self.tier = TIER_BUNDLE_BM25
            if self.dim <= 0:
                self.why = "The bundle was built without vectors – rebuild it from the PDF for semantic search."
            elif embedder is None:
                self.why = "Semantic search is off in Settings."
            else:
                ok, self.why = embedder.ready()
                if ok:
                    self.tier = TIER_HYBRID
        elif self._load_text():
            self.tier = TIER_TEXT_BM25

    # ---------------------------------------------------------------- load

    def _load_bundle(self) -> bool:
        if not self.bundle_dir:
            return False
        chunks_path = self.bundle_dir / BUNDLE_CHUNKS
        vec_path = self.bundle_dir / BUNDLE_VECTORS
        if not chunks_path.exists():
            return False
        self.chunks = [
            json.loads(line)
            for line in chunks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not self.chunks:
            return False
        self._bm25 = BM25([tokenize(c.get("text", "")) for c in self.chunks])

        if vec_path.exists():
            raw = vec_path.read_bytes()
            if raw[:4] == VECTOR_MAGIC:
                n, dim = struct.unpack_from("<ii", raw, 4)
                if n == len(self.chunks):
                    off = 12
                    self._scales = list(struct.unpack_from(f"<{n}f", raw, off))
                    off += 4 * n
                    self._vectors = raw[off : off + n * dim]
                    self.dim = dim
        return True

    def _load_text(self) -> bool:
        if not self.text_path or not self.text_path.exists():
            return False
        pages = self.text_path.read_text(
            encoding="utf-8", errors="replace"
        ).split("\f")
        for i, page in enumerate(pages):
            if not page.strip():
                continue
            meta = self._parse_footer(page)
            self.chunks.append({"text": page.strip(), "page": i + 1, **meta})
        if not self.chunks:
            return False
        self._bm25 = BM25([tokenize(c["text"]) for c in self.chunks])
        return True

    @staticmethod
    def _parse_footer(page_text: str) -> dict:
        """Recover chapter metadata from the running footer pdftotext leaves
        in the page body, e.g. 'Setup and Workflows|Chapter 8Improving...'."""
        for line in reversed(page_text.split("\n")):
            if FOOTER_MARK not in line:
                continue
            m = FOOTER_RE.match(line.strip())
            if m:
                return {
                    "section": m.group("section"),
                    "chapter_no": int(m.group("num")),
                    "chapter_title": m.group("title").strip(),
                }
        return {}

    # -------------------------------------------------------------- search

    def stored_vector(self, index: int) -> list[float]:
        """A chunk's vector as the bundle stores it (unit length, 512 dims)."""
        row = self._vectors[index * self.dim:(index + 1) * self.dim]
        return [(b - 256 if b > 127 else b) * self._scales[index] for b in row]

    def _query_vector(self, query: str) -> list[float] | None:
        """The query's vector - and, the first time, the embedder's own
        vector for a stored chunk, which must match the bundle's (MATCH)
        before any query of its is trusted. None means keywords only now;
        self.why says why."""
        try:
            if self._verified:
                return self.embedder.embed([QUERY_PREFIX + query])[0]
            first = self.chunks[0]
            probe = DOC_PREFIX.format(title=first.get("chapter_title") or "none") + first.get("text", "")
            mine, qvec = self.embedder.embed([probe, QUERY_PREFIX + query])
        except Exception as exc:  # noqa: BLE001 - EmbedError, or anything a server threw
            self.tier, self.why = TIER_BUNDLE_BM25, f"{self.embedder.label} stopped answering: {exc}"
            return None
        score = cosine(mine[: self.dim], self.stored_vector(0))
        if score < MATCH:
            self.tier = TIER_BUNDLE_BM25
            self.why = mismatch(self.embedder.label, score)
            return None
        self._verified = True
        return qvec

    def _vector_scores(self, qvec: list[float]) -> list[tuple[int, float]]:
        """Cosine against dequantized int8 vectors, via MRL-truncated query."""
        q = qvec[: self.dim]
        norm = math.sqrt(sum(x * x for x in q)) or 1.0
        q = [x / norm for x in q]
        out = []
        for i, scale in enumerate(self._scales):
            base = i * self.dim
            row = self._vectors[base : base + self.dim]
            dot = 0.0
            for j, byte in enumerate(row):
                dot += (byte - 256 if byte > 127 else byte) * q[j]
            out.append((i, dot * scale))
        out.sort(key=lambda x: -x[1])
        return out[:50]

    @staticmethod
    def _rrf(rankings: list[list[int]], limit: int) -> list[tuple[int, float]]:
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, idx in enumerate(ranking):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
        return sorted(fused.items(), key=lambda x: -x[1])[:limit]

    def _passage(self, idx: int, score: float = 0.0) -> Passage:
        c = self.chunks[idx]
        return Passage(
            text=c.get("text", ""),
            page=c.get("page"),
            chapter_no=c.get("chapter_no"),
            chapter_title=c.get("chapter_title", ""),
            section=c.get("section", ""),
            score=score,
            index=idx,
        )

    def _rerank(self, query: str, fused: list[tuple[int, float]]) -> list[tuple[int, float]]:
        """The fused candidates, re-ordered by how much of the question each
        one actually covers. Fusion ranks a chunk that says one of the
        question's words many times beside one that says all of them once;
        this lifts the second. A boost on the fused score, never a
        replacement for it - a paraphrase the embedder found, sharing no
        words with the question, keeps the place fusion gave it among the
        others like it."""
        words = [t for t in tokenize(query) if t not in STOPWORDS]
        terms = set(words)
        if not terms or not self._bm25:
            return fused
        pairs = {f"{a} {b}" for a, b in zip(words, words[1:])}
        out = []
        for idx, score in fused:
            chunk = self.chunks[idx]
            have = self._bm25.freqs[idx]
            coverage = sum(1 for t in terms if t in have) / len(terms)
            phrase = 0.0
            if pairs and coverage:
                flat = " ".join(tokenize(chunk.get("text", "")))
                phrase = sum(1 for p in pairs if p in flat) / len(pairs)
            heading = set(tokenize(f"{chunk.get('section', '')} {chunk.get('chapter_title', '')}"))
            titled = sum(1 for t in terms if t in heading) / len(terms)
            out.append((idx, score * (1 + RERANK_COVERAGE * coverage + RERANK_PHRASE * phrase
                                      + RERANK_HEADING * titled)))
        out.sort(key=lambda x: -x[1])
        return out

    def _with_context(self, picked: list[tuple[int, float]], spare=()) -> list[Passage]:
        """Passages for the chosen chunks, each a readable stretch: chunks
        that follow one another in the manual become one passage, and the
        best CONTINUED hits bring the chunk after them - a chunk is under a
        thousand characters, and a procedure that starts in one usually
        finishes in the next."""
        chosen = {idx for idx, _ in picked}
        used: set[int] = set()
        results = []
        for rank, (idx, score) in enumerate(picked):
            if idx in used:
                continue
            run = [idx]
            while run[0] - 1 in chosen and run[0] - 1 not in used and self._same_chapter(run[0] - 1, idx):
                run.insert(0, run[0] - 1)
            while run[-1] + 1 in chosen and run[-1] + 1 not in used and self._same_chapter(run[-1] + 1, idx):
                run.append(run[-1] + 1)
            nxt = run[-1] + 1
            if (rank < CONTINUED and nxt < len(self.chunks) and nxt not in used
                    and self._same_chapter(nxt, idx)):
                run.append(nxt)
            used.update(run)
            passage = self._passage(run[0], score)
            if len(run) > 1:
                passage.text = "\n\n".join(self.chunks[i].get("text", "") for i in run)
            results.append(passage)
        # Chunks that merged left places free: the next best fill them.
        for idx, score in spare:
            if len(results) >= len(picked):
                break
            if idx not in used:
                used.add(idx)
                results.append(self._passage(idx, score))
        return results

    def _same_chapter(self, a: int, b: int) -> bool:
        return self.chunks[a].get("chapter_no") == self.chunks[b].get("chapter_no")

    def search(self, query: str, limit: int = 5) -> list[Passage]:
        if not self._bm25 or not self.chunks:
            return []
        keyword = self._bm25.search(query, limit=50)
        rankings = [[i for i, _ in keyword]]

        if self.tier == TIER_HYBRID:
            # The embedder gone away mid-session, or not the bundle's model:
            # keep answering on keywords, the reason noted for the status line.
            qvec = self._query_vector(query)
            if qvec:
                rankings.append([i for i, _ in self._vector_scores(qvec)])

        pool = max(limit, RERANK_POOL)
        fused = self._rrf(rankings, pool) if len(rankings) > 1 else [
            (i, s) for i, s in keyword[:pool]
        ]
        ranked = self._rerank(query, fused)
        picked = ranked[:limit]
        if self.tier == TIER_TEXT_BM25:
            return [self._passage(idx, score) for idx, score in picked]    # whole pages already
        return self._with_context(picked, ranked[limit:])

    def page_passages(self, first: int, last: int | None = None) -> list[Passage]:
        """Everything the manual says from page `first` to `last`, in
        order - for reading on from an excerpt that stopped short."""
        last = first if last is None else max(first, last)
        return [self._passage(i) for i, c in enumerate(self.chunks)
                if isinstance(c.get("page"), int) and first <= c["page"] <= last]

    @property
    def last_page(self) -> int:
        return max((c["page"] for c in self.chunks if isinstance(c.get("page"), int)), default=0)

    def describe_tier(self) -> str:
        if self.tier == TIER_HYBRID and self.embedder is not None:
            return f"Semantic + keyword search over the built bundle, with {self.embedder.label}."
        blurb = TIER_BLURB.get(self.tier, "")
        return f"{blurb} {self.why}" if self.tier == TIER_BUNDLE_BM25 and self.why else blurb


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def mismatch(label: str, score: float) -> str:
    return (f"{label} isn't the model the manual's index was built with (similarity {score:.2f}). "
            "Rebuild the manual with it, or use EmbeddingGemma.")


def check_embedder(bundle_dir: str | Path, embedder) -> tuple[bool, str]:
    """Settings' Test: does this embedder answer, and with the vectors the
    bundle was built with? Reads only the first chunk and its vector, not
    the whole bundle."""
    bundle = Path(bundle_dir)
    try:
        with open(bundle / BUNDLE_CHUNKS, encoding="utf-8") as f:
            first = json.loads(f.readline())
        with open(bundle / BUNDLE_VECTORS, "rb") as f:
            head = f.read(12)
            if head[:4] != VECTOR_MAGIC:
                raise ValueError("no vectors")
            n, dim = struct.unpack_from("<ii", head, 4)
            scale = struct.unpack("<f", f.read(4))[0]
            f.seek(12 + 4 * n)
            row = f.read(dim)
    except (OSError, ValueError, struct.error):
        return False, "There's no manual bundle with vectors to test against – rebuild it from the PDF."
    stored = [(b - 256 if b > 127 else b) * scale for b in row]
    ok, why = embedder.ready()
    if not ok:
        return False, why
    text = DOC_PREFIX.format(title=first.get("chapter_title") or "none") + first.get("text", "")
    try:
        mine = embedder.embed([text], timeout=120)[0]
    except Exception as exc:  # noqa: BLE001 - shown to the person
        return False, f"{embedder.label} didn't answer: {exc}"
    score = cosine(mine[:dim], stored)
    if score < MATCH:
        return False, mismatch(embedder.label, score)
    return True, f"Works – {embedder.label} matches the manual's index (similarity {score:.4f})."
