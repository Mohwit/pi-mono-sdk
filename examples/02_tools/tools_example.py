"""
Tool Calling — Core v1 Feature

Demonstrates how to define Python tools and let the agent use them:
  - ToolDefinition with name, description, JSON Schema parameters, execute callback
  - execute receives params dict, returns {"content": [{"type": "text", "text": "..."}]}
  - ToolResult dataclass (alternative return type with details/terminate)
  - parallel vs sequential execution mode
  - error handling inside tools (agent sees the error message and continues)
  - streaming events: tool_execution_start, tool_execution_end, message_update

Run:
  python examples/tools_example.py

Requires Ollama:
  ollama serve && ollama pull llama3.1
"""

import asyncio
import math
import os
from datetime import datetime

from pi_agent import PiAgent, PiAgentOptions, LocalModelConfig, ToolDefinition, ToolResult

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


# ─── Tool implementations ─────────────────────────────────────────────────────

async def get_current_time(params: dict) -> dict:
    """Simple tool: no params needed."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {"content": [{"type": "text", "text": f"Current time: {now}"}]}


async def calculate(params: dict) -> dict:
    """Math evaluator with a safe subset of builtins."""
    expression = params["expression"]
    try:
        allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
        result  = eval(expression, {"__builtins__": {}}, allowed)  # noqa: S307
        return {"content": [{"type": "text", "text": f"{expression} = {result}"}]}
    except Exception as e:
        # Returning an error text is the correct pattern — do NOT raise here.
        # The agent will read the error message and can decide what to do next.
        return {"content": [{"type": "text", "text": f"Calculation error: {e}"}]}


async def list_directory(params: dict) -> dict:
    """Tool with optional parameter (uses .get with default)."""
    path = params.get("path", ".")
    try:
        entries = sorted(
            f"{e}{'/' if os.path.isdir(os.path.join(path, e)) else ''}"
            for e in os.listdir(path)
        )
        text = "\n".join(entries) if entries else "(empty directory)"
        return {"content": [{"type": "text", "text": text}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


async def read_file(params: dict) -> dict:
    """Tool that returns rich metadata using ToolResult."""
    path = params["path"]
    try:
        with open(path) as f:
            content = f.read()
        # ToolResult lets you attach structured details alongside the text
        return ToolResult(
            content=[{"type": "text", "text": content}],
            details={
                "path":       path,
                "size_bytes": len(content.encode()),
                "lines":      content.count("\n") + 1,
                "read_at":    datetime.now().isoformat(),
            },
        )
    except FileNotFoundError:
        return {"content": [{"type": "text", "text": f"File not found: {path}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error reading {path}: {e}"}]}


async def write_file(params: dict) -> dict:
    """Demonstrates a tool that has side effects."""
    path    = params["path"]
    content = params["content"]
    try:
        with open(path, "w") as f:
            f.write(content)
        return {"content": [{"type": "text", "text": f"Wrote {len(content)} characters to {path}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error writing {path}: {e}"}]}


# ─── Tool definitions ─────────────────────────────────────────────────────────

TOOLS = [
    ToolDefinition(
        name="get_current_time",
        description="Returns the current date and time.",
        parameters={"type": "object", "properties": {}, "required": []},
        execute=get_current_time,
    ),
    ToolDefinition(
        name="calculate",
        description=(
            "Evaluate a mathematical expression. "
            "Supports: +, -, *, /, **, sqrt, log, sin, cos, tan, pi, e, etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Python-style math expression, e.g. 'sqrt(144)' or '2 ** 10'",
                },
            },
            "required": ["expression"],
        },
        execute=calculate,
    ),
    ToolDefinition(
        name="list_directory",
        description="List files and folders in a directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list (defaults to current directory)",
                },
            },
            "required": [],
        },
        execute=list_directory,
    ),
    ToolDefinition(
        name="read_file",
        description="Read the full contents of a text file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to read"},
            },
            "required": ["path"],
        },
        execute=read_file,
    ),
    ToolDefinition(
        name="write_file",
        description="Write text content to a file, creating it if it does not exist.",
        parameters={
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Destination file path"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["path", "content"],
        },
        execute=write_file,
        # sequential: this tool must finish before the next one starts
        execution_mode="sequential",
    ),
]


# ─── Event printer ────────────────────────────────────────────────────────────

def print_event(event: dict) -> None:
    t = event.get("type")
    if (
        t == "message_update"
        and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
    ):
        print(event["assistantMessageEvent"]["delta"], end="", flush=True)
    elif t == "tool_execution_start":
        tool = event.get("toolName", "?")
        params = event.get("params", {})
        print(f"\n  [→] {tool}({params})", flush=True)
    elif t == "tool_execution_end":
        print(f"  [←] done", flush=True)


# ─── Main ─────────────────────────────────────────────────────────────────────

DEMO_PROMPT = """\
Please complete these tasks and report the results:
1. What time is it right now?
2. Calculate: (sin(pi/4) ** 2) + (cos(pi/4) ** 2)  — this should equal 1
3. List the files in the current directory
4. Read the file pyproject.toml
5. Write a file /tmp/pi_agent_tools_demo.txt with the content: "Tools demo ran at <current time>"
"""


async def main() -> None:
    print("Tool Calling Demo")
    print("=" * 60)
    print("Tools: get_current_time, calculate, list_directory, read_file, write_file")
    print("=" * 60 + "\n")

    async with PiAgent(PiAgentOptions(
        system_prompt=(
            "You are a helpful assistant with file system and math capabilities. "
            "Complete tasks systematically and report each result clearly."
        ),
        model=LOCAL_MODEL,
        tools=TOOLS,
        get_api_key=get_api_key,
    )) as agent:
        print(f"Prompt: {DEMO_PROMPT.strip()}\n")
        print("─" * 60)
        print("AI: ", end="", flush=True)

        async for event in agent.prompt(DEMO_PROMPT):
            print_event(event)

    print()


if __name__ == "__main__":
    asyncio.run(main())
