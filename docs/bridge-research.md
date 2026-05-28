# Pi Agent SDK — Python Interface Research

## Overview

Research into building a Python interface for `@earendil-works/pi-agent-core`, following the same
architectural pattern as the Claude Code Python SDK (`claude-agent-sdk-python`).

---

## 1. Pi Agent SDK (`@earendil-works/pi-agent-core`)

**Source**: https://github.com/earendil-works/pi/tree/main/packages/agent

### What it is
A stateful TypeScript agent framework supporting all major AI providers (Anthropic, OpenAI, Google,
etc.) via `@earendil-works/pi-ai`. It is a **library**, not a CLI — there is no bundled executable.

### Core `Agent` class

```typescript
const agent = new Agent({
  initialState: {
    systemPrompt: "You are helpful.",
    model: getModel("anthropic", "claude-sonnet-4-20250514"),
    thinkingLevel?: "off" | "minimal" | "low" | "medium" | "high" | "xhigh",
    tools?: AgentTool[],
    messages?: AgentMessage[],
  },
  convertToLlm?: (messages) => Message[],
  transformContext?: async (messages, signal) => AgentMessage[],
  steeringMode?: "one-at-a-time" | "all",
  followUpMode?: "one-at-a-time" | "all",
  toolExecution?: "parallel" | "sequential",
  streamFn?: CustomStreamFunction,
  sessionId?: string,
  getApiKey?: async (provider) => string,
  beforeToolCall?: async (ctx) => void | { block: true, reason: string },
  afterToolCall?: async (ctx) => void | { terminate: true } | { details: any },
  thinkingBudgets?: { minimal: number, low: number, ... },
});
```

### Key methods

| Method | Description |
|--------|-------------|
| `agent.prompt(text, attachments?)` | Send a user message; drives a full agent turn |
| `agent.continue()` | Resume from existing context without a new message |
| `agent.abort()` | Cancel the running turn |
| `agent.waitForIdle()` | Await until no turn is running |
| `agent.subscribe(handler)` | Register an event listener; returns unsubscribe fn |
| `agent.reset()` | Clear all messages and runtime state |
| `agent.steer(message)` | Queue a message to inject after current tools finish |
| `agent.followUp(message)` | Queue a message for when the agent would otherwise stop |
| `agent.clearSteeringQueue()` | Drain the steer queue |
| `agent.clearFollowUpQueue()` | Drain the follow-up queue |
| `agent.clearAllQueues()` | Drain both queues |
| `agent.state` | Read/write agent state |

### Agent state shape

```typescript
interface AgentState {
  systemPrompt: string;
  model: Model;
  thinkingLevel: ThinkingLevel;
  tools: AgentTool[];
  messages: AgentMessage[];
  readonly isStreaming: boolean;
  readonly streamingMessage?: AgentMessage;
  readonly pendingToolCalls: ReadonlySet<string>;
  readonly errorMessage?: string;
}
```

### Event system

Events are emitted to all `subscribe()` listeners during a turn:

| Event | When |
|-------|------|
| `agent_start` | Run begins |
| `turn_start` | LLM call starts |
| `message_start` | A message begins (user, assistant, tool result) |
| `message_update` | Streaming delta from assistant |
| `message_end` | A message completes |
| `tool_execution_start` | Tool invocation begins |
| `tool_execution_update` | Tool streams partial progress |
| `tool_execution_end` | Tool finishes |
| `turn_end` | LLM + tools cycle ends |
| `agent_end` | Run completes |

### Tool definition

```typescript
const myTool: AgentTool = {
  name: "read_file",
  label: "Read File",
  description: "Read a file's contents",
  parameters: Type.Object({ path: Type.String() }),  // TypeBox schema
  executionMode?: "sequential" | "parallel",
  execute: async (toolCallId, params, signal, onUpdate) => {
    return {
      content: [{ type: "text", text: "file contents" }],
      details: { path: params.path },
    };
  },
};
```

Errors: **throw** exceptions rather than returning error content; the agent wraps them as `isError: true`.

---

## 2. Claude Code Python SDK — Reference Pattern

**Source**: https://github.com/anthropics/claude-agent-sdk-python

### How it works

The Claude Code Python SDK works because `claude` ships as a **CLI** that speaks a
JSON-lines protocol over stdin/stdout. The Python package just spawns a subprocess.

```
Python process                   claude CLI subprocess
──────────────────               ─────────────────────
ClaudeSDKClient
  ↕ JSON lines via stdio  ←────→  handles conversation, tools, state
```

Key classes: `ClaudeSDKClient`, `SubprocessCLITransport`, `Query` (internal protocol handler).

**Transport contract**:
- `.connect()` — spawns subprocess
- `.write(line)` — sends a JSON line to subprocess stdin
- `.disconnect()` — closes subprocess

**Message flow**:
- Python writes `{"type":"user","message":{...}}` to stdin
- Claude CLI writes events as JSON lines to stdout
- Python reads and parses them into typed `Message` objects

### Key design decisions to copy

1. Async context manager (`async with client`)
2. `receive_response()` async generator that terminates after `ResultMessage`
3. Pending callback map for roundtrip calls (tool results, hooks)
4. Single background reader task that dispatches to active generators
5. `asyncio.Queue` per active `prompt()` call

---

## 3. The Fundamental Difference

| | Claude Code Python SDK | Pi Agent Python |
|---|---|---|
| Subprocess | `claude` CLI (user installs) | `pi-agent-bridge` (bundled in wheel) |
| Protocol side | Claude CLI owns the agent | Bridge owns the agent |
| Tools | Run inside Claude process | Python-defined, roundtrip to Python |

Because Pi SDK is a library (not a CLI), we must ship our own bridge executable.

---

## 4. Bun Compile Approach

### Why Bun

`bun build --compile` creates a **truly standalone binary**:
- Embeds Bun runtime + all npm dependencies + TypeScript source
- Cross-compiles from one machine to all target platforms
- `--bytecode` flag pre-compiles to bytecode for ~2× faster startup
- Output: a single native binary with zero runtime dependencies

### Build command

```bash
bun build --compile --minify --bytecode \
  --target=bun-darwin-arm64 \
  ./bridge/bridge.ts \
  --outfile ./src/pi_agent/bin/pi-agent-bridge-darwin-arm64
```

### Supported cross-compilation targets

| Target flag | OS | Arch |
|---|---|---|
| `bun-darwin-arm64` | macOS | Apple Silicon |
| `bun-darwin-x64` | macOS | Intel |
| `bun-linux-x64` | Linux | x86_64 (glibc) |
| `bun-linux-arm64` | Linux | ARM64 (glibc) |
| `bun-linux-x64-musl` | Linux | x86_64 (Alpine/musl) |
| `bun-windows-x64` | Windows | x64 |
| `bun-windows-arm64` | Windows | ARM64 |

All built from a **single machine** — no per-platform CI runners required.

### Binary size & PyPI

- Bun compiled binaries: ~80–100 MB (runtime embedded)
- PyPI per-file limit: 100 MB
- **Solution**: platform-specific wheels

```
pi_agent-0.1.0-py3-none-macosx_11_0_arm64.whl     # darwin-arm64 only
pi_agent-0.1.0-py3-none-macosx_10_9_x86_64.whl    # darwin-x64 only
pi_agent-0.1.0-py3-none-manylinux2014_x86_64.whl   # linux-x64 only
pi_agent-0.1.0-py3-none-manylinux2014_aarch64.whl  # linux-arm64 only
pi_agent-0.1.0-py3-none-win_amd64.whl              # windows-x64 only
```

`pip install pi-agent` downloads only the one wheel for the user's platform.

---

## 5. Bridge Protocol Design

The bridge is a long-lived subprocess. Communication is JSON lines over stdin/stdout.

### Python → Bridge (stdin)

| Message | Purpose |
|---------|---------|
| `{"type":"initialize","options":{...}}` | Create the `Agent` instance |
| `{"type":"prompt","text":"...","attachments":[...]}` | Call `agent.prompt()` |
| `{"type":"continue"}` | Call `agent.continue()` |
| `{"type":"abort"}` | Call `agent.abort()` |
| `{"type":"reset"}` | Call `agent.reset()` |
| `{"type":"steer","message":{...}}` | Call `agent.steer()` |
| `{"type":"follow_up","message":{...}}` | Call `agent.followUp()` |
| `{"type":"clear_steering"}` | Call `agent.clearSteeringQueue()` |
| `{"type":"clear_follow_up"}` | Call `agent.clearFollowUpQueue()` |
| `{"type":"clear_all"}` | Call `agent.clearAllQueues()` |
| `{"type":"set_state","field":"model","value":{...}}` | Mutate `agent.state.*` |
| `{"type":"tool_result","id":"tc_1","result":{...},"is_error":false}` | Return Python tool result |
| `{"type":"shutdown"}` | Graceful exit |

### Bridge → Python (stdout)

| Message | Purpose |
|---------|---------|
| `{"type":"ready"}` | Bridge initialized, agent created |
| `{"type":"event","event":{...}}` | Any `AgentEvent` from Pi SDK |
| `{"type":"prompt_done"}` | `agent.prompt()` has resolved |
| `{"type":"continue_done"}` | `agent.continue()` has resolved |
| `{"type":"error","message":"...","stack":"..."}` | Unhandled bridge error |
| `{"type":"tool_call","id":"tc_1","name":"read_file","params":{...}}` | Python tool needs executing |

### Tool execution roundtrip

```
Python                          Bridge
──────────────────              ─────────────────────────────
                                agent calls execute("tc_1", params)
                                  → suspends (awaits Promise)
                                  → sends {"type":"tool_call","id":"tc_1",...}
receives tool_call
  → looks up Python fn
  → calls fn(params)
  → sends {"type":"tool_result","id":"tc_1","result":{...}}
                                receives tool_result
                                  → resolves Promise with result
                                  → agent continues
```

The bridge keeps a `Map<id, resolve>` for pending tool calls. Python keeps a
`dict[id, asyncio.Future]` for pending results.

---

## 6. Bridge Source Sketch (`bridge/bridge.ts`)

```typescript
import { Agent } from "@earendil-works/pi-agent-core";
import { getModel } from "@earendil-works/pi-ai";
import { createInterface } from "readline";

const rl = createInterface({ input: process.stdin, terminal: false });
let agent: Agent | null = null;
const pendingTools = new Map<string, (result: any) => void>();

function send(obj: unknown) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

rl.on("line", async (line) => {
  const msg = JSON.parse(line);

  switch (msg.type) {
    case "initialize": {
      const { systemPrompt, model: m, tools: toolDefs = [], ...rest } = msg.options;

      const tools = toolDefs.map((t: any) => ({
        ...t,
        execute: async (id: string, params: any) => {
          send({ type: "tool_call", id, name: t.name, params });
          return new Promise((resolve) => pendingTools.set(id, resolve));
        },
      }));

      agent = new Agent({
        initialState: {
          systemPrompt,
          model: getModel(m.provider, m.name),
          tools,
          thinkingLevel: msg.options.thinkingLevel,
          messages: msg.options.messages ?? [],
        },
        toolExecution: rest.toolExecution,
        steeringMode: rest.steeringMode,
        followUpMode: rest.followUpMode,
      });

      agent.subscribe((event) => send({ type: "event", event }));
      send({ type: "ready" });
      break;
    }

    case "prompt":
      try {
        await agent!.prompt(msg.text, msg.attachments);
        send({ type: "prompt_done" });
      } catch (err: any) {
        send({ type: "error", message: err.message, stack: err.stack });
      }
      break;

    case "continue":
      try {
        await agent!.continue();
        send({ type: "continue_done" });
      } catch (err: any) {
        send({ type: "error", message: err.message, stack: err.stack });
      }
      break;

    case "abort":         agent!.abort(); break;
    case "reset":         agent!.reset(); break;
    case "steer":         agent!.steer(msg.message); break;
    case "follow_up":     agent!.followUp(msg.message); break;
    case "clear_steering":  agent!.clearSteeringQueue(); break;
    case "clear_follow_up": agent!.clearFollowUpQueue(); break;
    case "clear_all":       agent!.clearAllQueues(); break;

    case "set_state":
      (agent!.state as any)[msg.field] = msg.value;
      break;

    case "tool_result":
      pendingTools.get(msg.id)?.(msg.result);
      pendingTools.delete(msg.id);
      break;

    case "shutdown":
      process.exit(0);
  }
});
```

---

## 7. Python API Design

### Types (`types.py`)

```python
from dataclasses import dataclass, field
from typing import Literal, Any

@dataclass
class ModelConfig:
    provider: str   # "anthropic", "openai", "google", ...
    name: str       # "claude-sonnet-4-20250514", "gpt-4o", ...

@dataclass
class ToolResult:
    content: list[dict]          # [{"type": "text", "text": "..."}]
    details: dict = field(default_factory=dict)
    terminate: bool = False

@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict             # JSON Schema
    execute: Any                 # async (params: dict) -> ToolResult | dict
    label: str = ""
    execution_mode: Literal["sequential", "parallel"] = "parallel"

@dataclass
class PiAgentOptions:
    system_prompt: str
    model: ModelConfig
    thinking_level: str | None = None
    tools: list[ToolDefinition] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    tool_execution: Literal["parallel", "sequential"] = "parallel"
    steering_mode: Literal["one-at-a-time", "all"] = "one-at-a-time"
    follow_up_mode: Literal["one-at-a-time", "all"] = "one-at-a-time"
```

### Client (`client.py`)

```python
class PiAgent:
    def __init__(self, options: PiAgentOptions): ...

    # Core operations — return async generators of AgentEvent
    async def prompt(self, text: str, attachments=None) -> AsyncIterator[AgentEvent]: ...
    async def continue_(self) -> AsyncIterator[AgentEvent]: ...

    # Fire-and-forget controls
    def abort(self) -> None: ...
    def reset(self) -> None: ...
    def steer(self, message: dict) -> None: ...
    def follow_up(self, message: dict) -> None: ...
    def clear_steering_queue(self) -> None: ...
    def clear_follow_up_queue(self) -> None: ...
    def clear_all_queues(self) -> None: ...

    # State mutation
    def set_model(self, model: ModelConfig) -> None: ...
    def set_system_prompt(self, prompt: str) -> None: ...
    def set_thinking_level(self, level: str) -> None: ...

    @property
    def state(self) -> AgentState: ...  # last snapshot from bridge

    # Lifecycle
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def __aenter__(self) -> "PiAgent": ...
    async def __aexit__(self, *args) -> bool: ...
```

### Usage examples

**Simple streaming:**
```python
async with PiAgent(PiAgentOptions(
    system_prompt="You are helpful.",
    model=ModelConfig(provider="anthropic", name="claude-sonnet-4-20250514"),
)) as agent:
    async for event in agent.prompt("Hello!"):
        if event["type"] == "message_update":
            print(event["assistantMessageEvent"]["delta"], end="", flush=True)
```

**Python tools:**
```python
async def read_file(params: dict) -> dict:
    with open(params["path"]) as f:
        return {"content": [{"type": "text", "text": f.read()}]}

async with PiAgent(PiAgentOptions(
    system_prompt="You are a coding assistant.",
    model=ModelConfig(provider="openai", name="gpt-4o"),
    tools=[ToolDefinition(
        name="read_file",
        description="Read a file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        execute=read_file,
    )],
)) as agent:
    async for event in agent.prompt("What's in README.md?"):
        if event["type"] == "message_update":
            print(event["assistantMessageEvent"]["delta"], end="")
```

**Multi-provider:**
```python
options = PiAgentOptions(
    system_prompt="You are helpful.",
    model=ModelConfig(provider="openai", name="gpt-4o"),
)
```

---

## 8. Internal Architecture

```
PiAgent
  ├── connect()
  │     └── SubprocessTransport.connect()
  │           └── subprocess.Popen([bridge_binary])
  │                 starts _reader_task (asyncio background task)
  │
  ├── prompt(text)
  │     ├── writes {"type":"prompt","text":...} to bridge stdin
  │     ├── creates asyncio.Queue → yields from it
  │     └── _reader_task feeds events into the Queue
  │           ├── on "event"       → put in active Queue
  │           ├── on "tool_call"   → call Python fn → write "tool_result" back
  │           └── on "prompt_done" → put sentinel → generator stops
  │
  └── disconnect()
        └── writes {"type":"shutdown"} → waits for process to exit
```

### Pending tool callback map

```python
# _callbacks.py
import asyncio
from typing import Any

class PendingCallbacks:
    def __init__(self):
        self._futures: dict[str, asyncio.Future] = {}

    def register(self, id: str) -> asyncio.Future:
        fut = asyncio.get_event_loop().create_future()
        self._futures[id] = fut
        return fut

    def resolve(self, id: str, result: Any) -> None:
        fut = self._futures.pop(id, None)
        if fut and not fut.done():
            fut.set_result(result)
```

---

## 9. CI / Release Process

```yaml
# .github/workflows/release.yml (sketch)
jobs:
  build-bridge:
    runs-on: ubuntu-latest   # cross-compile from Linux
    steps:
      - uses: oven-sh/setup-bun@v2
      - run: cd bridge && bun install
      - run: bun run bridge/build.ts   # produces all 5+ binaries

  build-wheels:
    needs: build-bridge
    strategy:
      matrix:
        include:
          - platform: macosx_11_0_arm64
            binary: pi-agent-bridge-darwin-arm64
          - platform: macosx_10_9_x86_64
            binary: pi-agent-bridge-darwin-x64
          - platform: manylinux2014_x86_64
            binary: pi-agent-bridge-linux-x64
          - platform: manylinux2014_aarch64
            binary: pi-agent-bridge-linux-arm64
          - platform: win_amd64
            binary: pi-agent-bridge-win32-x64.exe
    steps:
      - run: python scripts/build_wheel.py --platform ${{ matrix.platform }} --binary ${{ matrix.binary }}
      - uses: pypa/gh-action-pypi-publish@release/v1
```

---

## 10. Open Questions (for future decisions)

| Question | Options |
|----------|---------|
| Tool parameter schema | Raw JSON Schema dict vs Pydantic model with auto-schema |
| `getApiKey` hook | Python async callback (roundtrip) vs env var convention |
| `beforeToolCall`/`afterToolCall` | Include in v1 or ship in v2 |
| `transformContext` | Include in v1 or skip (Pi SDK can use it server-side) |
| Sync API | Expose `prompt_sync()` for non-async codebases? |
| musl/Alpine support | Add `bun-linux-x64-musl` target for Docker Alpine users? |
