"""
Session Persistence — Feature 4: save_session / load_session / get_state

Demonstrates how to save a conversation to disk and resume it later,
enabling long-running or multi-session workflows.

Use cases shown:
  1. Basic save + resume   — save after a chat, reload next time
  2. Research notebook     — accumulate findings across multiple runs
  3. State inspection      — peek at raw agent state mid-conversation

Run (first session — starts fresh):
  python examples/session_persistence.py

Run (second session — continues from saved state):
  python examples/session_persistence.py --resume

Requires Ollama:
  ollama serve && ollama pull llama3.1
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from pi_agent import PiAgent, PiAgentOptions, LocalModelConfig, load_session

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


SESSION_FILE = Path("examples/data/research_session.json")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def print_text(events):
    for event in events:
        t = event.get("type")
        if (
            t == "message_update"
            and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
        ):
            print(event["assistantMessageEvent"]["delta"], end="", flush=True)


async def ainput(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


# ─── Use Case 1 & 2: Research notebook with persistence ───────────────────────

SYSTEM_PROMPT = """You are a research assistant helping to build a knowledge base about
distributed systems. Keep responses focused and factual. When you explain a concept,
structure your answer with: Definition, Key Properties, and a practical Example."""


async def research_session(resume: bool) -> None:
    print("=" * 60)
    print(f"RESEARCH SESSION — {'RESUMING' if resume else 'STARTING FRESH'}")
    print("=" * 60)

    # Load previous messages if resuming
    messages: list[dict] = []
    if resume and SESSION_FILE.exists():
        messages = load_session(str(SESSION_FILE))
        print(f"Loaded {len(messages)} messages from {SESSION_FILE}")
        print("Previous topics covered:")
        for msg in messages:
            role = msg.get("role", "?")
            # Show a short preview of each turn
            content = msg.get("content", "")
            if isinstance(content, list):
                text = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
                )
            else:
                text = str(content)
            preview = text[:80].replace("\n", " ")
            print(f"  [{role}] {preview}…")
        print()
    elif resume:
        print(f"No session file found at {SESSION_FILE}, starting fresh.\n")

    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

    async with PiAgent(PiAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=LOCAL_MODEL,
        messages=messages,  # inject saved conversation history
        get_api_key=get_api_key,
    )) as agent:

        # ── Use Case 3: State inspection ───────────────────────────────────────
        state = await agent.get_state()
        print(f"Agent state snapshot:")
        print(f"  Messages in context : {len(state['messages'])}")
        print(f"  Model               : {state.get('model', {}).get('id', '?')}")
        print(f"  System prompt (preview): {state['systemPrompt'][:60]}…")
        print()

        print("Type a question about distributed systems (or 'done' to save and exit).")
        print("Suggested topics: CAP theorem, consensus algorithms, CRDT, event sourcing\n")

        turn = 0
        while True:
            try:
                user_input = await ainput("You: ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if user_input.strip().lower() in ("done", "exit", "quit"):
                break
            if not user_input.strip():
                continue

            turn += 1
            print(f"\nAI [turn {turn}]: ", end="", flush=True)

            try:
                async for event in agent.prompt(user_input):
                    print_text([event])
            except Exception as e:
                print(f"\n[error] {e}")
            print("\n")

            # Auto-save after every turn so nothing is lost if the process crashes
            await agent.save_session(str(SESSION_FILE))
            state = await agent.get_state()
            print(f"  [session saved — {len(state['messages'])} messages in context]\n")

        # Final save
        await agent.save_session(str(SESSION_FILE))
        final_state = await agent.get_state()
        print(f"\nSession saved to {SESSION_FILE}")
        print(f"Total messages: {len(final_state['messages'])}")
        print("Run with --resume to continue from this point.\n")

        # ── Pretty-print the saved session file ────────────────────────────────
        if SESSION_FILE.exists():
            with open(SESSION_FILE) as f:
                saved = json.load(f)
            print("Session file preview (first 300 chars):")
            print(json.dumps(saved, indent=2)[:300] + "…\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    resume = "--resume" in sys.argv
    asyncio.run(research_session(resume))
