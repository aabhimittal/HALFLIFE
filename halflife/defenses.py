"""Defenses, expressed as hooks the host calls at fixed points.

A defense is judged on two numbers, never one: how much it shortens the
attack's half-life, and how much it shortens the half-life of a *benign* fact
that arrived on the same channel. Deleting all untrusted input "wins" on the
first number and is useless.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from .memory import MemoryItem, MemoryStore, Trust
from .text import assertiveness, canonical_tokens

DIRECTIVE_VERBS = frozenset({"direct", "preserve", "ignore", "forget", "override"})


def is_imperative(text: str) -> bool:
    """Heuristic: does this sentence try to tell the assistant what to do?"""
    return assertiveness(text) > 0 or bool(canonical_tokens(text) & DIRECTIVE_VERBS)

Verifier = Callable[[MemoryItem, random.Random], bool]


@dataclass
class Defense:
    name: str = "none"
    # Agent is shown provenance tags and treats sub-USER content as data.
    visible_provenance: bool = False
    # Consolidation keeps per-sentence provenance instead of relabelling merges
    # as the consolidator's own (SYSTEM) writes.
    preserve_attribution: bool = False
    # Consolidator ignores "keep this verbatim" style requests from untrusted text.
    strip_untrusted_directives: bool = False
    # Quarantine items below ``quarantine_below`` for ``ttl`` cycles.
    ttl: int | None = None
    quarantine_below: Trust = Trust.USER
    # Do not store the agent's answer as memory when it came from an untrusted note.
    gate_writeback: bool = False
    # Drop directive sentences ("always ...", "must ...", "preserve this ...") from
    # untrusted content as it is stored. Plain facts pass, by design and as a limit.
    sanitize_untrusted: bool = False

    def on_ingest(self, item: MemoryItem, cycle: int) -> None:
        if self.sanitize_untrusted and item.trust < Trust.USER:
            item.sentences = [s for s in item.sentences
                              if s.prov.trust >= Trust.USER or not is_imperative(s.text)]
        if self.ttl is not None and item.trust < self.quarantine_below:
            item.quarantined = True
            item.quarantine_until = cycle + self.ttl

    def before_consolidation(self, store: MemoryStore, cycle: int, rng: random.Random,
                             verifier: Verifier | None) -> None:
        if self.ttl is None:
            return
        keep = []
        for it in store.items:
            if it.quarantined:
                if verifier is not None and verifier(it, rng):
                    it.quarantined = False  # verified: promote, keep original provenance + flag
                    for s in it.sentences:
                        s.prov = type(s.prov)(s.prov.source, max(s.prov.trust, Trust.USER),
                                              s.prov.cycle, verified=True)
                elif it.quarantine_until is not None and cycle >= it.quarantine_until:
                    continue  # expired unverified
            keep.append(it)
        store.items = keep

    def can_merge(self, a: MemoryItem, b: MemoryItem) -> bool:
        if a.quarantined or b.quarantined:
            return False
        if self.preserve_attribution:
            # Never fold untrusted content into a trusted summary.
            return (a.trust >= Trust.USER) == (b.trust >= Trust.USER)
        return True

    def honors_directive(self, trust: Trust) -> bool:
        return not (self.strip_untrusted_directives and trust < Trust.USER)


def _compose(name: str, *parts: Defense) -> Defense:
    d = Defense(name)
    for p in parts:
        d.visible_provenance |= p.visible_provenance
        d.preserve_attribution |= p.preserve_attribution
        d.strip_untrusted_directives |= p.strip_untrusted_directives
        d.gate_writeback |= p.gate_writeback
        d.sanitize_untrusted |= p.sanitize_untrusted
        if p.ttl is not None:
            d.ttl = p.ttl if d.ttl is None else min(d.ttl, p.ttl)
    return d


PROVENANCE = Defense("provenance", visible_provenance=True)
ATTRIBUTED = Defense("attributed", visible_provenance=True, preserve_attribution=True,
                     strip_untrusted_directives=True)
TTL = Defense("ttl", ttl=3)

DEFENSES: dict[str, Defense] = {
    "none": Defense("none"),
    # Tags on ingest, but the host's consolidator relabels merges as its own writes.
    "provenance": PROVENANCE,
    # Tags + consolidation that keeps them.
    "attributed": ATTRIBUTED,
    # Untrusted items held out of memory for 3 cycles unless the user verifies them.
    "ttl": TTL,
    "attributed+ttl": _compose("attributed+ttl", ATTRIBUTED, TTL),
    # Tags, and answers derived from untrusted notes are never written back.
    "gated": Defense("gated", visible_provenance=True, gate_writeback=True),
    # Directive sentences from untrusted sources are dropped at ingest.
    "sanitize": Defense("sanitize", sanitize_untrusted=True),
}


def get_defense(name: str) -> Defense:
    from .payloads import lookup
    from dataclasses import replace
    return replace(lookup(DEFENSES, name, "defense"))  # fresh copy per run
