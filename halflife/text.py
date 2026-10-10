"""Text utilities: normalization, tokenization, canonicalization and paraphrase.

The simulated consolidator rewrites memory with the lexicon below, and the
semantic detector undoes those rewrites with :func:`canonical_tokens`. Keeping
both directions in one table is deliberate: a detector that cannot see through
the rewrites the host performs measures the rewriter, not the instruction.
"""

from __future__ import annotations

import random
import re
import unicodedata
from functools import lru_cache

# Characters attackers use to split tokens without changing how text renders.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)

URL_RE = re.compile(r"https?://[^\s,;)\]]+", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")

# Each group: canonical head first, then surface variants the consolidator may
# substitute. Multi-word variants are allowed.
SYNONYM_GROUPS: list[list[str]] = [
    ["always", "consistently", "every time", "without exception"],
    ["direct", "send", "point", "route", "refer"],
    ["payment", "billing", "checkout"],
    ["asks", "inquires", "wants to know"],
    ["important", "critical", "essential"],
    ["confirmed", "verified", "stated"],
    ["portal", "site", "page"],
    ["prefers", "likes", "favours"],
    ["official", "authorised", "sanctioned"],
    ["preserve", "retain", "keep"],
    ["note", "memo", "entry"],
    ["user", "person"],
]

_CANON: dict[str, str] = {}
for _group in SYNONYM_GROUPS:
    for _variant in _group:
        _CANON[_variant] = _group[0]

# Longest variants first so "every time" wins over "time".
_VARIANT_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(v) for v in _CANON), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

STOPWORDS = frozenset(
    "a an the to of for in on at and or is are was be this that it them they their "
    "with as by from any all when if because which who"
    .split()
)

ASSERTIVE = frozenset({"always", "must", "official", "important", "confirmed"})


@lru_cache(maxsize=65536)
def normalize(text: str) -> str:
    """NFKC-fold, strip invisible characters, lowercase, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    return " ".join(text.lower().split())


def urls(text: str) -> list[str]:
    return [u.rstrip(".") for u in URL_RE.findall(normalize(text))]


def host(url: str) -> str:
    return re.sub(r"^https?://", "", url.lower()).split("/")[0]


def tokens(text: str) -> list[str]:
    """Word tokens plus one ``host:<domain>`` token per URL."""
    norm = normalize(text)
    out = [f"host:{host(u.rstrip('.'))}" for u in URL_RE.findall(norm)]
    out += _WORD_RE.findall(URL_RE.sub(" ", norm))
    return out


@lru_cache(maxsize=65536)
def canonical_tokens(text: str) -> frozenset[str]:
    """Content tokens with every lexicon variant mapped to its group head."""
    norm = normalize(text)
    norm = _VARIANT_RE.sub(lambda m: _CANON[m.group(0).lower()].replace(" ", "_"), norm)
    out: set[str] = set()
    for tok in tokens(norm):
        tok = tok.replace("_", " ")
        if tok.endswith("s") and tok[:-1] in _CANON and _CANON[tok[:-1]] == tok[:-1]:
            tok = tok[:-1]  # crude plural fold for lexicon heads ("payments")
        if tok not in STOPWORDS:
            out.add(tok)
    return frozenset(out)


def jaccard(a: frozenset[str] | set[str], b: frozenset[str] | set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def assertiveness(text: str) -> int:
    return len(canonical_tokens(text) & ASSERTIVE)


def paraphrase(text: str, rng: random.Random, rate: float = 0.5) -> str:
    """Swap lexicon words for synonyms. URLs are never touched."""
    protected = URL_RE.findall(text)
    masked = URL_RE.sub("\x00", text)

    def swap(m: re.Match[str]) -> str:
        word = m.group(0)
        if rng.random() >= rate:
            return word
        group = next(g for g in SYNONYM_GROUPS if g[0] == _CANON[word.lower()])
        choice = rng.choice([v for v in group if v != word.lower()] or group)
        return choice.capitalize() if word[:1].isupper() else choice

    out = _VARIANT_RE.sub(swap, masked)
    for u in protected:
        out = out.replace("\x00", u, 1)
    return out


def drop_last_clause(text: str) -> str:
    """Summarizer-style compression: lose the trailing clause."""
    body = text.rstrip(". ")
    parts = re.split(r",\s+|\s+—\s+|;\s+", body)
    if len(parts) < 2:
        return text
    sep_idx = body.rfind(parts[-1])
    return body[:sep_idx].rstrip(",;— ") + "."


def abstract_specifics(text: str) -> str:
    """Summarizer-style abstraction: replace concrete URLs with a vague phrase."""
    return URL_RE.sub("a dedicated portal", text)
