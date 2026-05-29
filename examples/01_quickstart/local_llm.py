"""
Interactive chat with a local LLM via Ollama, with tool calling.

Requires Ollama running locally:
  brew install ollama
  ollama serve
  ollama pull llama3.1

Run:
  python examples/local_llm.py

Try asking:
  "What time is it?"
  "What is 1234 * 5678?"
  "List the files in the current directory"
  "Read the file pyproject.toml"
"""

import asyncio
import math
import os
from datetime import datetime

from pi_agent import PiAgent, PiAgentOptions, LocalModelConfig, ToolDefinition

LOCAL_MODEL = LocalModelConfig(
    id="llama3.1",
    name="Llama 3.1",
    api="openai-completions",
    provider="local",
    base_url="http://localhost:11434/v1/",
)


# ─── Tools ────────────────────────────────────────────────────────────────────

async def get_current_time(params: dict) -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {"content": [{"type": "text", "text": f"Current time: {now}"}]}


async def calculate(params: dict) -> dict:
    expression = params["expression"]
    try:
        # NOTE: eval() with __builtins__={} is NOT a full CPython sandbox — class-hierarchy
        # escapes can bypass it.  Fine for a local demo; use `simpleeval` in production.
        allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
        result = eval(expression, {"__builtins__": {}}, allowed)  # noqa: S307
        return {"content": [{"type": "text", "text": f"{expression} = {result}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


async def list_directory(params: dict) -> dict:
    path = params.get("path", ".")
    try:
        entries = sorted(os.listdir(path))
        text = "\n".join(entries) if entries else "(empty)"
        return {"content": [{"type": "text", "text": text}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


async def read_file(params: dict) -> dict:
    path = params["path"]
    try:
        with open(path) as f:
            contents = f.read()
        return {"content": [{"type": "text", "text": contents}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


TOOLS = [
    ToolDefinition(
        name="get_current_time",
        description="Returns the current date and time.",
        parameters={"type": "object", "properties": {}, "required": []},
        execute=get_current_time,
    ),
    ToolDefinition(
        name="calculate",
        description="Evaluates a mathematical expression. Supports standard operators and math functions (sin, cos, sqrt, log, etc.).",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression to evaluate, e.g. '2 ** 10' or 'sqrt(144)'"},
            },
            "required": ["expression"],
        },
        execute=calculate,
    ),
    ToolDefinition(
        name="list_directory",
        description="Lists files and folders in a directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: current directory)"},
            },
            "required": [],
        },
        execute=list_directory,
    ),
    ToolDefinition(
        name="read_file",
        description="Reads the contents of a text file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file"},
            },
            "required": ["path"],
        },
        execute=read_file,
    ),
]


# ─── Chat loop ────────────────────────────────────────────────────────────────

async def get_api_key(provider: str) -> str:
    return "ollama"


async def ainput(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


async def main() -> None:
    async with PiAgent(PiAgentOptions(
        system_prompt=(
            "You are a helpful assistant with access to tools. "
            "Use them whenever they help answer the user's question."
        ),
        model=LOCAL_MODEL,
        tools=TOOLS,
        get_api_key=get_api_key,
    )) as agent:
        print("Chat with Llama 3.1 + tools (type 'exit' to quit)")
        print("Tools: get_current_time, calculate, list_directory, read_file\n")

        while True:
            try:
                user_input = await ainput("You: ")
            except (EOFError, KeyboardInterrupt):
                break

            if user_input.strip().lower() == "exit":
                break

            print("AI: ", end="", flush=True)
            try:
                async for event in agent.prompt(user_input):
                    t = event.get("type")
                    if (
                        t == "message_update"
                        and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
                    ):
                        print(event["assistantMessageEvent"]["delta"], end="", flush=True)
                    elif t == "tool_execution_start":
                        print(f"\n[tool] {event.get('toolName', '?')}({event.get('params', {})})", flush=True)
                    elif t == "tool_execution_end":
                        print("[tool done]", flush=True)
            except Exception as e:
                print(f"\n[error] {e}", flush=True)
            print()


asyncio.run(main())
