"""The response-size budget every incident-mcp response stays within.

The SDK sends a structured tool result twice: as `structured_content` and as indented JSON text in
`content`. The budget applies to each form separately. 16,000 characters is roughly 4,000 tokens
per form. Single-item responses fit it by construction (the fixture size limits); search_logs
pages are cut between records to fit it.
"""

from __future__ import annotations

RESPONSE_BUDGET_CHARS = 16_000

# Allowance for a search page's fields other than its records (ids, counts, brackets, indents).
PAGE_ENVELOPE_CHARS = 512
