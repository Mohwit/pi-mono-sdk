"""
transformContext Callback — Feature 7

Called before every LLM request with the full message history.
Returns the (possibly modified) message list that gets sent to the model.

Use cases shown:
  1. Sliding window    — keep only the last N messages to prevent context overflow
  2. Context injection — prepend a live system status block before every request
  3. Summarisation     — collapse old messages into a running summary (simulated)
  4. PII scrubbing     — strip personal data from context before it leaves the app

Run:
  python examples/transform_context.py [sliding|inject|scrub|combined]

Requires Ollama:
  ollama serve && ollama pull llama3.1
"""

import asyncio
import re
import sys
import time
from datetime import datetime

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


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_text(messages: list[dict]) -> str:
    """Extract all text content from a message list for display."""
    parts = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(f"[{msg.get('role','?')}] {block['text'][:60]}")
        elif isinstance(content, str):
            parts.append(f"[{msg.get('role','?')}] {content[:60]}")
    return "\n".join(parts)


async def ainput(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


def print_stream(events):
    for event in events:
        t = event.get("type")
        if (
            t == "message_update"
            and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
        ):
            print(event["assistantMessageEvent"]["delta"], end="", flush=True)


# ─── Use Case 1: Sliding window ───────────────────────────────────────────────

def make_sliding_window(max_turns: int = 4):
    """
    Keep only the last `max_turns` user+assistant exchange pairs.
    Older messages are discarded so the context never grows unboundedly.
    """
    def transform(messages: list[dict]) -> list[dict]:
        # Split messages by role pairs: each "turn" = 1 user + 1 assistant
        turns = []
        current: list[dict] = []
        for msg in messages:
            current.append(msg)
            if msg.get("role") == "assistant":
                turns.append(current)
                current = []
        # Keep the last N turns + any in-progress user message
        kept_turns = turns[-max_turns:]
        result     = [msg for turn in kept_turns for msg in turn] + current

        dropped = len(messages) - len(result)
        if dropped > 0:
            print(f"\n  [context] sliding window: dropped {dropped} old messages, keeping {len(result)}")

        return result

    return transform


# ─── Use Case 2: Live context injection ───────────────────────────────────────

def make_context_injector():
    """
    Prepend a live status block as a "system" message before every LLM request.
    The block updates every call so the model always sees fresh runtime info.
    """
    def transform(messages: list[dict]) -> list[dict]:
        status_block = {
            "role": "user",
            "content": (
                f"[SYSTEM STATUS — injected at {datetime.now().strftime('%H:%M:%S')}]\n"
                f"  Server load: {__import__('random').uniform(10, 90):.0f}%\n"
                f"  Active users: {__import__('random').randint(50, 500)}\n"
                f"  Cache hit rate: {__import__('random').uniform(70, 99):.1f}%\n"
                "[END STATUS]\n\n"
                "Use the above runtime context if relevant to the user's question.\n"
            ),
        }
        print(f"\n  [context] injecting live status block before request")
        return [status_block] + list(messages)

    return transform


# ─── Use Case 3: PII scrubbing ────────────────────────────────────────────────

PII_PATTERNS = [
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[EMAIL]"),
    # Phone numbers (loose)
    (re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),                  "[PHONE]"),
    # Credit card-like (4 groups of 4 digits)
    (re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),            "[CARD]"),
    # SSN-like
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                               "[SSN]"),
    # API / secret keys
    (re.compile(r"sk-[A-Za-z0-9]{16,}", re.I),                           "[SECRET_KEY]"),
    (re.compile(r"api[_-]?key\s*[:=]\s*[A-Za-z0-9_\-]{8,}", re.I),      "[API_KEY]"),
]


def scrub_pii(text: str) -> tuple[str, int]:
    count = 0
    for pattern, placeholder in PII_PATTERNS:
        new_text, n = pattern.subn(placeholder, text)
        text   = new_text
        count += n
    return text, count


def make_pii_scrubber():
    """Strip PII from all messages before they are sent to the model."""
    def transform(messages: list[dict]) -> list[dict]:
        cleaned = []
        total_scrubbed = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                new_content, n = scrub_pii(content)
                total_scrubbed += n
                cleaned.append({**msg, "content": new_content})
            elif isinstance(content, list):
                new_blocks = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        new_text, n = scrub_pii(block["text"])
                        total_scrubbed += n
                        new_blocks.append({**block, "text": new_text})
                    else:
                        new_blocks.append(block)
                cleaned.append({**msg, "content": new_blocks})
            else:
                cleaned.append(msg)
        if total_scrubbed:
            print(f"\n  [context] PII scrubber replaced {total_scrubbed} pattern(s)")
        return cleaned

    return transform


# ─── Use Case 4: Combined pipeline ────────────────────────────────────────────

def make_combined_transform(max_turns: int = 3):
    """Chain: scrub PII → slide window → inject status."""
    scrub  = make_pii_scrubber()
    slide  = make_sliding_window(max_turns)
    inject = make_context_injector()

    async def transform(messages: list[dict]) -> list[dict]:
        messages = scrub(messages)
        messages = slide(messages)
        messages = inject(messages)
        return messages

    return transform


# ─── Demo runners ─────────────────────────────────────────────────────────────

async def demo_sliding_window() -> None:
    print("=" * 60)
    print("USE CASE 1: Sliding Window (max 3 turn pairs)")
    print("=" * 60)
    print("Have a multi-turn conversation — context is trimmed after 3 exchanges.\n")

    transform = make_sliding_window(max_turns=3)

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a helpful assistant. Keep replies brief.",
        model=LOCAL_MODEL,
        transform_context=lambda msgs: asyncio.get_event_loop().run_in_executor(
            None, transform, msgs
        ),
        get_api_key=get_api_key,
    )) as agent:
        turns = [
            "What is Python?",
            "What is its biggest strength?",
            "What about its weaknesses?",
            "What version is current?",
            "What is GIL?",
        ]
        for turn in turns:
            print(f"You: {turn}")
            print("AI: ", end="", flush=True)
            async for event in agent.prompt(turn):
                if (
                    event.get("type") == "message_update"
                    and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
                ):
                    print(event["assistantMessageEvent"]["delta"], end="", flush=True)
            print("\n")


async def demo_context_injection() -> None:
    print("=" * 60)
    print("USE CASE 2: Live Context Injection")
    print("=" * 60)
    print("A live status block is injected before every LLM request.\n")

    transform = make_context_injector()

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a system monitoring assistant. Use injected status data when answering.",
        model=LOCAL_MODEL,
        transform_context=lambda msgs: asyncio.get_event_loop().run_in_executor(
            None, transform, msgs
        ),
        get_api_key=get_api_key,
    )) as agent:
        questions = [
            "How is the server performing right now?",
            "Should I be worried about the current load?",
        ]
        for q in questions:
            print(f"You: {q}")
            print("AI: ", end="", flush=True)
            async for event in agent.prompt(q):
                if (
                    event.get("type") == "message_update"
                    and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
                ):
                    print(event["assistantMessageEvent"]["delta"], end="", flush=True)
            print("\n")


async def demo_pii_scrubbing() -> None:
    print("=" * 60)
    print("USE CASE 3: PII Scrubbing")
    print("=" * 60)
    print("All PII is stripped before the message is sent to the model.\n")

    transform = make_pii_scrubber()

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a data analysis assistant.",
        model=LOCAL_MODEL,
        transform_context=lambda msgs: asyncio.get_event_loop().run_in_executor(
            None, transform, msgs
        ),
        get_api_key=get_api_key,
    )) as agent:
        prompts = [
            (
                "I need help analysing this customer record: "
                "Name: John Doe, Email: john.doe@example.com, "
                "Phone: 415-555-0123, Card: 4111-1111-1111-1111. "
                "What patterns do you notice (ignore the actual values)?"
            ),
            (
                "Also check this API key: sk-prod1234567890abcdef — is this format secure?"
            ),
        ]
        for prompt in prompts:
            print(f"You: {prompt[:80]}…")
            print("AI: ", end="", flush=True)
            async for event in agent.prompt(prompt):
                if (
                    event.get("type") == "message_update"
                    and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
                ):
                    print(event["assistantMessageEvent"]["delta"], end="", flush=True)
            print("\n")


async def demo_combined() -> None:
    print("=" * 60)
    print("USE CASE 4: Combined Pipeline (scrub → slide → inject)")
    print("=" * 60)
    print("All three transforms chained in sequence.\n")

    transform = make_combined_transform(max_turns=2)

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a system assistant with access to runtime metrics.",
        model=LOCAL_MODEL,
        transform_context=transform,
        get_api_key=get_api_key,
    )) as agent:
        turns = [
            "My email is admin@corp.com — what can you tell me about the system?",
            "Is cache performance good?",
            "What about server load?",
            "Should I scale up?",
        ]
        for turn in turns:
            print(f"You: {turn}")
            print("AI: ", end="", flush=True)
            async for event in agent.prompt(turn):
                if (
                    event.get("type") == "message_update"
                    and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
                ):
                    print(event["assistantMessageEvent"]["delta"], end="", flush=True)
            print("\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

DEMOS = {
    "sliding":  demo_sliding_window,
    "inject":   demo_context_injection,
    "scrub":    demo_pii_scrubbing,
    "combined": demo_combined,
}

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "combined"
    if mode not in DEMOS:
        print(f"Unknown mode '{mode}'. Choose from: {', '.join(DEMOS)}")
        sys.exit(1)
    asyncio.run(DEMOS[mode]())
