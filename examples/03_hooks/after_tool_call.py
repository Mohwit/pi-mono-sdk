"""
afterToolCall Hook — Feature 6

Called after every tool execution with the full result. The hook can:
  - Inspect / log the result
  - Inject extra context into the result via {"details": {...}}
  - Terminate the agent turn early via {"terminate": True}
  - Return None to pass through unchanged

Use cases shown:
  1. Execution monitoring  — track timing and error rate for every tool
  2. Output enrichment     — inject extra metadata the LLM can use
  3. Error circuit breaker — stop the agent if too many tools fail
  4. Content filtering     — detect and sanitise sensitive data in results

Run:
  python examples/after_tool_call.py

Requires Ollama:
  ollama serve && ollama pull llama3.1
"""

import asyncio
import math
import os
import re
import time
from collections import defaultdict

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


# ─── Tools ────────────────────────────────────────────────────────────────────

async def read_file(params: dict) -> dict:
    path = params["path"]
    try:
        with open(path) as f:
            text = f.read()
        return {"content": [{"type": "text", "text": text}]}
    except FileNotFoundError:
        # Intentionally not catching all exceptions so "error" path is exercised
        raise


async def list_directory(params: dict) -> dict:
    path = params.get("path", ".")
    entries = sorted(
        f"{e}{'/' if os.path.isdir(os.path.join(path, e)) else ''}"
        for e in os.listdir(path)
    )
    return {"content": [{"type": "text", "text": "\n".join(entries) or "(empty)"}]}


async def fetch_data(params: dict) -> dict:
    """Simulates a data fetch that occasionally returns sensitive-looking data."""
    key = params.get("key", "default")
    # Simulate a few different responses
    data_store = {
        "user_profile":  "Name: Alice, Role: admin, email: alice@example.com, api_key: sk-abc123XYZ",
        "system_status": "All services operational. Uptime: 99.97%. Nodes: 12 active.",
        "config":        "debug=false, log_level=info, db_host=postgres.internal",
        "missing":       None,
    }
    value = data_store.get(key)
    if value is None:
        raise KeyError(f"No data found for key '{key}'")
    return {"content": [{"type": "text", "text": value}]}


async def calculate(params: dict) -> dict:
    expression = params["expression"]
    allowed    = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    result     = eval(expression, {"__builtins__": {}}, allowed)  # noqa: S307
    return {"content": [{"type": "text", "text": f"{expression} = {result}"}]}


TOOLS = [
    ToolDefinition(
        name="read_file",
        description="Read a file from disk.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        execute=read_file,
    ),
    ToolDefinition(
        name="list_directory",
        description="List files and folders in a directory.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": [],
        },
        execute=list_directory,
    ),
    ToolDefinition(
        name="fetch_data",
        description="Fetch data from an internal store by key. Keys: user_profile, system_status, config.",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Data key to retrieve"}},
            "required": ["key"],
        },
        execute=fetch_data,
    ),
    ToolDefinition(
        name="calculate",
        description="Evaluate a math expression.",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
        execute=calculate,
    ),
]


# ─── Monitoring state ─────────────────────────────────────────────────────────

class ToolMonitor:
    def __init__(self, error_limit: int = 2) -> None:
        self.calls:        list[dict]            = []
        self.timing:       dict[str, list[float]] = defaultdict(list)
        self.error_count:  int                   = 0
        self.error_limit:  int                   = error_limit
        self._call_start:  dict[str, float]      = {}  # id → start time

    def record_start(self, tool_id: str) -> None:
        self._call_start[tool_id] = time.monotonic()

    def record_end(self, tool_id: str, tool_name: str, success: bool) -> float:
        elapsed = time.monotonic() - self._call_start.pop(tool_id, time.monotonic())
        self.timing[tool_name].append(elapsed)
        if not success:
            self.error_count += 1
        return elapsed

    def print_summary(self) -> None:
        print("\n" + "=" * 60)
        print("TOOL EXECUTION REPORT")
        print("=" * 60)
        for entry in self.calls:
            icon = "✓" if entry["success"] else "✗"
            print(f"  {icon} {entry['tool']:<20} {entry['elapsed_ms']:>6.0f} ms  {entry['note']}")
        print()
        for tool, times in self.timing.items():
            avg = sum(times) / len(times) * 1000
            print(f"  avg latency  {tool:<20} {avg:.0f} ms  ({len(times)} calls)")
        print(f"\n  Total errors: {self.error_count}")


monitor = ToolMonitor(error_limit=2)

# Patterns we never want the LLM to see in plain text
SENSITIVE_PATTERNS = [
    (re.compile(r"api[_-]?key\s*[:=]\s*\S+", re.I), "[REDACTED API KEY]"),
    (re.compile(r"sk-[A-Za-z0-9]{8,}", re.I),         "[REDACTED SK TOKEN]"),
    (re.compile(r"password\s*[:=]\s*\S+", re.I),       "[REDACTED PASSWORD]"),
]


def redact(text: str) -> tuple[str, bool]:
    """Return (redacted_text, was_changed)."""
    original = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text, text != original


# ─── afterToolCall hook ───────────────────────────────────────────────────────

# Track call starts — we need them for timing (hook receives the result, not a timer)
_pending_starts: dict[str, float] = {}


async def after_tool_call(ctx: dict) -> dict | None:
    """
    Combined afterToolCall hook:
      1. Timing & monitoring
      2. Content filtering (redact sensitive data)
      3. Output enrichment (inject extra context for the LLM)
      4. Circuit breaker (terminate on too many errors)
    """
    tool_name = ctx["name"]
    tool_id   = ctx["id"]
    result    = ctx.get("result") or {}
    is_error  = ctx.get("is_error", False)

    # ── 1. Timing ─────────────────────────────────────────────────────────────
    elapsed   = monitor.record_end(tool_id, tool_name, not is_error)
    elapsed_ms = elapsed * 1000

    # Extract text from result for inspection
    content_blocks = result.get("content", [])
    result_text    = " ".join(
        b.get("text", "") for b in content_blocks if isinstance(b, dict) and b.get("type") == "text"
    )

    # ── 2. Content filtering ───────────────────────────────────────────────────
    redacted_text, was_redacted = redact(result_text)
    if was_redacted:
        print(f"\n[FILTER] Redacted sensitive data from '{tool_name}' result")
        # Rebuild the content blocks with sanitised text
        result = {"content": [{"type": "text", "text": redacted_text}]}

    # ── 3. Output enrichment ───────────────────────────────────────────────────
    details: dict = {"elapsed_ms": round(elapsed_ms, 1)}
    if tool_name == "list_directory":
        file_count = len(result_text.splitlines())
        details["file_count"] = file_count
        details["hint"] = f"Directory contains {file_count} entries"

    if tool_name == "fetch_data":
        details["source"] = "internal-data-store"
        details["fetched_at"] = time.strftime("%H:%M:%S")

    # Log to monitor
    note = "redacted" if was_redacted else ("error" if is_error else "ok")
    monitor.calls.append({
        "tool":       tool_name,
        "elapsed_ms": elapsed_ms,
        "success":    not is_error,
        "note":       note,
    })
    print(f"\n[MONITOR] '{tool_name}' completed in {elapsed_ms:.0f} ms  [{note}]")

    # ── 4. Circuit breaker ─────────────────────────────────────────────────────
    if is_error:
        if monitor.error_count >= monitor.error_limit:
            print(f"\n[CIRCUIT BREAKER] {monitor.error_count} errors reached limit — terminating turn")
            return {"terminate": True}

    return {"details": details}


# ─── Event printer ────────────────────────────────────────────────────────────

call_times: dict[str, float] = {}


def print_event(event: dict) -> None:
    t = event.get("type")
    if (
        t == "message_update"
        and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
    ):
        print(event["assistantMessageEvent"]["delta"], end="", flush=True)
    elif t == "tool_execution_start":
        tool_id   = event.get("toolCallId", event.get("id", "?"))
        tool_name = event.get("toolName", "?")
        call_times[tool_id] = time.monotonic()
        # Also record in monitor so record_end can compute timing
        monitor.record_start(tool_id)
        print(f"\n[→] {tool_name}({event.get('params', {})})", flush=True)
    elif t == "tool_execution_end":
        print(f"[←] done", flush=True)


# ─── Main ─────────────────────────────────────────────────────────────────────

SCENARIO_PROMPT = """\
Please do the following in order and summarise what you found:
1. List the current directory
2. Fetch the 'user_profile' data key
3. Fetch the 'system_status' data key
4. Try to read the file 'nonexistent_file.txt' (it won't exist — handle gracefully)
5. Calculate: pi * 7 ** 2
"""


async def main() -> None:
    print("=" * 60)
    print("afterToolCall Hook Demo")
    print(f"Error circuit breaker trips after {monitor.error_limit} errors")
    print("Sensitive data patterns are redacted from all results")
    print("=" * 60)
    print()

    async with PiAgent(PiAgentOptions(
        system_prompt=(
            "You are a helpful assistant. Complete tasks systematically and "
            "report your findings. If a tool fails, note it and continue."
        ),
        model=LOCAL_MODEL,
        tools=TOOLS,
        after_tool_call=after_tool_call,
        get_api_key=get_api_key,
    )) as agent:
        print(f"Prompt:\n{SCENARIO_PROMPT}\n")
        print("AI: ", end="", flush=True)

        async for event in agent.prompt(SCENARIO_PROMPT):
            print_event(event)

    print("\n")
    monitor.print_summary()


if __name__ == "__main__":
    asyncio.run(main())
