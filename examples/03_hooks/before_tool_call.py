"""
beforeToolCall Hook — Feature 5

Called before every tool execution. The hook can:
  - ALLOW the call (return None or {"block": False})
  - BLOCK the call (return {"block": True, "reason": "..."})

Use cases shown:
  1. Security gate    — block reads of sensitive files (.env, secrets)
  2. Confirmation     — interactively ask the user before destructive writes
  3. Audit log        — record every tool invocation before it runs
  4. Rate limiting    — refuse calls once a per-session budget is exceeded

Run:
  python examples/before_tool_call.py

Requires Ollama:
  ollama serve && ollama pull llama3.1
"""

import asyncio
import os
import math
import re
from datetime import datetime
from pathlib import Path

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


# ─── Tools the agent can use ──────────────────────────────────────────────────

async def read_file(params: dict) -> dict:
    path = params["path"]
    try:
        with open(path) as f:
            return {"content": [{"type": "text", "text": f.read()}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


async def write_file(params: dict) -> dict:
    path   = params["path"]
    content = params["content"]
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return {"content": [{"type": "text", "text": f"Written {len(content)} chars to {path}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


async def list_directory(params: dict) -> dict:
    path = params.get("path", ".")
    try:
        entries = sorted(
            f"{e}{'/' if os.path.isdir(os.path.join(path, e)) else ''}"
            for e in os.listdir(path)
        )
        return {"content": [{"type": "text", "text": "\n".join(entries) or "(empty)"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


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
        name="read_file",
        description="Read a file from disk.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File path to read"}},
            "required": ["path"],
        },
        execute=read_file,
    ),
    ToolDefinition(
        name="write_file",
        description="Write content to a file on disk.",
        parameters={
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Destination file path"},
                "content": {"type": "string", "description": "Text to write"},
            },
            "required": ["path", "content"],
        },
        execute=write_file,
    ),
    ToolDefinition(
        name="list_directory",
        description="List files and folders in a directory.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory path (default: .)"}},
            "required": [],
        },
        execute=list_directory,
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


# ─── Use Case 1: Security gate ────────────────────────────────────────────────

SENSITIVE_PATTERNS = [
    re.compile(r"\.env"),
    re.compile(r"\.pem$"),
    re.compile(r"id_rsa"),
    re.compile(r"secrets?\.(json|yaml|yml|toml)$", re.I),
    re.compile(r"credentials?\.(json|yaml|yml)$", re.I),
    re.compile(r"\.netrc$"),
    re.compile(r"\.aws/credentials"),
]


def is_sensitive_path(path: str) -> bool:
    return any(p.search(path) for p in SENSITIVE_PATTERNS)


# ─── Use Case 2: Interactive confirmation ─────────────────────────────────────

DESTRUCTIVE_TOOLS = {"write_file"}


async def ask_user_confirmation(tool_name: str, params: dict) -> bool:
    """Ask the user on the terminal whether to allow a destructive action."""
    print(f"\n[CONFIRM] Agent wants to run '{tool_name}' with:")
    for k, v in params.items():
        preview = str(v)[:120].replace("\n", "\\n")
        print(f"    {k}: {preview}")
    try:
        loop  = asyncio.get_running_loop()
        reply = await loop.run_in_executor(None, input, "Allow? [y/N] ")
        return reply.strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ─── Use Case 3 & 4: Audit log + rate limiter ────────────────────────────────

audit_log: list[dict]  = []
tool_call_count: int   = 0
TOOL_BUDGET: int       = 6


async def before_tool_call(ctx: dict) -> dict | None:
    """
    Combined beforeToolCall hook that enforces:
      1. Security gate: block sensitive file reads
      2. Confirmation: ask user before write operations
      3. Audit log: record every call attempt
      4. Rate limit: block after TOOL_BUDGET calls
    """
    global tool_call_count

    tool_name = ctx["name"]
    params    = ctx["params"]

    # ── 4. Rate limit check (first so we don't log blocked calls) ──────────────
    tool_call_count += 1
    if tool_call_count > TOOL_BUDGET:
        reason = f"Tool budget exhausted ({TOOL_BUDGET} calls used)"
        print(f"\n[RATE LIMIT] Blocked '{tool_name}' — {reason}")
        audit_log.append({
            "ts": datetime.now().isoformat(),
            "tool": tool_name,
            "params": params,
            "decision": "blocked:rate_limit",
        })
        return {"block": True, "reason": reason}

    # ── 1. Security gate ───────────────────────────────────────────────────────
    if tool_name == "read_file":
        path = params.get("path", "")
        if is_sensitive_path(path):
            reason = f"Access denied: '{path}' matches sensitive file pattern"
            print(f"\n[SECURITY] Blocked read of '{path}'")
            audit_log.append({
                "ts": datetime.now().isoformat(),
                "tool": tool_name,
                "params": params,
                "decision": "blocked:security",
                "reason": reason,
            })
            return {"block": True, "reason": reason}

    # ── 2. Confirmation for destructive tools ──────────────────────────────────
    if tool_name in DESTRUCTIVE_TOOLS:
        allowed = await ask_user_confirmation(tool_name, params)
        if not allowed:
            reason = "User denied the operation"
            audit_log.append({
                "ts": datetime.now().isoformat(),
                "tool": tool_name,
                "params": params,
                "decision": "blocked:user_denied",
            })
            return {"block": True, "reason": reason}

    # ── 3. Audit log — allowed ─────────────────────────────────────────────────
    audit_log.append({
        "ts": datetime.now().isoformat(),
        "tool": tool_name,
        "params": params,
        "decision": "allowed",
    })
    print(f"\n[AUDIT] Allowing '{tool_name}' (call #{tool_call_count}/{TOOL_BUDGET})")
    return None  # allow


# ─── Print event stream ───────────────────────────────────────────────────────

def print_event(event: dict) -> None:
    t = event.get("type")
    if (
        t == "message_update"
        and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
    ):
        print(event["assistantMessageEvent"]["delta"], end="", flush=True)
    elif t == "tool_execution_start":
        print(f"\n[tool→] {event.get('toolName')}({event.get('params', {})})", flush=True)
    elif t == "tool_execution_end":
        print(f"[tool←] done", flush=True)


# ─── Main ─────────────────────────────────────────────────────────────────────

SCENARIO_PROMPT = """\
Please help me with these tasks in order:
1. List the files in the current directory
2. Read the file pyproject.toml
3. Try to read the file .env (this should be blocked)
4. Write a short summary file to /tmp/pi_agent_demo.txt saying "Demo complete at <current time>"
5. Calculate: sqrt(2) + log(100)
"""


async def main() -> None:
    print("=" * 60)
    print("beforeToolCall Hook Demo")
    print(f"Tool budget: {TOOL_BUDGET} calls")
    print("Sensitive paths: .env, *.pem, id_rsa, secrets.*, credentials.*")
    print("Destructive tools require confirmation: write_file")
    print("=" * 60)
    print()

    async with PiAgent(PiAgentOptions(
        system_prompt=(
            "You are a helpful assistant with file system access. "
            "Complete tasks methodically and report what you did."
        ),
        model=LOCAL_MODEL,
        tools=TOOLS,
        before_tool_call=before_tool_call,
        get_api_key=get_api_key,
    )) as agent:
        print(f"Prompt:\n{SCENARIO_PROMPT}\n")
        print("AI: ", end="", flush=True)

        async for event in agent.prompt(SCENARIO_PROMPT):
            print_event(event)

    print("\n")
    print("=" * 60)
    print("AUDIT LOG")
    print("=" * 60)
    for entry in audit_log:
        decision = entry["decision"]
        icon     = "✓" if decision == "allowed" else "✗"
        reason   = f" — {entry.get('reason', '')}" if "reason" in entry else ""
        print(f"  {icon} [{entry['ts'][11:19]}] {entry['tool']:<20} {decision}{reason}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
