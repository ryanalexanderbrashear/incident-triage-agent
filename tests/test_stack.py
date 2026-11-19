"""Pin the SDK versions and API names that docs/adr/0001-stack.md records.

A version bump that renames one of these fails here, by name, rather than in an agent run.
Delete once the server and agent tests exercise the same names.
"""

from importlib.metadata import version

import pytest

ADR_001_PINS = {
    "anthropic": "2.11.0",
    "langfuse": "5.2.0",
    "mcp": "2.3.0",
    "pydantic": "2.14.4",
}


@pytest.mark.parametrize(("package", "pinned"), sorted(ADR_001_PINS.items()))
def test_installed_version_matches_adr_001(package: str, pinned: str) -> None:
    assert version(package) == pinned, f"{package} differs from ADR-001; update the ADR and pin"


def test_mcp_v2_names() -> None:
    from mcp import Client
    from mcp.server import MCPServer

    assert MCPServer.__name__ == "MCPServer"
    assert Client.__name__ == "Client"


def test_langfuse_v5_otel_api() -> None:
    from langfuse import Langfuse, get_client, observe

    assert callable(get_client)
    assert callable(observe)
    assert hasattr(Langfuse, "start_as_current_observation")
    assert not hasattr(Langfuse, "trace"), "legacy v2 client API is back; ADR-001 assumes v5"


def test_anthropic_messages_api() -> None:
    from anthropic import Anthropic

    assert hasattr(Anthropic, "messages")
