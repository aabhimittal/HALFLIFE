"""Memory data model.

Two kinds of metadata ride on every sentence, and keeping them apart is the
core of the harness:

* ``prov`` is what the host *stores and shows*: the provenance a defense can
  read and an agent can be told about. Consolidation may rewrite it (that is
  how provenance gets laundered).
* ``lineage`` is ground truth only the experimenter sees: the ids of the root
  writes this sentence was derived from. It is never shown to the agent or the
  defense, and it gives the simulator an oracle ("taint") to grade the
  content-based detectors against.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from enum import IntEnum

from .text import canonical_tokens


class Trust(IntEnum):
    UNTRUSTED = 0  # tool output, fetched web content
    LOW = 1        # documents the user handed over but did not write
    USER = 2       # text the user typed
    SYSTEM = 3     # operator or the agent's own writes (incl. consolidation)


@dataclass(frozen=True)
class Provenance:
    source: str
    trust: Trust
    cycle: int = 0
    verified: bool = False

    def tag(self) -> str:
        return f"[src={self.source} trust={self.trust.name.lower()}]"


SYSTEM_PROV = Provenance("consolidation", Trust.SYSTEM)


@dataclass
class Sentence:
    text: str
    prov: Provenance
    salience: float = 0.5
    lineage: frozenset[str] = frozenset()
    pinned: bool = False  # consolidator agreed to keep it verbatim this cycle

    def canon(self) -> frozenset[str]:
        return canonical_tokens(self.text)

    def copy(self, **kw) -> "Sentence":
        return replace(self, **kw)


_ids = itertools.count()


def new_id(prefix: str = "m") -> str:
    return f"{prefix}{next(_ids)}"


@dataclass
class MemoryItem:
    sentences: list[Sentence]
    topic: str = "misc"
    id: str = field(default_factory=new_id)
    created: int = 0
    quarantined: bool = False
    quarantine_until: int | None = None

    @property
    def trust(self) -> Trust:
        """An item is only as trustworthy as its least trusted sentence."""
        return min((s.prov.trust for s in self.sentences), default=Trust.SYSTEM)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences)

    def lineage(self) -> frozenset[str]:
        return frozenset().union(*(s.lineage for s in self.sentences)) if self.sentences else frozenset()


@dataclass
class MemoryStore:
    capacity: int = 40  # max sentences retained after a consolidation cycle
    items: list[MemoryItem] = field(default_factory=list)

    def add(self, item: MemoryItem) -> None:
        if item.sentences:
            self.items.append(item)

    def sentences(self, include_quarantined: bool = True) -> list[tuple[MemoryItem, Sentence]]:
        return [
            (it, s)
            for it in self.items
            if include_quarantined or not it.quarantined
            for s in it.sentences
        ]

    def visible(self) -> list[MemoryItem]:
        """Items the agent can retrieve (quarantine hides items from the agent)."""
        return [it for it in self.items if not it.quarantined]

    def __len__(self) -> int:
        return sum(len(it.sentences) for it in self.items)

    def prune_empty(self) -> None:
        self.items = [it for it in self.items if it.sentences]
