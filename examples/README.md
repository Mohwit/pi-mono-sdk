# pi-agent Examples

All examples target a local Ollama LLM by default. Start Ollama before running:

```bash
ollama serve
ollama pull llama3.1
```

To use a cloud model instead, set the environment variable and pass `--model`:
```bash
export ANTHROPIC_API_KEY=sk-...
# then inside each example replace LocalModelConfig with ModelConfig(provider="anthropic", name="claude-sonnet-4-6")
```

---

## Directory structure

```
examples/
├── 01_quickstart/      Core API — first examples to run
├── 02_tools/           Tool calling — give the agent Python functions
├── 03_hooks/           Lifecycle hooks — beforeToolCall / afterToolCall
├── 04_streaming/       Complete streaming event reference
├── 05_control/         Agent control — steer, follow-up, abort, reset
├── 06_context/         Context management — session persistence & transformContext
├── 07_sync/            Synchronous API — no asyncio required
├── 08_agents/          Full-featured agents combining all features
└── 09_cli_agent/       Full-screen TUI coding agent (like Claude Code)
```

---

## Quick-start path (recommended order)

```bash
# 1. Basic streaming chat
python examples/01_quickstart/simple_chat.py

# 2. Add tools
python examples/02_tools/tools_example.py

# 3. Understand every event
python examples/04_streaming/streaming_events.py

# 4. Hook into tool calls
python examples/03_hooks/before_tool_call.py
python examples/03_hooks/after_tool_call.py

# 5. Control the agent mid-response
python examples/05_control/steering_and_followup.py pipeline

# 6. Persist and resume sessions
python examples/06_context/session_persistence.py
python examples/06_context/session_persistence.py --resume

# 7. Slide / scrub / inject context
python examples/06_context/transform_context.py combined

# 8. Full research agent (everything together)
python examples/08_agents/research_agent.py
python examples/08_agents/research_agent.py --resume
```

---

## Example index

### 01 · Quickstart

| File | What it shows |
|------|---------------|
| `simple_chat.py` | `agent.prompt()` async generator, streaming text deltas, multi-turn |
| `local_llm.py` | `LocalModelConfig`, Ollama openai-completions, tool calling on local model |
| `get_api_key.py` | `get_api_key` callback, vault simulation, key rotation, audit logging |

### 02 · Tools

| File | What it shows |
|------|---------------|
| `tools_example.py` | `ToolDefinition`, JSON-Schema params, parallel vs sequential, error handling |

### 03 · Hooks

| File | What it shows |
|------|---------------|
| `before_tool_call.py` | Security gate, interactive confirmation, rate limiter |
| `after_tool_call.py` | Output monitoring, result enrichment, circuit breaker, PII filter |

### 04 · Streaming

| File | What it shows |
|------|---------------|
| `streaming_events.py` | Every event type: `agent_start`, `turn_start`, `message_update`, `tool_execution_*`, `turn_end` |

### 05 · Control

| File | What it shows |
|------|---------------|
| `steering_and_followup.py` | `steer()`, `follow_up()`, `abort()`, `reset()`, `set_model()`, `set_system_prompt()` |

### 06 · Context

| File | What it shows |
|------|---------------|
| `session_persistence.py` | `save_session()`, `load_session()`, `get_state()`, resume across runs |
| `transform_context.py` | Sliding window, live status injection, PII scrubbing, combined pipeline |

### 07 · Sync API

| File | What it shows |
|------|---------------|
| `sync_api.py` | `SyncPiAgent` for scripts and notebooks, batch processing, sync REPL |

### 08 · Agents

| File | What it shows |
|------|---------------|
| `research_agent.py` | All features combined: tools, hooks, transform, session persistence |

### 09 · CLI Coding Agent

| Entry point | Run | What it builds |
|-------------|-----|----------------|
| `09_cli_agent/` | `python examples/09_cli_agent/` | Full-screen TUI coding agent — Claude Code-style UI with 10 file/shell tools, slash commands, Esc-to-abort, follow-up queue, auto-save |

---

## Core concepts

### Minimal streaming chat

```python
from pi_agent import PiAgent, PiAgentOptions, LocalModelConfig

model = LocalModelConfig(
    id="llama3.1", name="Llama 3.1",
    api="openai-completions", provider="local",
    base_url="http://localhost:11434/v1/",
)

async def get_api_key(provider: str) -> str:
    return "ollama"   # Ollama accepts any non-empty string

async with PiAgent(PiAgentOptions(
    system_prompt="You are a helpful assistant.",
    model=model,
    get_api_key=get_api_key,
)) as agent:
    async for event in agent.prompt("Hello!"):
        ae = event.get("assistantMessageEvent", {})
        if ae.get("type") == "text_delta":
            print(ae["delta"], end="", flush=True)
```

### Defining a tool

```python
from pi_agent import ToolDefinition

async def get_weather(params: dict) -> dict:
    city = params["city"]
    return {"content": [{"type": "text", "text": f"It's 22 °C in {city}."}]}

weather_tool = ToolDefinition(
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
)
```

### Event stream reference

```python
async for event in agent.prompt("..."):
    t = event.get("type")

    if t == "message_update":
        ae = event.get("assistantMessageEvent", {})
        if ae.get("type") == "text_delta":
            print(ae["delta"], end="", flush=True)   # streaming text
        elif ae.get("type") == "thinking_delta":
            pass   # extended thinking delta (models that support it)

    elif t == "tool_execution_start":
        print(f"\nRunning {event['toolName']}({event.get('params', {})})")

    elif t == "tool_execution_end":
        print(f"  → {event.get('result')}")

    elif t == "turn_end":
        print(f"\n[stop: {event.get('stopReason')}]")
```

### v2 hooks quick reference

```python
# beforeToolCall — inspect or block before execution
async def before_tool_call(ctx: dict) -> dict | None:
    # ctx keys: id, name, params
    if "drop_table" in ctx["params"].get("command", ""):
        return {"block": True, "reason": "Destructive SQL blocked"}
    return None   # None means allow

# afterToolCall — inspect result, enrich, or circuit-break
async def after_tool_call(ctx: dict) -> dict | None:
    # ctx keys: id, name, params, result, is_error
    if ctx.get("is_error"):
        return {"terminate": True}
    return {"details": {"fetched_at": "2025-01-01T00:00:00Z"}}

# transformContext — rewrite message history before every LLM call
async def transform_context(messages: list[dict]) -> list[dict]:
    return messages[-12:]   # sliding window: keep last 6 turns
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
