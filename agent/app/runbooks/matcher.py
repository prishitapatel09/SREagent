"""Runbook matching: BM25 over the runbooks directory, frontmatter-aware.

BM25 over a ~6-document corpus is deliberate: zero model downloads, and the
scores are explainable — the tool returns the matched terms, so the agent
(and an interviewer) can see *why* a runbook matched. Title and keyword
tokens are weighted 3x; an exact alertname match adds a flat bonus.
"""

import re
from pathlib import Path

import yaml
from rank_bm25 import BM25Okapi

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_ALERTNAME_BONUS = 3.0


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


class RunbookMatcher:
    def __init__(self, runbooks_dir: str):
        self._entries: list[dict] = []
        directory = Path(runbooks_dir)
        if directory.is_dir():
            for path in sorted(directory.glob("*.md")):
                self._entries.append(self._parse(path))
        corpus = [entry["tokens"] for entry in self._entries]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    @staticmethod
    def _parse(path: Path) -> dict:
        raw = path.read_text()
        meta: dict = {}
        body = raw
        match = _FRONTMATTER_RE.match(raw)
        if match:
            meta = yaml.safe_load(match.group(1)) or {}
            body = raw[match.end():]
        title = str(meta.get("title", path.stem))
        keywords = str(meta.get("keywords") or "")
        alertnames = str(meta.get("alertnames") or "").split()
        tokens = _tokenize(body) + 3 * (_tokenize(title) + _tokenize(keywords))
        return {
            "slug": str(meta.get("slug", path.stem)),
            "title": title,
            "alertnames": alertnames,
            "path": str(path),
            "body": body.strip(),
            "tokens": tokens,
        }

    def search(self, query: str, top: int = 3) -> list[dict]:
        if not self._bm25:
            return []
        query_tokens = _tokenize(query)
        scores = self._bm25.get_scores(query_tokens)
        ranked = []
        for entry, score in zip(self._entries, scores):
            bonus = _ALERTNAME_BONUS if any(
                alert.lower() in query_tokens for alert in entry["alertnames"]
            ) else 0.0
            token_set = set(entry["tokens"])
            matched = sorted({t for t in query_tokens if t in token_set})
            ranked.append({
                "slug": entry["slug"],
                "title": entry["title"],
                "score": round(float(score) + bonus, 2),
                "matched_terms": matched,
            })
        ranked.sort(key=lambda r: r["score"], reverse=True)
        return ranked[:top]

    def get(self, slug: str) -> str | None:
        for entry in self._entries:
            if entry["slug"] == slug:
                return entry["body"]
        return None

    def slugs(self) -> list[str]:
        return [entry["slug"] for entry in self._entries]
