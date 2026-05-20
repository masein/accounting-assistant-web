"""AI accountant — conversational bookkeeping assistant.

Architecture: tool-using Anthropic agent. The LLM has access to a fixed
set of typed tools; it cannot write to the DB directly. Every write
goes through a proposal → confirmation → execute loop with idempotency
tokens and audit logging.

This package contains:

* ``anthropic_client`` — async SDK wrapper for Claude with tool use,
  prompt caching, and typed exception handling.
* ``tools`` — the tool catalogue: read tools (free) and proposal /
  execute tools (gated by ``confirmation_token``).
* ``orchestrator`` — drives the tool-use loop, handles session memory
  and idempotency.
"""
