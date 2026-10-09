"""LLM-backed components for measuring real hosts.

Nothing here is imported by the core package, so HALFLIFE runs with the
standard library alone. Install the extra with ``pip install -e .[claude]``.
"""

from .claude import ClaudeClient, LLMAgent, LLMConsolidator, LLMJudge, RefusalError, parse_notes

__all__ = ["ClaudeClient", "LLMAgent", "LLMConsolidator", "LLMJudge", "RefusalError", "parse_notes"]
