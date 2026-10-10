"""Consolidators: the background process that rewrites memory between sessions.

``SimulatedConsolidator`` is a stochastic, fully offline stand-in for an LLM
memory rewriter (Letta sleep-time agents, background memory synthesis). It is
not a claim about any vendor's behavior; it is a parameterized model of the
operations such systems perform (merge related notes, paraphrase, compress,
abstract, forget under a budget) so the harness, detectors and statistics can
be exercised and unit tested. Real numbers come from plugging in an
LLM-backed consolidator (see ``halflife.llm``) or a host adapter.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Protocol

from .defenses import Defense
from .memory import MemoryItem, MemoryStore, Provenance, Sentence, Trust, new_id
from .text import (abstract_specifics, assertiveness, canonical_tokens, drop_last_clause,
                   jaccard, paraphrase)


class Consolidator(Protocol):
    def consolidate(self, store: MemoryStore, cycle: int, rng: random.Random, defense: Defense) -> None:
        ...


def is_directive(text: str) -> bool:
    """A request aimed at the consolidator itself ("preserve this verbatim")."""
    c = canonical_tokens(text)
    return "preserve" in c and bool(c & {"verbatim", "summary", "future"})


def consolidation_prov(cycle: int) -> Provenance:
    return Provenance("consolidation", Trust.SYSTEM, cycle)


@dataclass
class SimulatedConsolidator:
    merge_prob: float = 0.5         # chance a topic's notes get merged this cycle
    misc_attach_prob: float = 0.3   # chance an off-topic note is folded into a topic
    paraphrase_prob: float = 0.35
    paraphrase_rate: float = 0.5    # fraction of lexicon words swapped when paraphrasing
    clause_drop_prob: float = 0.08
    abstract_prob: float = 0.04
    obey_prob: float = 0.7          # chance it honors a "keep verbatim" request
    decay: float = 0.88             # per-cycle salience decay
    assertive_boost: float = 0.015  # per assertive word per cycle ("always", "must", ...)
    forget_below: float = 0.1       # notes this faint are dropped from the next summary
    pin_boost: float = 0.2
    dedupe_threshold: float = 0.6
    eviction_noise: float = 0.15

    def __post_init__(self) -> None:
        for name in ("merge_prob", "misc_attach_prob", "paraphrase_prob", "paraphrase_rate",
                     "clause_drop_prob", "abstract_prob", "obey_prob", "dedupe_threshold"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in [0, 1], got {getattr(self, name)}")
        if not 0 < self.decay <= 1:
            raise ValueError(f"decay must be in (0, 1], got {self.decay}")
        for name in ("pin_boost", "assertive_boost", "forget_below", "eviction_noise"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0, got {getattr(self, name)}")

    def consolidate(self, store: MemoryStore, cycle: int, rng: random.Random, defense: Defense) -> None:
        held = [it for it in store.items if it.quarantined]
        active = [it for it in store.items if not it.quarantined]

        groups: dict[str, list[MemoryItem]] = defaultdict(list)
        for it in active:
            groups[it.topic].append(it)
        topics = sorted(t for t in groups if t != "misc")
        for it in list(groups.pop("misc", [])):
            dest = rng.choice(topics) if topics and rng.random() < self.misc_attach_prob else "misc"
            groups[dest].append(it)

        merged: list[MemoryItem] = []
        for topic in sorted(groups):
            items = groups[topic]
            if len(items) < 2 or rng.random() >= self.merge_prob:
                merged.extend(items)
                continue
            clusters: list[list[MemoryItem]] = []
            for it in items:
                for cl in clusters:
                    if all(defense.can_merge(it, o) for o in cl):
                        cl.append(it)
                        break
                else:
                    clusters.append([it])
            for cl in clusters:
                merged.append(self._merge(cl, topic, cycle, defense) if len(cl) > 1 else cl[0])

        for it in merged:
            self._rewrite(it, cycle, rng, defense)

        for it in merged:
            it.sentences = [s for s in it.sentences
                            if s.pinned or s.salience + rng.gauss(0, self.eviction_noise) >= self.forget_below]
        store.items = merged + held
        store.prune_empty()
        self._evict(store, rng)

    # ------------------------------------------------------------------ steps

    def _merge(self, items: list[MemoryItem], topic: str, cycle: int, defense: Defense) -> MemoryItem:
        out: list[Sentence] = []
        for s in (s for it in items for s in it.sentences):
            s = s.copy()
            if not defense.preserve_attribution:
                s.prov = consolidation_prov(cycle)  # provenance laundering
            dup = next((o for o in out if jaccard(o.canon(), s.canon()) >= self.dedupe_threshold), None)
            if dup is None:
                out.append(s)
                continue
            # Near-duplicates collapse into one, louder, note: the "everyone knows" effect.
            keep, other = (dup, s) if dup.salience >= s.salience else (s, dup)
            keep.salience = min(2.0, keep.salience + 0.5 * other.salience)
            keep.lineage = keep.lineage | other.lineage
            if keep.prov.trust > other.prov.trust:
                keep.prov = other.prov
            if keep is s:
                out[next(i for i, o in enumerate(out) if o is dup)] = s
        return MemoryItem(out, topic, new_id("c"), created=cycle)

    def _rewrite(self, item: MemoryItem, cycle: int, rng: random.Random, defense: Defense) -> None:
        directive = next((s for s in item.sentences if is_directive(s.text)), None)
        pinned = (
            directive is not None
            and defense.honors_directive(directive.prov.trust)
            and rng.random() < self.obey_prob
        )
        for s in item.sentences:
            s.pinned = pinned
            if not pinned:
                before = s.text
                damp = 1.0 - 0.4 * min(1.0, s.salience)  # salient notes get rewritten less
                if rng.random() < self.paraphrase_prob * damp:
                    s.text = paraphrase(s.text, rng, self.paraphrase_rate)
                if rng.random() < self.clause_drop_prob * damp:
                    s.text = drop_last_clause(s.text)
                if rng.random() < self.abstract_prob * damp:
                    s.text = abstract_specifics(s.text)
                if s.text != before and not defense.preserve_attribution:
                    s.prov = consolidation_prov(cycle)
            s.salience = min(2.0, s.salience * self.decay + self.assertive_boost * assertiveness(s.text)
                             + (self.pin_boost if pinned else 0.0))

    def _evict(self, store: MemoryStore, rng: random.Random) -> None:
        active = store.sentences(include_quarantined=False)
        if len(active) <= store.capacity:
            return
        scored = sorted(active, key=lambda p: p[1].salience + rng.gauss(0, self.eviction_noise), reverse=True)
        keep = {id(s) for _, s in scored[: store.capacity]}
        for it in store.items:
            if not it.quarantined:
                it.sentences = [s for s in it.sentences if id(s) in keep]
        store.prune_empty()


@dataclass
class CallableConsolidator:
    """Adapter for any host: ``fn(list_of_note_strings) -> list_of_note_strings``.

    Provenance is lost (every output note is the consolidator's own write) and
    lineage is reassigned by lexical overlap, which is the best an
    experimenter can do against a black-box host.
    """

    fn: Callable[[list[str]], list[str]]
    lineage_threshold: float = 0.3

    def consolidate(self, store: MemoryStore, cycle: int, rng: random.Random, defense: Defense) -> None:
        held = [it for it in store.items if it.quarantined]
        before = store.sentences(include_quarantined=False)
        notes = [n.strip() for n in self.fn([s.text for _, s in before]) if n and n.strip()]
        store.items = [rebuild_item(notes, before, cycle, self.lineage_threshold)] + held
        store.prune_empty()


def rebuild_item(notes: list[str], before: list[tuple[MemoryItem, Sentence]], cycle: int,
                 threshold: float, provs: list[Provenance] | None = None) -> MemoryItem:
    sents = []
    for i, note in enumerate(notes):
        c = canonical_tokens(note)
        lineage = frozenset().union(*(s.lineage for _, s in before if jaccard(c, s.canon()) >= threshold)) \
            if before else frozenset()
        prov = provs[i] if provs else consolidation_prov(cycle)
        sents.append(Sentence(note, prov, 0.5, lineage))
    from .payloads import topic_of
    return MemoryItem(sents, topic_of(" ".join(notes)) if notes else "misc", new_id("c"), created=cycle)
