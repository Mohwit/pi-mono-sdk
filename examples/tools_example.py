"""Example using Python-defined tools."""

import asyncio
from pi_agent import PiAgent, PiAgentOptions, ModelConfig, ToolDefinition


async def read_file(params: dict) -> dict:
    with open(params["path"]) as f:
        return {"content": [{"type": "text", "text": f.read()}]}


async def main() -> None:
    async with PiAgent(PiAgentOptions(
        system_prompt="You are a coding assistant.",
        model=ModelConfig(provider="openai", name="gpt-4o"),
        tools=[
            ToolDefinition(
                name="read_file",
                description="Read a file's contents",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "File path to read"}},
                    "required": ["path"],
                },
                execute=read_file,
            )
        ],
    )) as agent:
        async for event in agent.prompt("Read README.md and summarize it."):
            if event.get("type") == "message_update":
                print(event.get("delta", ""), end="", flush=True)
    print()


asyncio.run(main())
