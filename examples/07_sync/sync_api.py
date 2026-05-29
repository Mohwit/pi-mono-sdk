"""
Sync API — Feature 3: SyncPiAgent

Demonstrates the synchronous wrapper for use cases where asyncio is unavailable
or inconvenient: plain scripts, data pipelines, Jupyter notebooks, Flask routes.

Use cases shown:
  1. Batch processing — run prompts over a list of items sequentially
  2. Script mode    — simple one-shot question, no async boilerplate
  3. REPL mode      — synchronous interactive chat loop

Run:
  python examples/sync_api.py

Requires Ollama:
  ollama serve && ollama pull llama3.1
"""

import sys
from pi_agent import SyncPiAgent, PiAgentOptions, LocalModelConfig, ToolDefinition

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


# ─── Helper: extract text from event list ─────────────────────────────────────

def extract_text(events: list[dict]) -> str:
    parts = []
    for event in events:
        t = event.get("type")
        if t == "message_update":
            ae = event.get("assistantMessageEvent", {})
            if ae.get("type") == "text_delta":
                parts.append(ae["delta"])
    return "".join(parts)


# ─── Use Case 1: Batch processing ─────────────────────────────────────────────

def batch_classify(items: list[str]) -> list[dict]:
    """
    Classify a list of customer feedback items as positive/negative/neutral.
    With SyncPiAgent you can call agent.prompt() in a plain for-loop.
    """
    print("=" * 60)
    print("USE CASE 1: Batch Classification")
    print("=" * 60)

    results = []

    with SyncPiAgent(PiAgentOptions(
        system_prompt=(
            "You are a sentiment classifier. "
            "Reply with exactly one word: POSITIVE, NEGATIVE, or NEUTRAL. "
            "No explanation, no punctuation."
        ),
        model=LOCAL_MODEL,
        get_api_key=get_api_key,
    )) as agent:
        for item in items:
            events = agent.prompt(item)
            label  = extract_text(events).strip().upper()
            results.append({"text": item, "label": label})
            print(f"  [{label:8s}]  {item}")
            # Reset conversation so each classification is independent
            agent.reset()

    print()
    return results


# ─── Use Case 2: One-shot script ──────────────────────────────────────────────

def one_shot_query(question: str) -> str:
    """Single synchronous question — no async, no event loop management."""
    print("=" * 60)
    print("USE CASE 2: One-shot Script Query")
    print("=" * 60)
    print(f"Q: {question}")

    with SyncPiAgent(PiAgentOptions(
        system_prompt="You are a concise assistant. Answer in 2-3 sentences max.",
        model=LOCAL_MODEL,
        get_api_key=get_api_key,
    )) as agent:
        events = agent.prompt(question)
        answer = extract_text(events)

    print(f"A: {answer}\n")
    return answer


# ─── Use Case 3: Synchronous REPL ─────────────────────────────────────────────

MATH_TOOLS = [
    ToolDefinition(
        name="calculate",
        description="Evaluate a mathematical expression. Supports +, -, *, /, **, sqrt, etc.",
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Python-style math expression, e.g. '2 ** 10' or '(3 + 4) * 5'",
                },
            },
            "required": ["expression"],
        },
        execute=lambda params: {"content": [{"type": "text", "text": str(eval(  # noqa: S307
            params["expression"],
            {"__builtins__": {}, "abs": abs, "round": round,
             "min": min, "max": max, "sum": sum,
             **__import__("math").__dict__}
        ))}]},
    ),
]


def sync_repl() -> None:
    """
    Interactive chat using the sync API.
    Identical behaviour to the async version but runs in a plain while-loop.
    """
    print("=" * 60)
    print("USE CASE 3: Synchronous Interactive REPL")
    print("=" * 60)
    print("Chat with Llama 3.1 (sync mode). Type 'exit' to quit.\n")

    with SyncPiAgent(PiAgentOptions(
        system_prompt=(
            "You are a helpful math and general assistant. "
            "Use the calculate tool for any arithmetic."
        ),
        model=LOCAL_MODEL,
        tools=MATH_TOOLS,
        get_api_key=get_api_key,
    )) as agent:
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if user_input.lower() in ("exit", "quit", "q"):
                break
            if not user_input:
                continue

            events = agent.prompt(user_input)

            print("AI: ", end="", flush=True)
            for event in events:
                t = event.get("type")
                if (
                    t == "message_update"
                    and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
                ):
                    print(event["assistantMessageEvent"]["delta"], end="", flush=True)
                elif t == "tool_execution_start":
                    print(f"\n[tool] {event.get('toolName')}({event.get('params', {})})", flush=True)
                elif t == "tool_execution_end":
                    print("[done]", flush=True)
            print()

    print("Goodbye!")


# ─── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode in ("batch", "all"):
        feedback = [
            "This product completely changed my workflow, absolutely love it!",
            "Terrible customer service, waited 3 weeks and got the wrong item.",
            "It works fine I guess, nothing special.",
            "Best purchase I've made this year without a doubt.",
            "The packaging was damaged but the item inside was okay.",
        ]
        batch_classify(feedback)

    if mode in ("oneshot", "all"):
        one_shot_query("What is the difference between concurrency and parallelism?")

    if mode in ("repl", "all"):
        sync_repl()
