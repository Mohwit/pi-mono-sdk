"""
Full-Fledged Research Agent — All v2 Features Combined

A research assistant that uses every pi-agent feature together:
  - Tools             : web search (simulated), calculator, file write, note-taking
  - beforeToolCall    : confirm destructive writes, log every call
  - afterToolCall     : enrich results with metadata, circuit-break on errors
  - transformContext  : sliding window to manage context size
  - Session persistence: save/resume research sessions between runs
  - SyncPiAgent       : all of the above via the sync API for easy scripting

Use case: A researcher asks the agent to investigate a technical topic,
the agent searches for information, takes notes, saves findings to disk,
and the session can be resumed in the next run.

Run (fresh research session):
  python examples/research_agent.py

Run (resume previous session):
  python examples/research_agent.py --resume

Requires Ollama:
  ollama serve && ollama pull llama3.1
"""

import asyncio
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from pi_agent import (
    PiAgent,
    PiAgentOptions,
    LocalModelConfig,
    ToolDefinition,
    load_session,
)

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


SESSION_FILE  = Path("examples/data/research_agent_session.json")
NOTES_DIR     = Path("examples/data/research_notes")
WINDOW_SIZE   = 6   # keep last 6 turn-pairs in context
ERROR_LIMIT   = 3   # circuit-break after 3 tool errors


# ─── Tools ────────────────────────────────────────────────────────────────────

# Simulated knowledge base (represents "web search" results)
_KNOWLEDGE_BASE = {
    "consensus algorithms": (
        "Consensus algorithms allow distributed systems to agree on a single value. "
        "Key examples: Paxos (Lamport 1989), Raft (Ongaro & Ousterhout 2014), "
        "and PBFT for Byzantine fault tolerance. Raft is widely used in etcd, "
        "CockroachDB, and TiKV."
    ),
    "cap theorem": (
        "The CAP theorem (Brewer 2000) states a distributed system can guarantee "
        "at most two of: Consistency, Availability, Partition tolerance. "
        "Modern systems choose CP (HBase, Zookeeper) or AP (Cassandra, DynamoDB)."
    ),
    "crdt": (
        "CRDTs (Conflict-free Replicated Data Types) are data structures that can "
        "be replicated across nodes and merged without coordination. Types: "
        "G-Counter, PN-Counter, OR-Set, LWW-Register. Used in Redis, Riak, Figma."
    ),
    "event sourcing": (
        "Event sourcing stores state as a sequence of immutable events rather than "
        "current values. Benefits: full audit trail, temporal queries, replay. "
        "Common with CQRS. Used in Apache Kafka, EventStoreDB."
    ),
    "vector clocks": (
        "Vector clocks track causality in distributed systems. Each node maintains "
        "a vector of counters. Used to detect concurrent vs. causally ordered events. "
        "Amazon Dynamo used vector clocks for conflict detection."
    ),
}


async def web_search(params: dict) -> dict:
    """Simulated web search against a local knowledge base."""
    query   = params.get("query", "").lower()
    results = []
    for key, text in _KNOWLEDGE_BASE.items():
        if any(word in key for word in query.split()):
            results.append(f"[{key.title()}]\n{text}")
    if not results:
        results = ["No results found for: " + params.get("query", "")]
    return {"content": [{"type": "text", "text": "\n\n".join(results)}]}


async def take_note(params: dict) -> dict:
    """Save a research note to disk."""
    title   = params.get("title", "untitled")
    content = params.get("content", "")
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    # Sanitise filename
    filename = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    path     = NOTES_DIR / f"{filename}.md"
    with open(path, "w") as f:
        f.write(f"# {title}\n\n")
        f.write(f"*Saved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write(content)
    return {"content": [{"type": "text", "text": f"Note saved to {path}"}]}


async def list_notes(params: dict) -> dict:
    """List all saved research notes."""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    notes = sorted(NOTES_DIR.glob("*.md"))
    if not notes:
        return {"content": [{"type": "text", "text": "No notes saved yet."}]}
    lines = [f"{i+1}. {n.stem}" for i, n in enumerate(notes)]
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


async def read_note(params: dict) -> dict:
    """Read a saved research note by title."""
    title    = params.get("title", "")
    filename = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    path     = NOTES_DIR / f"{filename}.md"
    try:
        return {"content": [{"type": "text", "text": path.read_text()}]}
    except FileNotFoundError:
        # Try fuzzy match
        matches = list(NOTES_DIR.glob(f"*{title[:10]}*.md"))
        if matches:
            return {"content": [{"type": "text", "text": matches[0].read_text()}]}
        return {"content": [{"type": "text", "text": f"Note not found: {title}"}]}


async def calculate(params: dict) -> dict:
    expression = params["expression"]
    try:
        allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
        result  = eval(expression, {"__builtins__": {}}, allowed)  # noqa: S307
        return {"content": [{"type": "text", "text": f"{expression} = {result}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


TOOLS = [
    ToolDefinition(
        name="web_search",
        description=(
            "Search for technical information. Available topics: "
            "consensus algorithms, CAP theorem, CRDT, event sourcing, vector clocks."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
        execute=web_search,
    ),
    ToolDefinition(
        name="take_note",
        description="Save a research note to disk with a title and content.",
        parameters={
            "type": "object",
            "properties": {
                "title":   {"type": "string", "description": "Note title"},
                "content": {"type": "string", "description": "Markdown content"},
            },
            "required": ["title", "content"],
        },
        execute=take_note,
    ),
    ToolDefinition(
        name="list_notes",
        description="List all previously saved research notes.",
        parameters={"type": "object", "properties": {}, "required": []},
        execute=list_notes,
    ),
    ToolDefinition(
        name="read_note",
        description="Read a previously saved research note by title.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title to read"},
            },
            "required": ["title"],
        },
        execute=read_note,
    ),
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


# ─── Hook: beforeToolCall ─────────────────────────────────────────────────────

audit_log: list[dict] = []


async def before_tool_call(ctx: dict) -> dict | None:
    """Log every call; confirm note-writing interactively."""
    tool_name = ctx["name"]
    params    = ctx["params"]

    audit_log.append({
        "ts":     datetime.now().isoformat(),
        "tool":   tool_name,
        "params": {k: str(v)[:80] for k, v in params.items()},
        "phase":  "before",
    })

    print(f"\n  [→] {tool_name}", end="", flush=True)

    if tool_name == "take_note":
        title = params.get("title", "?")
        print(f": saving note '{title}'", flush=True)
        # In non-interactive mode (piped stdin), auto-approve
        if sys.stdin.isatty():
            loop  = asyncio.get_running_loop()
            reply = await loop.run_in_executor(None, input, "     Allow? [Y/n] ")
            if reply.strip().lower() == "n":
                return {"block": True, "reason": "User cancelled note save"}

    return None


# ─── Hook: afterToolCall ──────────────────────────────────────────────────────

error_count = 0


async def after_tool_call(ctx: dict) -> dict | None:
    """Enrich results with metadata; circuit-break on repeated errors."""
    global error_count

    tool_name = ctx["name"]
    is_error  = ctx.get("is_error", False)
    result    = ctx.get("result") or {}

    content_text = " ".join(
        b.get("text", "")
        for b in result.get("content", [])
        if isinstance(b, dict) and b.get("type") == "text"
    )

    print(f" ← {'ERROR' if is_error else 'OK'} ({len(content_text)} chars)", flush=True)

    if is_error:
        error_count += 1
        audit_log.append({"ts": datetime.now().isoformat(), "tool": tool_name, "phase": "error"})
        if error_count >= ERROR_LIMIT:
            print(f"\n  [circuit breaker] {error_count} errors — terminating turn")
            return {"terminate": True}

    # Enrich web_search results with a timestamp
    details: dict = {}
    if tool_name == "web_search":
        details["searched_at"] = datetime.now().strftime("%H:%M:%S")
        details["result_length"] = len(content_text)

    return {"details": details} if details else None


# ─── Transform: sliding window ────────────────────────────────────────────────

def sliding_window(messages: list[dict]) -> list[dict]:
    turns: list[list[dict]] = []
    current: list[dict]     = []
    for msg in messages:
        current.append(msg)
        if msg.get("role") == "assistant":
            turns.append(current)
            current = []
    kept   = turns[-WINDOW_SIZE:]
    result = [m for t in kept for m in t] + current
    if len(messages) != len(result):
        print(f"\n  [context] window: {len(messages)} → {len(result)} messages", flush=True)
    return result


# ─── Event printer ────────────────────────────────────────────────────────────

def print_event(event: dict) -> None:
    t = event.get("type")
    if (
        t == "message_update"
        and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
    ):
        print(event["assistantMessageEvent"]["delta"], end="", flush=True)


# ─── Main ─────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a research assistant specialising in distributed systems.
Your workflow:
1. Search for information using web_search
2. Analyse and synthesise what you find
3. Save important findings as notes using take_note
4. If resuming, check existing notes first with list_notes

Keep responses concise. After researching, always save a structured note."""

INITIAL_PROMPT = """\
Research the following topics and save a note for each:
1. CAP theorem — what it is, practical implications
2. Consensus algorithms — compare Paxos vs Raft

After saving both notes, give me a brief summary of the key tradeoffs."""

RESUME_PROMPT = """\
We're continuing our distributed systems research.
First list existing notes, then research: CRDT data types and their use cases.
Save your findings as a new note."""


async def main() -> None:
    resume = "--resume" in sys.argv

    print("=" * 60)
    print("Research Agent — All v2 Features")
    print(f"Mode: {'RESUME' if resume else 'FRESH START'}")
    print(f"Context window: {WINDOW_SIZE} turns | Error limit: {ERROR_LIMIT}")
    print("=" * 60 + "\n")

    # Load previous session if resuming
    messages: list[dict] = []
    if resume and SESSION_FILE.exists():
        messages = load_session(str(SESSION_FILE))
        print(f"Loaded {len(messages)} messages from previous session.\n")

    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

    async with PiAgent(PiAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=LOCAL_MODEL,
        tools=TOOLS,
        messages=messages,
        before_tool_call=before_tool_call,
        after_tool_call=after_tool_call,
        transform_context=lambda msgs: asyncio.get_event_loop().run_in_executor(
            None, sliding_window, msgs
        ),
        get_api_key=get_api_key,
    )) as agent:

        # Inspect state at start
        state = await agent.get_state()
        print(f"Context: {len(state['messages'])} messages loaded\n")

        prompt = RESUME_PROMPT if resume else INITIAL_PROMPT
        print(f"Prompt: {prompt[:100]}…\n")
        print("─" * 60)
        print("AI: ", end="", flush=True)

        async for event in agent.prompt(prompt):
            print_event(event)

        print("\n" + "─" * 60)

        # Save session after every response
        await agent.save_session(str(SESSION_FILE))
        final_state = await agent.get_state()
        print(f"\nSession saved — {len(final_state['messages'])} messages total")
        print(f"Notes saved in: {NOTES_DIR.resolve()}")
        print("Run with --resume to continue.\n")

    # Print audit summary
    print("=" * 60)
    print("AUDIT LOG")
    print("=" * 60)
    for entry in audit_log:
        phase = entry.get("phase", "?")
        icon  = "→" if phase == "before" else "✗"
        print(f"  {icon} [{entry['ts'][11:19]}] {entry['tool']}")

    # List saved notes
    if NOTES_DIR.exists():
        notes = sorted(NOTES_DIR.glob("*.md"))
        if notes:
            print(f"\nSaved notes ({len(notes)}):")
            for note in notes:
                print(f"  - {note.stem}")


if __name__ == "__main__":
    asyncio.run(main())
