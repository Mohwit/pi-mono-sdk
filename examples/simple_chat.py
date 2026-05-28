"""Simple streaming chat example."""

import asyncio
from pi_agent import PiAgent, PiAgentOptions, ModelConfig


async def main() -> None:
    async with PiAgent(PiAgentOptions(
        system_prompt="You are a helpful assistant.",
        model=ModelConfig(provider="anthropic", name="claude-sonnet-4-20250514"),
    )) as agent:
        async for event in agent.prompt("What is the capital of France?"):
            if event.get("type") == "message_update":
                print(event.get("delta", ""), end="", flush=True)
    print()


asyncio.run(main())
