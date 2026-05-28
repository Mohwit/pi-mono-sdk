# Pi Agent Python SDK — Implementation Plan (v3)

> Reference: `docs/bridge-research.md` for research, protocol spec, and design rationale.
> Advisor review incorporated — see inline notes marked **[ADVISOR]**.

---

## Goal

A Python package (`pi-agent`) that lets Python users drive the `@earendil-works/pi-agent-core`
TypeScript SDK. Primary distribution: platform-specific PyPI wheels, each containing a pre-compiled
Bun standalone binary for that platform. A Bun runtime fallback is included for development.

---

## Transport Strategy

### Primary: Pre-compiled Bun binary (bundled in wheel)

`bun build --compile` → per-platform binary embedded in platform-specific PyPI wheels.
Each wheel contains only the binary for its platform, staying under PyPI's 100 MB limit.

> **[ADVISOR] Pre-distribution gate**: Measure actual compiled binary size before committing to
> PyPI as the distribution channel. `@earendil-works/pi-ai` bundles Anthropic + OpenAI + Google
> SDKs and may push the binary over 100 MB. If it does, contingency options are:
> (a) split into a `pi-agent-binary` sub-package, or
> (b) post-install downloader that fetches the binary from GitHub Releases (like Prisma).

### Fallback: Bun runtime (development only)

If the compiled binary is not found, detect `bun` in PATH and run `bridge/bridge.ts` directly.

### No Node.js fallback in v1

Explicitly out of scope. `@earendil-works/pi-ai` may use Bun-specific APIs; a Node fallback
that silently fails is worse than no fallback. Listed as v2 after explicit testing.

### Detection order (v1)

```
1. Compiled binary in <package>/bin/   (installed wheel)
2. Compiled binary in <repo-root>/bin/ (local dev build)
3. bun run bridge/bridge.ts            (bun in PATH + source present)
4. raise PiConnectionError with actionable message
```

---

## Repository Layout

```
pi-mono-sdk/
├── bridge/
│   ├── bridge.ts          # TypeScript entry point
│   ├── package.json       # pinned versions (not "latest")
│   ├── bun.lockb          # committed — reproducible builds
│   └── build.ts           # Cross-compile → ../bin/  (--current flag for local)
│
├── src/
│   └── pi_agent/
│       ├── __init__.py    # Public API
│       ├── client.py      # PiAgent class
│       ├── types.py       # PiAgentOptions, ModelConfig, ToolDefinition, ToolResult, AgentState
│       ├── _errors.py     # PiAgentError, PiBridgeError, PiConnectionError, PiToolError
│       └── _transport.py  # SubprocessTransport + binary detection + stderr drainer
│
├── bin/                   # Built artifacts (git-ignored)
│   ├── pi-agent-bridge-darwin-arm64
│   ├── pi-agent-bridge-darwin-x64
│   ├── pi-agent-bridge-linux-x64
│   ├── pi-agent-bridge-linux-arm64
│   └── pi-agent-bridge-win32-x64.exe
│
├── examples/
│   ├── simple_chat.py
│   └── tools_example.py
│
├── tests/
│   ├── test_transport.py
│   └── test_client.py
│
├── scripts/
│   └── build_wheel.py     # Per-platform wheel builder (handles executable bit)
│
├── pyproject.toml
└── .gitignore
```

> No `_callbacks.py` — tool roundtrips and `getApiKey` roundtrips are handled inline in
> `_read_loop` via `asyncio.create_task`. The bridge holds the JS Promise; Python executes
> and sends the result back. No Python-side futures needed.

---

## Protocol Design

### 1. Serialized readline handler (CRITICAL)

**[ADVISOR]** Node's `readline` fires the next `"line"` event before an `async` handler resolves.
Without serialization, a second `prompt` arriving mid-turn launches a concurrent `agent.prompt()`
on the same Agent instance — undefined behavior.

Fix: explicit serial promise chain. All logic lives in `handleLine()`:

```typescript
let chain = Promise.resolve();
rl.on("line", (line: string) => {
  chain = chain.then(() => handleLine(line)).catch(() => {});
});

async function handleLine(line: string): Promise<void> {
  // ... parse + dispatch
}
```

### 2. Request ID correlation (CRITICAL)

**[ADVISOR]** Every Python→Bridge message carries a `request_id`. Every Bridge→Python response
echoes it. This allows Python to correctly attribute errors and done-signals to the right waiting
coroutine — essential when multiple generators are live concurrently.

- Fire-and-forget messages (`abort`, `reset`, `steer`, etc.) carry a `request_id` but no response.
- `tool_call` / `tool_result` use the SDK's `toolCallId` as the correlation key (not `request_id`).
- `get_api_key` / `api_key_result` use a bridge-generated `request_id`.

### 3. Protocol version handshake

**[ADVISOR]** Bridge includes its protocol version in `ready`. Python asserts it matches.
Catches binary/package version skew at connect time.

```jsonc
{"type": "ready", "protocol_version": 1}
```

`PROTOCOL_VERSION = 1` is a constant in both bridge.ts and client.py.

### 4. Python → Bridge message table

| Message | Key fields | Notes |
|---------|-----------|-------|
| `initialize` | `request_id`, `options` | First message; creates Agent |
| `prompt` | `request_id`, `text`, `attachments?` | Starts a turn |
| `continue` | `request_id` | Resume without new message |
| `abort` | `request_id` | Cancel current turn |
| `reset` | `request_id` | Clear messages |
| `steer` | `request_id`, `message` | Queue steer message |
| `follow_up` | `request_id`, `message` | Queue follow-up message |
| `clear_steering` | `request_id` | Drain steer queue |
| `clear_follow_up` | `request_id` | Drain follow-up queue |
| `clear_all` | `request_id` | Drain both queues |
| `set_state` | `request_id`, `field`, `value` | Mutate whitelisted field only |
| `wait_for_idle` | `request_id` | Bridge replies `idle` when not streaming |
| `tool_result` | `id` (toolCallId), `result`, `is_error` | Return tool output |
| `api_key_result` | `request_id`, `key`, `error?` | Return API key (or error) |
| `shutdown` | — | Graceful exit |

### 5. Bridge → Python message table

| Message | Key fields | Notes |
|---------|-----------|-------|
| `ready` | `protocol_version` | Agent created |
| `event` | `request_id`, `event` | Forwarded AgentEvent |
| `prompt_done` | `request_id` | `agent.prompt()` resolved |
| `continue_done` | `request_id` | `agent.continue()` resolved |
| `idle` | `request_id` | `agent.waitForIdle()` resolved |
| `tool_call` | `id`, `name`, `params` | Python tool needs executing |
| `get_api_key` | `request_id`, `provider` | Python callback needed for API key |
| `error` | `request_id`, `message`, `stack` | Error attributed to a specific request |

### 6. Tool roundtrip

```
Python                               Bridge (JS)
──────────────────                   ─────────────────────────────
                                     agent calls execute("tc_1", params, signal)
                                       → suspends (awaits Promise)
                                       → sends {"type":"tool_call","id":"tc_1",...}
receives tool_call
  → asyncio.create_task(_handle_tool_call)
  → calls Python fn(params)
  → sends {"type":"tool_result","id":"tc_1","result":{...},"is_error":false}
                                     receives tool_result
                                       → resolves Promise with result
                                       → agent continues
```

### 7. getApiKey roundtrip (v1)

```
Python                               Bridge (JS)
──────────────────                   ─────────────────────────────
                                     SDK calls getApiKey("anthropic")
                                       → sends {"type":"get_api_key",
                                                "request_id":"gak_1",
                                                "provider":"anthropic"}
receives get_api_key
  → asyncio.create_task(_handle_get_api_key)
  → calls options.get_api_key("anthropic")
  → sends {"type":"api_key_result","request_id":"gak_1","key":"sk-ant-..."}
                                     receives api_key_result
                                       → resolves getApiKey Promise with key
```

If `options.get_api_key` is `None`, the bridge falls back to standard provider env vars
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) — the Pi SDK handles this automatically when
no `getApiKey` option is passed during initialization.

### 8. Mutable `set_state` field whitelist

**[ADVISOR]** Only these fields may be set via `set_state`. Bridge enforces this.

```typescript
const MUTABLE_FIELDS = new Set(["model", "systemPrompt", "thinkingLevel", "tools"]);
// Mutations to isStreaming, pendingToolCalls, errorMessage, messages are rejected with error.
```

---

## Phase 1 — Bridge TypeScript (`bridge/bridge.ts`)

Full implementation requirements:

```typescript
// Constants
const PROTOCOL_VERSION = 1;
const MUTABLE_FIELDS = new Set(["model", "systemPrompt", "thinkingLevel", "tools"]);

// State
let agent: Agent | null = null;
const pendingTools = new Map<string, { resolve: (r: any) => void; reject: (e: Error) => void }>();

// Serial handler — prevents concurrent agent.prompt() calls
let chain = Promise.resolve();
rl.on("line", (line: string) => {
  chain = chain.then(() => handleLine(line)).catch(() => {});
});

async function handleLine(line: string): Promise<void> {
  let msg: any;
  try { msg = JSON.parse(line); } catch {
    send({ type: "error", request_id: null, message: "Invalid JSON", stack: "" });
    return;
  }
  const { request_id } = msg;

  switch (msg.type) {
    case "initialize": { /* create agent, set up getApiKey if has_get_api_key, send ready */ }
    case "prompt":     { await agent!.prompt(...); send({ type:"prompt_done", request_id }); }
    case "continue":   { await agent!.continue(); send({ type:"continue_done", request_id }); }
    case "wait_for_idle": { await agent!.waitForIdle(); send({ type:"idle", request_id }); }
    case "abort":      { agent!.abort(); break; }
    case "reset":      { agent!.reset(); break; }
    case "steer":      { agent!.steer(msg.message); break; }
    case "follow_up":  { agent!.followUp(msg.message); break; }
    case "clear_steering":  { agent!.clearSteeringQueue(); break; }
    case "clear_follow_up": { agent!.clearFollowUpQueue(); break; }
    case "clear_all":       { agent!.clearAllQueues(); break; }
    case "set_state":  { /* whitelist check, then mutate */ }
    case "tool_result": { /* resolve or reject pending tool */ }
    case "api_key_result": { /* resolve or reject pending getApiKey */ }
    case "shutdown":   { process.stdout.end(() => process.exit(0)); break; }
  }
}
```

Key details:

- **`initialize` → getApiKey**: if `msg.options.has_get_api_key === true`, set `getApiKey` on
  the agent options as a function that sends `get_api_key` to stdout and awaits `api_key_result`
  via a `pendingApiKeys: Map<string, {resolve, reject}>`.
- **Tool `execute()`**: wraps as Promise in `pendingTools`, sends `tool_call`, resolves/rejects on
  `tool_result`. `is_error: true` → `pending.reject(new Error(text))`.
- **`set_state`**: check `MUTABLE_FIELDS` first; reject unknown fields with an error message.
- **`shutdown`**: `process.stdout.end(() => process.exit(0))` — never bare `process.exit(0)`.
- **`rl.on("close")`**: call `agent?.abort()`, then `process.stdout.end(() => process.exit(0))`.
- **Error wrapping**: all `await` calls inside `handleLine` are wrapped in try/catch that sends
  `{"type":"error","request_id":...,"message":"...","stack":"..."}`.

---

## Phase 2 — Build Script (`bridge/build.ts`)

```typescript
// bun run bridge/build.ts           — all 5 targets
// bun run bridge/build.ts --current — current platform only (for local dev)

const targets = [
  { target: "bun-darwin-arm64", out: "pi-agent-bridge-darwin-arm64"  },
  { target: "bun-darwin-x64",   out: "pi-agent-bridge-darwin-x64"    },
  { target: "bun-linux-x64",    out: "pi-agent-bridge-linux-x64"     },
  { target: "bun-linux-arm64",  out: "pi-agent-bridge-linux-arm64"   },
  { target: "bun-windows-x64",  out: "pi-agent-bridge-win32-x64.exe" },
];
// bun build --compile --minify --bytecode --target=... bridge.ts --outfile ../bin/...
// After each build: log file size — gate on < 100 MB
```

After building, `bridge/build.ts` prints each binary's size. If any exceeds 95 MB, it prints a
warning about PyPI's 100 MB limit.

---

## Phase 3 — Python Types (`src/pi_agent/types.py`)

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Literal

@dataclass
class ModelConfig:
    provider: str   # "anthropic" | "openai" | "google" | ...
    name: str       # "claude-sonnet-4-20250514", "gpt-4o", ...

@dataclass
class ToolResult:
    content: list[dict]            # [{"type": "text", "text": "..."}]
    details: dict = field(default_factory=dict)
    terminate: bool = False

@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict               # JSON Schema object
    execute: Any                   # async (params: dict) -> ToolResult | dict
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
    # v1: getApiKey callback — if None, bridge relies on provider env vars
    get_api_key: Callable[[str], Awaitable[str]] | None = None

@dataclass
class AgentState:
    system_prompt: str
    model: ModelConfig
    thinking_level: str
    tools: list[dict]
    messages: list[dict]
    is_streaming: bool
    pending_tool_calls: list[str]
    error_message: str | None
```

---

## Phase 4 — Errors (`src/pi_agent/_errors.py`)

```python
class PiAgentError(Exception): pass
class PiBridgeError(PiAgentError): pass        # error message attributed from bridge
class PiConnectionError(PiAgentError): pass    # binary not found / subprocess failed to start
class PiToolError(PiAgentError): pass          # Python tool execution failed
```

---

## Phase 5 — Transport (`src/pi_agent/_transport.py`)

### Binary detection + executable bit fix

```python
_BINARY_MAP = {
    ("darwin", "arm64"):   "pi-agent-bridge-darwin-arm64",
    ("darwin", "x86_64"):  "pi-agent-bridge-darwin-x64",
    ("linux",  "x86_64"):  "pi-agent-bridge-linux-x64",
    ("linux",  "aarch64"): "pi-agent-bridge-linux-arm64",
    ("win32",  "AMD64"):   "pi-agent-bridge-win32-x64.exe",
}

_PACKAGE_BIN_DIR = Path(__file__).parent / "bin"
_REPO_BIN_DIR    = Path(__file__).parent.parent.parent / "bin"
_BRIDGE_SRC      = Path(__file__).parent.parent.parent / "bridge" / "bridge.ts"

def _detect_command() -> list[str]:
    plat, arch = sys.platform, platform.machine()
    name = _BINARY_MAP.get((plat, arch))

    if name:
        for d in (_PACKAGE_BIN_DIR, _REPO_BIN_DIR):
            p = d / name
            if p.exists() and p.stat().st_size > 0:
                _ensure_executable(p)   # [ADVISOR] wheels strip Unix permissions
                return [str(p)]

    bun = shutil.which("bun")
    if bun and _BRIDGE_SRC.exists():
        return [bun, "run", str(_BRIDGE_SRC)]

    raise PiConnectionError(
        f"No pi-agent-bridge found for {plat}/{arch}.\n"
        "  1. Install platform wheel:  pip install pi-agent\n"
        "  2. Build locally:           cd bridge && bun install && "
        "bun run build.ts --current\n"
        "  3. Install Bun (fallback):  https://bun.sh"
    )

def _ensure_executable(path: Path) -> None:
    """[ADVISOR] Fix missing +x that wheel ZIP extraction strips on Unix."""
    import stat
    mode = path.stat().st_mode
    if not (mode & stat.S_IXUSR):
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
```

### `SubprocessTransport`

```python
class SubprocessTransport:
    async def connect(self) -> None:
        cmd = _detect_command()
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,   # [ADVISOR] pipe to prevent deadlock
        )
        # [ADVISOR] Drain stderr to prevent 64 KB pipe buffer deadlock
        asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        async for line in self._process.stderr:
            sys.stderr.buffer.write(b"[pi-bridge] " + line)
            sys.stderr.buffer.flush()

    async def write(self, obj: dict) -> None:
        line = (json.dumps(obj) + "\n").encode()
        self._process.stdin.write(line)
        await self._process.stdin.drain()

    async def iter_lines(self) -> AsyncIterator[dict]:
        while True:
            raw = await self._process.stdout.readline()
            if not raw:
                break
            try:
                yield json.loads(raw.strip())
            except json.JSONDecodeError:
                continue

    async def disconnect(self) -> None:
        try:
            await self.write({"type": "shutdown"})
            self._process.stdin.close()
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            self._process.kill()
            await self._process.wait()
        finally:
            self._process = None
```

---

## Phase 6 — Client (`src/pi_agent/client.py`)

### Constants

```python
PROTOCOL_VERSION = 1
```

### `connect()`

```python
async def connect(self) -> None:
    self._transport = SubprocessTransport()
    await self._transport.connect()
    self._ready_event = asyncio.Event()
    self._reader_task = asyncio.create_task(self._read_loop())
    await self._transport.write({
        "type": "initialize",
        "request_id": "init",
        "options": self._serialize_options(),
    })
    try:
        await asyncio.wait_for(self._ready_event.wait(), timeout=30.0)
    except asyncio.TimeoutError:
        raise PiConnectionError("Bridge did not send 'ready' within 30 seconds")

def _serialize_options(self) -> dict:
    o = self._options
    return {
        "systemPrompt": o.system_prompt,
        "model": {"provider": o.model.provider, "name": o.model.name},
        "thinkingLevel": o.thinking_level,
        "tools": [
            {"name": t.name, "label": t.label, "description": t.description,
             "parameters": t.parameters, "execution_mode": t.execution_mode}
            for t in o.tools
        ],
        "messages": o.messages,
        "toolExecution": o.tool_execution,
        "steeringMode": o.steering_mode,
        "followUpMode": o.follow_up_mode,
        # Signal to bridge: set up getApiKey roundtrip
        "has_get_api_key": o.get_api_key is not None,
    }
```

### `_read_loop()` — background dispatcher

```python
async def _read_loop(self) -> None:
    async for msg in self._transport.iter_lines():
        t   = msg.get("type")
        rid = msg.get("request_id")

        if t == "ready":
            # [ADVISOR] verify protocol version at connect time
            version = msg.get("protocol_version", 0)
            if version != PROTOCOL_VERSION:
                raise PiBridgeError(
                    f"Protocol version mismatch: expected {PROTOCOL_VERSION}, got {version}"
                )
            self._ready_event.set()

        elif t == "event":
            if q := self._active_queues.get(rid):
                await q.put(("event", msg["event"]))

        elif t in ("prompt_done", "continue_done", "idle"):
            if q := self._active_queues.get(rid):
                await q.put(("done", None))

        elif t == "tool_call":
            asyncio.create_task(self._handle_tool_call(msg))

        elif t == "get_api_key":
            asyncio.create_task(self._handle_get_api_key(msg))

        elif t == "error":
            if q := self._active_queues.get(rid):
                await q.put(("error", msg.get("message", "Unknown bridge error")))

    # [ADVISOR] Bridge died — drain all active queues with error sentinel
    for q in self._active_queues.values():
        await q.put(("error", "Bridge process exited unexpectedly"))
    self._active_queues.clear()
```

### `_handle_tool_call()` — guaranteed tool_result send

```python
async def _handle_tool_call(self, msg: dict) -> None:
    tool_id = msg["id"]
    # [ADVISOR] Always send tool_result — never leave the bridge Promise pending
    try:
        tool = self._find_tool(msg["name"])
        if tool is None:
            raise PiToolError(f"Unknown tool: {msg['name']}")
        result = await tool.execute(msg["params"])
        if isinstance(result, ToolResult):
            result = {"content": result.content, "details": result.details}
        await self._transport.write({
            "type": "tool_result", "id": tool_id,
            "result": result, "is_error": False,
        })
    except Exception as e:
        await self._transport.write({
            "type": "tool_result", "id": tool_id,
            "result": {"content": [{"type": "text", "text": str(e)}]},
            "is_error": True,
        })
```

### `_handle_get_api_key()` — getApiKey roundtrip

```python
async def _handle_get_api_key(self, msg: dict) -> None:
    rid = msg["request_id"]
    try:
        if self._options.get_api_key is None:
            raise PiAgentError("get_api_key callback not provided")
        key = await self._options.get_api_key(msg["provider"])
        await self._transport.write({
            "type": "api_key_result", "request_id": rid, "key": key,
        })
    except Exception as e:
        await self._transport.write({
            "type": "api_key_result", "request_id": rid,
            "key": None, "error": str(e),
        })
```

### `prompt()` — async generator

```python
async def prompt(
    self, text: str, attachments: list | None = None
) -> AsyncIterator[dict]:
    rid = self._next_request_id()
    q: asyncio.Queue = asyncio.Queue()
    self._active_queues[rid] = q
    try:
        await self._transport.write({
            "type": "prompt", "request_id": rid,
            "text": text, "attachments": attachments or [],
        })
        while True:
            kind, payload = await q.get()
            if kind == "done":
                return
            elif kind == "error":
                raise PiBridgeError(payload)
            else:
                yield payload
    finally:
        self._active_queues.pop(rid, None)
```

> All coroutines use `asyncio.get_running_loop()` — never `asyncio.get_event_loop()`.
> **[ADVISOR]** `get_event_loop()` is deprecated in Python 3.10+ and behavior changes in 3.12.

### `wait_for_idle()`

```python
async def wait_for_idle(self) -> None:
    rid = self._next_request_id()
    q: asyncio.Queue = asyncio.Queue()
    self._active_queues[rid] = q
    try:
        await self._transport.write({"type": "wait_for_idle", "request_id": rid})
        kind, payload = await q.get()
        if kind == "error":
            raise PiBridgeError(payload)
    finally:
        self._active_queues.pop(rid, None)
```

### Full public API

```python
class PiAgent:
    # Lifecycle
    async def connect(self) -> None
    async def disconnect(self) -> None
    async def __aenter__(self) -> "PiAgent"
    async def __aexit__(self, *args) -> bool

    # Core turn methods — async generators
    async def prompt(self, text: str, attachments=None) -> AsyncIterator[dict]
    async def continue_(self) -> AsyncIterator[dict]

    # Agent control (fire-and-forget — all async for consistency)
    async def abort(self) -> None
    async def reset(self) -> None
    async def steer(self, message: str) -> None
    async def follow_up(self, message: str) -> None
    async def clear_steering_queue(self) -> None
    async def clear_follow_up_queue(self) -> None
    async def clear_all_queues(self) -> None

    # Await idle (essential for steer/follow-up workflows)
    async def wait_for_idle(self) -> None

    # State mutation (whitelisted fields only)
    async def set_model(self, model: ModelConfig) -> None
    async def set_system_prompt(self, prompt: str) -> None
    async def set_thinking_level(self, level: str) -> None
```

---

## Phase 7 — Public API (`src/pi_agent/__init__.py`)

```python
from .client import PiAgent
from .types import PiAgentOptions, ModelConfig, ToolDefinition, ToolResult, AgentState
from ._errors import PiAgentError, PiBridgeError, PiConnectionError, PiToolError

__all__ = [
    "PiAgent", "PiAgentOptions", "ModelConfig",
    "ToolDefinition", "ToolResult", "AgentState",
    "PiAgentError", "PiBridgeError", "PiConnectionError", "PiToolError",
]
```

---

## Phase 8 — Packaging (`pyproject.toml`)

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "pi-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[tool.hatch.build.targets.wheel]
packages = ["src/pi_agent"]
```

### Platform wheel tags

| Platform | Wheel tag | Notes |
|----------|-----------|-------|
| macOS arm64 | `macosx_11_0_arm64` | Bun requires macOS 11 minimum |
| macOS x64 | `macosx_11_0_x86_64` | Use 11.0, not 10.9 — verify Bun min |
| Linux x86_64 | `manylinux2014_x86_64` | Verify Bun's actual glibc requirement |
| Linux arm64 | `manylinux2014_aarch64` | Same glibc caveat |
| Windows x64 | `win_amd64` | |

### `scripts/build_wheel.py`

1. Accept `--platform` and `--binary` args
2. Copy binary into `src/pi_agent/bin/`
3. **[ADVISOR]** `chmod 0o755` on the copy before archiving (Python's zipfile preserves permissions if set at archive time)
4. Run `hatch build --target wheel` with correct platform tag
5. Clean up `src/pi_agent/bin/` after build

---

## Phase 9 — Examples

### `examples/simple_chat.py`

```python
import asyncio
from pi_agent import PiAgent, PiAgentOptions, ModelConfig

async def main():
    async with PiAgent(PiAgentOptions(
        system_prompt="You are a helpful assistant.",
        model=ModelConfig(provider="anthropic", name="claude-sonnet-4-20250514"),
    )) as agent:
        async for event in agent.prompt("What is the capital of France?"):
            if event.get("type") == "message_update":
                print(event.get("delta", ""), end="", flush=True)
    print()

asyncio.run(main())
```

### `examples/tools_example.py`

```python
import asyncio
from pi_agent import PiAgent, PiAgentOptions, ModelConfig, ToolDefinition

async def read_file(params: dict) -> dict:
    with open(params["path"]) as f:
        return {"content": [{"type": "text", "text": f.read()}]}

async def main():
    async with PiAgent(PiAgentOptions(
        system_prompt="You are a coding assistant.",
        model=ModelConfig(provider="openai", name="gpt-4o"),
        tools=[ToolDefinition(
            name="read_file",
            description="Read a file's contents",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            execute=read_file,
        )],
    )) as agent:
        async for event in agent.prompt("Read README.md and summarize it."):
            if event.get("type") == "message_update":
                print(event.get("delta", ""), end="", flush=True)
    print()

asyncio.run(main())
```

### `examples/get_api_key.py`

```python
import asyncio, os
from pi_agent import PiAgent, PiAgentOptions, ModelConfig

async def my_get_api_key(provider: str) -> str:
    # Fetch from secret manager, vault, etc.
    return os.environ[f"{provider.upper()}_API_KEY"]

async def main():
    async with PiAgent(PiAgentOptions(
        system_prompt="You are helpful.",
        model=ModelConfig(provider="anthropic", name="claude-sonnet-4-20250514"),
        get_api_key=my_get_api_key,
    )) as agent:
        async for event in agent.prompt("Hello!"):
            if event.get("type") == "message_update":
                print(event.get("delta", ""), end="", flush=True)
    print()

asyncio.run(main())
```

---

## Phase 10 — Tests

- `test_transport.py`: spawn bridge, send `initialize`, assert `ready` has `protocol_version: 1`
- `test_client.py`: full `async with PiAgent(...)` round-trip (skipped if no API key)

---

## Implementation Order

| # | Step | Files | Notes |
|---|------|-------|-------|
| 1 | Bridge source | `bridge/bridge.ts` | Serial handler, request_id, protocol version, getApiKey, waitForIdle, field whitelist, clean shutdown |
| 2 | Build script | `bridge/build.ts` | `--current` flag; log binary sizes; warn if > 95 MB |
| 3 | Install deps | `cd bridge && bun install` | Requires Bun |
| 4 | Build binary | `bun run bridge/build.ts --current` | Verify size < 100 MB |
| 5 | Python types | `src/pi_agent/types.py` | Includes `get_api_key` field |
| 6 | Python errors | `src/pi_agent/_errors.py` | |
| 7 | Python transport | `src/pi_agent/_transport.py` | Detection, stderr drainer, executable bit fix |
| 8 | Python client | `src/pi_agent/client.py` | request_id map, reader loop, tool + apikey handlers |
| 9 | Public API | `src/pi_agent/__init__.py` | |
| 10 | Packaging | `pyproject.toml`, `scripts/build_wheel.py` | Executable bit in wheel |
| 11 | Examples | `examples/` | Including get_api_key example |
| 12 | Tests | `tests/` | |

---

## Design Decisions (v3 — locked)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Transport | Binary primary, bun-run dev fallback | Zero runtime dep for end users; DX for contributors |
| Node.js fallback | Dropped in v1 | May use Bun-specific APIs; silent failures worse than no fallback |
| readline serialization | Serial promise chain | Prevents concurrent agent.prompt() on same instance |
| Protocol | JSON lines + `request_id` on every message | Correct error attribution; supports multiple concurrent generators |
| Protocol version | `ready` carries `protocol_version: 1` | Catches binary/package skew at connect time |
| stderr | Piped + drained to Python stderr | Prevents 64 KB pipe buffer deadlock; surfaces bridge crashes |
| Executable bit | `_ensure_executable()` at detection + `chmod` in build_wheel.py | Wheels strip Unix permissions |
| tool_result guarantee | `try/except` in `_handle_tool_call` always sends | Prevents bridge JS Promise from hanging indefinitely |
| getApiKey | Roundtrip in v1 (same pattern as tool_call) | Essential for secret managers, vaults, dynamic keys |
| waitForIdle | `async def wait_for_idle()` | Essential for steer/follow-up workflows |
| set_state | Whitelisted fields only | Prevents mutation of readonly SDK state |
| asyncio API | `asyncio.get_running_loop()` throughout | `get_event_loop()` deprecated Python 3.10+; broken 3.12+ |
| npm versions | Pinned + committed `bun.lockb` | Reproducible binary builds |

---

## Out of Scope (v1)

- Node.js fallback (v2, after explicit testing)
- `beforeToolCall` / `afterToolCall` hooks (v2)
- `transformContext` callback (v2)
- Sync API (v2)
- Session persistence
- musl/Alpine support — `PiConnectionError` with clear message if detected
- `win_arm64` wheel (v2)
