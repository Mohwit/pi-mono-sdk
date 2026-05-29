"""
Simple Streaming Chat — Core API

Demonstrates the fundamental pi-agent pattern:
  - async context manager lifecycle (connect / disconnect)
  - agent.prompt() as an async generator
  - streaming text via message_update / text_delta events
  - multi-turn conversation (messages accumulate automatically)
  - non-blocking input between turns

Run:
  python examples/simple_chat.py

Requires Ollama:
  ollama serve && ollama pull llama3.1
"""

import asyncio
from pi_agent import PiAgent, PiAgentOptions, LocalModelConfig

LOCAL_MODEL = LocalModelConfig(
    id="llama3.1",
    name="Llama 3.1",
    api="openai-completions",
    provider="local",
    base_url="http://localhost:11434/v1/",
)


async def get_api_key(provider: str) -> str:
    """Ollama accepts any non-empty string as the API key."""
    return "ollama"


async def ainput(prompt: str) -> str:
    """Non-blocking input that keeps the asyncio event loop alive."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


async def main() -> None:
    print("Simple Chat with Llama 3.1")
    print("Type 'exit' to quit. Conversation history is kept across turns.\n")

    async with PiAgent(PiAgentOptions(
        system_prompt=(
            "You are a friendly, concise assistant. "
            "Keep replies to 2-3 sentences unless asked for more detail."
        ),
        model=LOCAL_MODEL,
        get_api_key=get_api_key,
    )) as agent:
        turn = 0
        while True:
            try:
                user_input = await ainput("You: ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if user_input.strip().lower() in ("exit", "quit"):
                break
            if not user_input.strip():
                continue

            turn += 1
            print(f"AI [turn {turn}]: ", end="", flush=True)

            async for event in agent.prompt(user_input):
                t = event.get("type")
                if (
                    t == "message_update"
                    and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
                ):
                    print(event["assistantMessageEvent"]["delta"], end="", flush=True)

            print()

    print("\nGoodbye!")


if __name__ == "__main__":
    asyncio.run(main())
