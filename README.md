# pi-agent

**Python SDK for building AI agents powered by pi-agent-core.**

Connect any LLM (Anthropic Claude, OpenAI GPT, local Ollama models) to Python tools, with a clean async API, streaming events, lifecycle hooks, and session persistence.

---

## Installation

### From the pre-built wheel (recommended for testers)

A ready-to-install wheel is provided in the `package/` directory:

```bash
pip install pi_agent-0.1.0-v1-py3-none-any.whl
```

If you received the file directly:

```bash
pip install /path/to/pi_agent-0.1.0-v1-py3-none-any.whl
```

### From PyPI (when published)

```bash
pip install pi-agent
```

**Requirements**
- Python ≥ 3.10
- Node.js ≥ 18 **or** a pre-built `pi-agent-bridge` binary (bundled in the wheel for macOS ARM64/x64, Linux x64/ARM64, Windows x64/ARM64)

---

## Verify the installation

After installing, run this one-liner to confirm the package loads and the bridge binary is found:

```bash
python -c "import pi_agent; print('pi-agent OK — version', pi_agent.__version__ if hasattr(pi_agent, '__version__') else '0.1.0')"
```

Expected output:
```
pi-agent OK — version 0.1.0
```

### Full smoke test (requires Ollama)

```bash
# 1. Start Ollama and pull a model
ollama serve &
ollama pull llama3.1

# 2. Run the quick-start smoke test
python - <<'EOF'
import asyncio
from pi_agent import PiAgent, PiAgentOptions, LocalModelConfig

model = LocalModelConfig(
    id="llama3.1", name="Llama 3.1",
    api="openai-completions", provider="local",
    base_url="http://localhost:11434/v1/",
)

async def get_api_key(provider: str) -> str:
    return "ollama"

async def main():
    async with PiAgent(PiAgentOptions(
        system_prompt="You are a helpful assistant. Reply in one sentence.",
        model=model,
        get_api_key=get_api_key,
    )) as agent:
        print("Sending prompt …")
        async for event in agent.prompt("Say hello and confirm you are working."):
            ae = event.get("assistantMessageEvent", {})
            if ae.get("type") == "text_delta":
                print(ae["delta"], end="", flush=True)
        print("\n✓ pi-agent is working correctly.")

asyncio.run(main())
EOF
```

### Smoke test with Anthropic Claude

```bash
export ANTHROPIC_API_KEY=sk-ant-...

python - <<'EOF'
import asyncio, os
from pi_agent import PiAgent, PiAgentOptions, ModelConfig

async def get_api_key(provider: str) -> str:
    return os.environ.get(f"{provider.upper()}_API_KEY", "")

async def main():
    async with PiAgent(PiAgentOptions(
        system_prompt="You are a helpful assistant. Reply in one sentence.",
        model=ModelConfig(provider="anthropic", name="claude-haiku-4-5-20251001"),
        get_api_key=get_api_key,
    )) as agent:
        async for event in agent.prompt("Say hello and confirm you are working."):
            ae = event.get("assistantMessageEvent", {})
            if ae.get("type") == "text_delta":
                print(ae["delta"], end="", flush=True)
        print("\n✓ pi-agent + Anthropic is working correctly.")

asyncio.run(main())
EOF
```

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `PiConnectionError: No pi-agent-bridge found` | Install Node.js ≥ 18 (`brew install node` / `apt install nodejs`) or use the correct platform wheel |
| `ModuleNotFoundError: No module named 'pi_agent'` | Re-run `pip install pi_agent-0.1.0-v1-py3-none-any.whl` |
| Blank / empty AI responses with Ollama | Ensure `get_api_key` callback is provided and returns a non-empty string |
| `ANTHROPIC_API_KEY` not found | Export the environment variable before running |

---

## Quick start

```python
import asyncio
from pi_agent import PiAgent, PiAgentOptions, LocalModelConfig

# Local Ollama model (ollama serve && ollama pull llama3.1)
model = LocalModelConfig(
    id="llama3.1", name="Llama 3.1",
    api="openai-completions", provider="local",
    base_url="http://localhost:11434/v1/",
)

async def get_api_key(provider: str) -> str:
    return "ollama"   # Ollama accepts any non-empty string

async def main():
    async with PiAgent(PiAgentOptions(
        system_prompt="You are a helpful assistant.",
        model=model,
        get_api_key=get_api_key,
    )) as agent:
        async for event in agent.prompt("What is the capital of France?"):
            ae = event.get("assistantMessageEvent", {})
            if ae.get("type") == "text_delta":
                print(ae["delta"], end="", flush=True)
        print()

asyncio.run(main())
```

### Using a cloud model

```python
import os
from pi_agent import ModelConfig

model = ModelConfig(provider="anthropic", name="claude-sonnet-4-6")

async def get_api_key(provider: str) -> str:
    return os.environ[f"{provider.upper()}_API_KEY"]
```

Supported providers: `anthropic`, `openai`, `google`, `mistral`, and any OpenAI-compatible endpoint via `LocalModelConfig`.

---

## Core concepts

### 1 · Streaming events

`agent.prompt()` is an async generator that yields typed events:

```python
async for event in agent.prompt("Hello"):
    t = event.get("type")

    if t == "message_update":
        ae = event.get("assistantMessageEvent", {})
        if ae.get("type") == "text_delta":
            print(ae["delta"], end="", flush=True)   # streaming text
        elif ae.get("type") == "thinking_delta":
            pass   # extended thinking (supported models)

    elif t == "tool_execution_start":
        print(f"\n[tool] {event['toolName']}({event.get('params')})")

    elif t == "tool_execution_end":
        print(f"  → result: {event.get('result')}")

    elif t == "turn_end":
        print(f"\n[stop: {event.get('stopReason')}]")
```

### 2 · Tool calling

Register Python functions as tools the model can invoke:

```python
from pi_agent import ToolDefinition

async def get_weather(params: dict) -> dict:
    city = params["city"]
    # fetch weather ...
    return {"content": [{"type": "text", "text": f"22 °C in {city}"}]}

tool = ToolDefinition(
    name="get_weather",
    description="Get current temperature for a city.",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"},
        },
        "required": ["city"],
    },
    execute=get_weather,
    execution_mode="parallel",   # "parallel" (default) | "sequential"
)

async with PiAgent(PiAgentOptions(
    system_prompt="You are a weather assistant.",
    model=model,
    tools=[tool],
    get_api_key=get_api_key,
)) as agent:
    ...
```

### 3 · Multi-turn conversations

The agent maintains conversation history automatically across multiple `prompt()` calls within the same context manager:

```python
async with PiAgent(PiAgentOptions(...)) as agent:
    async for event in agent.prompt("My name is Alice."):
        ...
    async for event in agent.prompt("What is my name?"):
        ...   # agent remembers "Alice"
```

### 4 · Session persistence

Save and restore conversation history across Python sessions:

```python
from pi_agent import load_session

# Save
await agent.save_session("session.json")

# Restore in the next run
messages = load_session("session.json")
async with PiAgent(PiAgentOptions(..., messages=messages)) as agent:
    ...   # full history is restored
```

Inspect state at any time:

```python
state = await agent.get_state()
print(state["messages"])       # full message list
print(state["model"])          # current model config
print(state["systemPrompt"])   # current system prompt
```

### 5 · Agent control

```python
# Inject an instruction the model sees mid-turn
await agent.steer("Be more concise.")

# Queue a message to be sent after the current turn finishes
await agent.follow_up("Now summarise that as a bullet list.")

# Cancel a running response
await agent.abort()

# Clear conversation history (keeps tools + system prompt)
await agent.reset()

# Hot-swap model or system prompt
await agent.set_model(ModelConfig(provider="anthropic", name="claude-opus-4-6"))
await agent.set_system_prompt("You are a code reviewer.")

# Extended thinking (Claude models only)
await agent.set_thinking_level("high")   # "off" | "low" | "medium" | "high"
```

### 6 · Lifecycle hooks

#### beforeToolCall

Called before every tool execution. Return `{"block": True, "reason": "..."}` to prevent it.

```python
async def before_tool_call(ctx: dict) -> dict | None:
    # ctx = {"id": str, "name": str, "params": dict}
    if ctx["name"] == "delete_file":
        answer = input(f"Delete {ctx['params']['path']}? [y/N] ")
        if answer.lower() != "y":
            return {"block": True, "reason": "User declined."}
    return None   # allow
```

#### afterToolCall

Called after every tool execution. Return `{"terminate": True}` to abort the turn.

```python
error_count = 0

async def after_tool_call(ctx: dict) -> dict | None:
    # ctx = {"id": str, "name": str, "params": dict, "result": dict, "is_error": bool}
    global error_count
    if ctx.get("is_error"):
        error_count += 1
        if error_count >= 3:
            return {"terminate": True}   # circuit breaker
    return {"details": {"logged_at": "..."}}   # optional: enrich the result
```

#### transformContext

Called before every LLM request. Returns the modified message list.

```python
async def transform_context(messages: list[dict]) -> list[dict]:
    # Sliding window: keep only the last 12 messages
    return messages[-12:]
```

Pass all three to `PiAgentOptions`:

```python
PiAgentOptions(
    ...,
    before_tool_call=before_tool_call,
    after_tool_call=after_tool_call,
    transform_context=transform_context,
)
```

### 7 · Synchronous API

For scripts and notebooks where asyncio is inconvenient:

```python
from pi_agent import SyncPiAgent, PiAgentOptions

with SyncPiAgent(PiAgentOptions(...)) as agent:
    for event in agent.prompt("Summarise this text: ..."):
        ae = event.get("assistantMessageEvent", {})
        if ae.get("type") == "text_delta":
            print(ae["delta"], end="", flush=True)
```

---

## API reference

### `PiAgentOptions`

| Field | Type | Description |
|-------|------|-------------|
| `system_prompt` | `str` | Initial system prompt |
| `model` | `ModelConfig \| LocalModelConfig` | Model to use |
| `tools` | `list[ToolDefinition]` | Tools available to the model |
| `messages` | `list[dict]` | Pre-loaded conversation history |
| `get_api_key` | `async (provider: str) -> str` | **Required.** Returns the API key for a given provider |
| `before_tool_call` | `async (ctx) -> dict \| None` | Hook called before each tool execution |
| `after_tool_call` | `async (ctx) -> dict \| None` | Hook called after each tool execution |
| `transform_context` | `async (messages) -> messages` | Rewrite history before each LLM call |

### `ModelConfig`

```python
ModelConfig(provider="anthropic", name="claude-sonnet-4-6")
ModelConfig(provider="openai",    name="gpt-4o")
```

### `LocalModelConfig`

```python
LocalModelConfig(
    id="llama3.1",
    name="Llama 3.1",
    api="openai-completions",
    provider="local",
    base_url="http://localhost:11434/v1/",
)
```

### `ToolDefinition`

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Tool identifier (snake_case) |
| `description` | `str` | Shown to the model to decide when to call it |
| `parameters` | `dict` | JSON Schema object describing the parameters |
| `execute` | `async (params: dict) -> dict` | Implementation |
| `execution_mode` | `"parallel" \| "sequential"` | Default: `"parallel"` |

### `PiAgent` methods

| Method | Description |
|--------|-------------|
| `prompt(text)` | Send a message; returns async generator of events |
| `steer(text)` | Inject instruction mid-turn |
| `follow_up(text)` | Queue message for after current turn |
| `abort()` | Cancel current response |
| `reset()` | Clear conversation history |
| `get_state()` | Returns `{"messages", "model", "systemPrompt", "thinkingLevel"}` |
| `save_session(path)` | Serialise history to JSON |
| `set_model(model)` | Hot-swap model |
| `set_system_prompt(text)` | Replace system prompt |
| `set_thinking_level(level)` | `"off" \| "low" \| "medium" \| "high"` |
| `clear_steering_queue()` | Discard queued steers |
| `clear_follow_up_queue()` | Discard queued follow-ups |
| `clear_all_queues()` | Discard all queued messages |

### `load_session(path) -> list[dict]`

Deserialise a session file saved with `agent.save_session()`.

---

## Examples

The `examples/` directory contains 9 groups of runnable examples:

```
examples/
├── 01_quickstart/   simple_chat, local_llm, get_api_key
├── 02_tools/        tools_example
├── 03_hooks/        before_tool_call, after_tool_call
├── 04_streaming/    streaming_events (full event reference)
├── 05_control/      steering_and_followup
├── 06_context/      session_persistence, transform_context
├── 07_sync/         sync_api
├── 08_agents/       research_agent (all features combined)
└── 09_cli_agent/    full-screen TUI coding agent
```

See [`examples/README.md`](examples/README.md) for the recommended reading order and run commands.

### CLI Coding Agent

A Claude Code-style interactive TUI is included:

```bash
# Local Ollama
python examples/09_cli_agent/

# Anthropic Claude
ANTHROPIC_API_KEY=sk-... python examples/09_cli_agent/ --model anthropic/claude-sonnet-4-6
```

Features: 10 file/shell tools · slash-command autocomplete · Esc-to-abort · follow-up queue · auto-save.

---

## Architecture

pi-agent uses a **TypeScript bridge** (`pi-agent-bridge`) to communicate with LLM providers. The Python SDK spawns the bridge process and communicates over stdio using a JSON-RPC-like protocol.

```
Python application
      │
  pi_agent (this package)
      │  stdio JSON
  pi-agent-bridge (TypeScript / Node.js)
      │  HTTPS
  LLM provider API
```

The wheel includes pre-built binaries for:

| Platform | Architecture |
|----------|-------------|
| macOS | ARM64 (Apple Silicon) |
| macOS | x86_64 |
| Linux | x86_64 (glibc) |
| Linux | ARM64 (glibc) |
| Linux | x86_64 (musl / Alpine) |
| Linux | ARM64 (musl / Alpine) |
| Windows | x64 |
| Windows | ARM64 |

If no binary is found for your platform, the SDK falls back to running the bridge via Node.js (requires Node ≥ 18 in `PATH`).

---

## License

MIT
