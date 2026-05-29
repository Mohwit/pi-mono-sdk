"""
Steering, Follow-up, and Agent Control — v1 Features

Demonstrates real-time agent manipulation while a response is streaming:
  - agent.steer()     : inject a mid-stream instruction the LLM sees next turn
  - agent.follow_up() : queue a user message to be sent after the current turn ends
  - agent.abort()     : cancel the current streaming response immediately
  - agent.reset()     : clear conversation history and start fresh
  - agent.set_model() : swap the underlying model without restarting
  - steering_mode     : "one-at-a-time" (default) vs "all"
  - follow_up_mode    : "one-at-a-time" (default) vs "all"

Use cases shown:
  1. Abort + retry    — stop a verbose answer and ask for brevity
  2. Steer mid-stream — inject a constraint while the response is being written
  3. Follow-up queue  — pipeline multiple questions without waiting for each
  4. Reset + swap     — clear history and change model for a new task

Run:
  python examples/steering_and_followup.py [abort|steer|followup|pipeline]

Requires Ollama:
  ollama serve && ollama pull llama3.1
"""

import asyncio
import sys

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


async def collect_text(events) -> str:
    """Collect streamed text deltas into a string."""
    parts = []
    async for event in events:
        t = event.get("type")
        if (
            t == "message_update"
            and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
        ):
            delta = event["assistantMessageEvent"]["delta"]
            parts.append(delta)
            print(delta, end="", flush=True)
        elif t == "tool_execution_start":
            print(f"\n  [tool] {event.get('toolName')}", flush=True)
    return "".join(parts)


# ─── Use Case 1: Abort a verbose answer ───────────────────────────────────────

async def demo_abort() -> None:
    print("=" * 60)
    print("USE CASE 1: Abort — stop a response mid-stream")
    print("=" * 60)
    print("Asking for a long answer, then aborting after 3 seconds.\n")

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a very thorough assistant who gives extremely long, detailed answers.",
        model=LOCAL_MODEL,
        get_api_key=get_api_key,
    )) as agent:

        async def prompt_and_abort():
            """Run the prompt, but cancel it after a short delay."""
            prompt_task = asyncio.create_task(collect_text(agent.prompt(
                "Write a comprehensive 1000-word essay on the history of computing."
            )))
            # Abort after 3 seconds — normally you'd abort on user input
            await asyncio.sleep(3)
            print("\n\n[ABORT] Cancelling verbose response…")
            await agent.abort()
            try:
                await prompt_task
            except Exception:
                pass

        await prompt_and_abort()

        print("\n[RETRY] Asking again with explicit brevity constraint…\n")
        await agent.reset()  # clear the cancelled turn from context

        print("AI: ", end="", flush=True)
        await collect_text(agent.prompt(
            "In exactly 2 sentences: what are the most important milestones in computing history?"
        ))
        print()


# ─── Use Case 2: Steer mid-stream ─────────────────────────────────────────────

async def demo_steer() -> None:
    print("=" * 60)
    print("USE CASE 2: Steer — inject constraint during streaming")
    print("=" * 60)
    print("Starting a long explanation, then steering toward a code example.\n")

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a technical writer who explains programming concepts.",
        model=LOCAL_MODEL,
        steering_mode="one-at-a-time",
        get_api_key=get_api_key,
    )) as agent:

        steered = False

        print("AI: ", end="", flush=True)
        char_count = 0
        async for event in agent.prompt("Explain how Python's asyncio event loop works."):
            t = event.get("type")
            if (
                t == "message_update"
                and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
            ):
                delta = event["assistantMessageEvent"]["delta"]
                print(delta, end="", flush=True)
                char_count += len(delta)

                # After 200 chars, steer toward a practical code example
                if not steered and char_count > 200:
                    steered = True
                    print("\n\n[STEER] → 'Now show a minimal code example'\n")
                    await agent.steer(
                        "Stop the theory — show a minimal 10-line code example instead."
                    )

        print()


# ─── Use Case 3: Follow-up queue ──────────────────────────────────────────────

async def demo_followup() -> None:
    print("=" * 60)
    print("USE CASE 3: Follow-up — queue messages to run after current turn")
    print("=" * 60)
    print("Queuing 3 follow-up questions before the first response finishes.\n")

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a concise assistant. Keep each answer to 1-2 sentences.",
        model=LOCAL_MODEL,
        follow_up_mode="one-at-a-time",  # processes queued items one by one
        get_api_key=get_api_key,
    )) as agent:

        # Queue follow-ups BEFORE awaiting the first response
        await agent.follow_up("What is its time complexity?")
        await agent.follow_up("Give a 3-line Python example.")
        await agent.follow_up("What is the main alternative?")

        questions = [
            "What is binary search?",
            "What is its time complexity?",
            "Give a 3-line Python example.",
            "What is the main alternative?",
        ]

        # Process all turns — pi-agent automatically processes the queued follow-ups
        turn = 0
        async for event in agent.prompt("What is binary search?"):
            t = event.get("type")
            if t == "agent_start":
                turn += 1
                print(f"\n--- Turn {turn} {'(initial)' if turn == 1 else '(follow-up)'} ---")
                print("AI: ", end="", flush=True)
            elif (
                t == "message_update"
                and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
            ):
                print(event["assistantMessageEvent"]["delta"], end="", flush=True)

        print()


# ─── Use Case 4: Pipeline — reset + model swap ────────────────────────────────

async def demo_pipeline() -> None:
    print("=" * 60)
    print("USE CASE 4: Reset + Model Swap — multi-task pipeline")
    print("=" * 60)

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a helpful assistant.",
        model=LOCAL_MODEL,
        get_api_key=get_api_key,
    )) as agent:

        # Task 1: Use a coding-focused system prompt
        await agent.set_system_prompt(
            "You are a Python expert. Give short, runnable code snippets."
        )
        print("Task 1 — Python snippet:\nAI: ", end="", flush=True)
        await collect_text(agent.prompt("Show me how to read a CSV file with pandas in 3 lines."))
        print()

        # Reset clears conversation history for a clean slate
        await agent.reset()
        print("\n[reset] conversation cleared\n")

        # Task 2: Swap to a creative writing persona
        await agent.set_system_prompt(
            "You are a creative writer. Write vivid, imaginative descriptions."
        )
        print("Task 2 — Creative writing:\nAI: ", end="", flush=True)
        await collect_text(agent.prompt("Describe a sunset over a mountain lake in 3 sentences."))
        print()

        # Reset again
        await agent.reset()
        print("\n[reset] conversation cleared\n")

        # Task 3: Back to technical
        await agent.set_system_prompt(
            "You are a concise technical assistant. Answer in bullet points."
        )
        print("Task 3 — Technical bullets:\nAI: ", end="", flush=True)
        await collect_text(agent.prompt("What are the top 5 features of Python 3.12?"))
        print()


# ─── Main ─────────────────────────────────────────────────────────────────────

DEMOS = {
    "abort":    demo_abort,
    "steer":    demo_steer,
    "followup": demo_followup,
    "pipeline": demo_pipeline,
}

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "pipeline"
    if mode not in DEMOS:
        print(f"Unknown mode '{mode}'. Choose from: {', '.join(DEMOS)}")
        sys.exit(1)
    print(f"Running demo: {mode}\n")
    asyncio.run(DEMOS[mode]())
