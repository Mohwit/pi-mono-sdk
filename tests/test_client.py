"""End-to-end client test — skipped when no API key is present."""

import asyncio
import os
import pytest

from pi_agent import PiAgent, PiAgentOptions, ModelConfig


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
async def test_simple_prompt() -> None:
    """Agent should stream events and complete a turn."""
    events: list[dict] = []

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a helpful assistant. Be brief.",
        model=ModelConfig(provider="anthropic", name="claude-haiku-4-5-20251001"),
    )) as agent:
        async for event in agent.prompt("Say exactly: hello"):
            events.append(event)

    assert len(events) > 0, "Expected at least one event"
    types = {e.get("type") for e in events}
    assert "agent_end" in types or "message_end" in types, (
        f"Expected agent_end or message_end event, got: {types}"
    )
