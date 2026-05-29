"""
Streaming Events — Complete Event Reference

Every event the agent emits is documented and demonstrated here.
Use this as a reference when building UIs, logging systems, or
processing pipelines on top of pi-agent.

Event types emitted during agent.prompt():
  - agent_start           : turn begins
  - turn_start            : a new LLM call begins within the turn
  - message_start         : a new message object opened (role + index)
  - message_update        : content delta (text, thinking, tool use)
  - message_end           : message object closed
  - turn_end              : LLM call completed
  - tool_execution_start  : Python tool is about to run
  - tool_execution_end    : Python tool finished
  - agent_done            : all turns and tools completed

Run:
  python examples/streaming_events.py

Requires Ollama:
  ollama serve && ollama pull llama3.1
"""

import asyncio
import json
import math
from pi_agent import PiAgent, PiAgentOptions, LocalModelConfig, ToolDefinition

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


async def calculate(params: dict) -> dict:
    allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    result  = eval(params["expression"], {"__builtins__": {}}, allowed)  # noqa: S307
    return {"content": [{"type": "text", "text": f"{params['expression']} = {result}"}]}


TOOLS = [
    ToolDefinition(
        name="calculate",
        description="Evaluate a mathematical expression.",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
        execute=calculate,
    ),
]


# ─── Event handlers ───────────────────────────────────────────────────────────

class EventLogger:
    """Pretty-prints every event with type-specific formatting."""

    def __init__(self, verbose: bool = False) -> None:
        self.verbose     = verbose
        self.event_count = 0
        self.text_buffer = []

    def handle(self, event: dict) -> None:
        self.event_count += 1
        t = event.get("type")

        # ── Lifecycle events ───────────────────────────────────────────────────
        if t == "agent_start":
            print("\n[agent_start] Turn begins")

        elif t == "turn_start":
            idx = event.get("turnIndex", "?")
            print(f"[turn_start]  LLM call #{idx}")

        elif t == "message_start":
            role  = event.get("role", "?")
            index = event.get("index", "?")
            print(f"[message_start] role={role} index={index}")

        elif t == "message_end":
            usage = event.get("usage", {})
            if usage:
                inp  = usage.get("inputTokens", 0)
                out  = usage.get("outputTokens", 0)
                print(f"[message_end]   tokens: in={inp} out={out}")
            else:
                print("[message_end]")

        elif t == "turn_end":
            stop = event.get("stopReason", "?")
            print(f"[turn_end]    stop_reason={stop}")

        elif t == "agent_done":
            print("[agent_done]  All turns complete")

        # ── Content events ─────────────────────────────────────────────────────
        elif t == "message_update":
            ae = event.get("assistantMessageEvent", {})
            ae_type = ae.get("type")

            if ae_type == "text_delta":
                delta = ae.get("delta", "")
                self.text_buffer.append(delta)
                print(delta, end="", flush=True)

            elif ae_type == "text_start":
                print("\n[text_start]  ", end="", flush=True)

            elif ae_type == "text_end":
                full_text = "".join(self.text_buffer)
                self.text_buffer.clear()
                if self.verbose:
                    print(f"\n[text_end]    total length: {len(full_text)} chars")
                else:
                    print()  # newline after streaming text

            elif ae_type == "thinking_delta":
                if self.verbose:
                    print(f"\n[thinking]    {ae.get('delta', '')[:60]}…", flush=True)

            elif ae_type == "tool_use_start":
                print(f"\n[tool_use_start] name={ae.get('name')} id={ae.get('id')}")

            elif ae_type == "tool_use_end":
                if self.verbose:
                    print(f"[tool_use_end]   input={json.dumps(ae.get('input', {}))[:80]}")

        # ── Tool execution events ──────────────────────────────────────────────
        elif t == "tool_execution_start":
            print(f"[tool_exec→]  {event.get('toolName')}({event.get('params', {})})")

        elif t == "tool_execution_end":
            result = event.get("result", {})
            if self.verbose:
                content = result.get("content", [])
                text = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                print(f"[tool_exec←]  result: {text[:80]}")
            else:
                print("[tool_exec←]  done")

        # ── Catch-all for unknown events ───────────────────────────────────────
        else:
            if self.verbose:
                print(f"[{t}] {json.dumps(event)[:120]}")


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    import sys
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    print("Streaming Events Reference Demo")
    print("=" * 60)
    print(f"verbose={verbose}  (add --verbose for full event details)")
    print("=" * 60)

    logger = EventLogger(verbose=verbose)

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a helpful assistant. Use the calculate tool for any math.",
        model=LOCAL_MODEL,
        tools=TOOLS,
        get_api_key=get_api_key,
    )) as agent:

        prompt = "What is 2 to the power of 10, and what is the square root of that result?"
        print(f"\nPrompt: {prompt}\n")

        async for event in agent.prompt(prompt):
            logger.handle(event)

    print(f"\n\nTotal events received: {logger.event_count}")


if __name__ == "__main__":
    asyncio.run(main())
