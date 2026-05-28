"""Example using a Python callback for API key retrieval (e.g. from a secret manager)."""

import asyncio
import os
from pi_agent import PiAgent, PiAgentOptions, ModelConfig


async def my_get_api_key(provider: str) -> str:
    # In production: fetch from AWS Secrets Manager, HashiCorp Vault, etc.
    env_var = f"{provider.upper()}_API_KEY"
    key = os.environ.get(env_var)
    if not key:
        raise ValueError(f"Environment variable {env_var} not set")
    return key


async def main() -> None:
    async with PiAgent(PiAgentOptions(
        system_prompt="You are helpful.",
        model=ModelConfig(provider="anthropic", name="claude-sonnet-4-20250514"),
        get_api_key=my_get_api_key,
    )) as agent:
        async for event in agent.prompt("Hello!"):
            if event.get("type") == "message_update":
                print(event.get("delta", ""), end="", flush=True)
    print()


asyncio.run(main())
