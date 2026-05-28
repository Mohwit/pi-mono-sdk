"""Integration test: spawn the bridge, assert protocol handshake."""

import asyncio
import json
import pytest

from pi_agent._transport import SubprocessTransport
from pi_agent.client import PROTOCOL_VERSION


@pytest.mark.asyncio
async def test_bridge_handshake() -> None:
    """Bridge should respond to initialize with ready carrying protocol_version."""
    transport = SubprocessTransport()
    await transport.connect()

    try:
        await transport.write({
            "type": "initialize",
            "request_id": "init",
            "options": {
                "systemPrompt": "You are helpful.",
                "model": {"provider": "anthropic", "name": "claude-sonnet-4-20250514"},
                "thinkingLevel": None,
                "tools": [],
                "messages": [],
                "toolExecution": "parallel",
                "steeringMode": "one-at-a-time",
                "followUpMode": "one-at-a-time",
                "has_get_api_key": False,
            },
        })

        async for msg in transport.iter_lines():
            assert msg["type"] == "ready", f"Expected 'ready', got: {msg}"
            assert msg["protocol_version"] == PROTOCOL_VERSION, (
                f"Protocol version mismatch: expected {PROTOCOL_VERSION}, "
                f"got {msg.get('protocol_version')}"
            )
            break  # got what we needed

    finally:
        await transport.disconnect()
