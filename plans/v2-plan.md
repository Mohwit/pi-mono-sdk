# v2 Features Implementation Plan

**Branch**: `v2-features`  
**Context**: 7 features excluded from v1 are now being implemented. All build on the established v1 patterns: serial chain, bypass-on-result, active_queues, and asyncio.create_task roundtrips.

---

## Key v1 Invariants Every Change Must Respect

1. **Serial chain deadlock rule** — any result message that resolves a Promise suspended *inside* `agent.prompt()` (i.e. inside the serial chain) MUST bypass the chain. Pattern in `rl.on("line")`: check `parsed.type`, call `resolveXxx(parsed)`, `return` before `chain.then(...)`.

2. **Handlers always reply** — `_handle_X()` methods in Python must catch all exceptions and always send a reply message. A swallowed exception leaves the bridge Promise pending forever.

3. **`SyncPiAgent` owns its loop** — uses `asyncio.new_event_loop()`, never `asyncio.run()`. `__exit__` must always call `loop.close()` in a `finally` block.

4. **`state_result` carries payload** — unlike `("done", None)` for `prompt_done`/`idle`, `_read_loop` puts `("done", full_msg_dict)` for `state_result` so `get_state()` can unpack it.

---

## Implementation Order

| # | Feature | Bridge changes | Python changes | Protocol changes |
|---|---------|---------------|----------------|-----------------|
| 1 | musl/Alpine + win_arm64 | `build.ts` targets | `_transport.py` detection | None |
| 2 | Node.js fallback | `build.ts` bundle step | `_transport.py` detection | None |
| 3 | Sync API | None | new `_sync.py` | None |
| 4 | Session persistence | `bridge.ts` get_state case | `client.py` get/save/load | `get_state` / `state_result` |
| 5 | beforeToolCall hook | `bridge.ts` roundtrip | `client.py` handler | `before_tool_call` / `before_tool_call_result` |
| 6 | afterToolCall hook | `bridge.ts` roundtrip | `client.py` handler | `after_tool_call` / `after_tool_call_result` |
| 7 | transformContext callback | `bridge.ts` roundtrip | `client.py` handler | `transform_context` / `transform_context_result` |

---

## Feature 1 — musl/Alpine + win_arm64

### `bridge/build.ts`
Add to `ALL_TARGETS`:
```typescript
{ target: "bun-linux-x64-musl",   out: "pi-agent-bridge-linux-x64-musl"    },
{ target: "bun-linux-arm64-musl", out: "pi-agent-bridge-linux-arm64-musl"  },
{ target: "bun-windows-arm64",    out: "pi-agent-bridge-win32-arm64.exe"   },
```
Add to `CURRENT_TARGET_MAP`:
```typescript
"win32 arm64": "bun-windows-arm64",
```
(musl targets are not auto-detected in `--current`; musl devs run the full build manually)

### `src/pi_agent/_transport.py`
Add musl detection function:
```python
def _is_musl() -> bool:
    if sys.platform != "linux":
        return False
    musl_paths = ["/lib/libc.musl-x86_64.so.1", "/lib/libc.musl-aarch64.so.1"]
    if any(Path(p).exists() for p in musl_paths):
        return True
    try:
        import subprocess
        r = subprocess.run(["ldd", "--version"], capture_output=True, text=True, timeout=2)
        return "musl" in (r.stdout + r.stderr).lower()
    except Exception:
        return False
```

Extend `_BINARY_MAP` (key type becomes `tuple`, supporting both 2-tuple and 3-tuple):
```python
_BINARY_MAP: dict[tuple, str] = {
    ("darwin",  "arm64"):              "pi-agent-bridge-darwin-arm64",
    ("darwin",  "x86_64"):            "pi-agent-bridge-darwin-x64",
    ("linux",   "x86_64"):            "pi-agent-bridge-linux-x64",
    ("linux",   "aarch64"):           "pi-agent-bridge-linux-arm64",
    ("linux",   "x86_64",  "musl"):   "pi-agent-bridge-linux-x64-musl",
    ("linux",   "aarch64", "musl"):   "pi-agent-bridge-linux-arm64-musl",
    ("win32",   "AMD64"):             "pi-agent-bridge-win32-x64.exe",
    ("win32",   "ARM64"):             "pi-agent-bridge-win32-arm64.exe",
}
```

Update `_detect_command()` — try musl 3-tuple key first on Linux:
```python
if plat == "linux" and _is_musl():
    name = _BINARY_MAP.get((plat, arch, "musl"))
else:
    name = _BINARY_MAP.get((plat, arch))
```

---

## Feature 2 — Node.js Fallback

### `bridge/build.ts`
Add path constants (near existing ones):
```typescript
const DIST_DIR = resolve(REPO_ROOT, "dist");
const JS_OUT   = resolve(DIST_DIR, "bridge.js");
```

Add function after existing `build()`:
```typescript
async function buildNodeBundle(): Promise<void> {
  console.log(`\nBundling Node.js fallback → ${JS_OUT} …`);
  if (!existsSync(DIST_DIR)) mkdirSync(DIST_DIR, { recursive: true });
  await $`bun build --target=node --minify ${ENTRY} --outfile ${JS_OUT}`;
  if (!existsSync(JS_OUT)) throw new Error(`Node bundle not produced: ${JS_OUT}`);
  console.log(`  Node bundle: ${formatSize(statSync(JS_OUT).size)}`);
}
```

Call at end of main block (always, regardless of `--current`):
```typescript
try { await buildNodeBundle(); }
catch (err: any) { console.error(`  Node bundle FAILED: ${err?.message}`); failed++; }
```

### `src/pi_agent/_transport.py`
Add constants:
```python
_PACKAGE_DIST_DIR = Path(__file__).parent / "dist"
_REPO_DIST_DIR    = Path(__file__).parent.parent.parent / "dist"
_NODE_BUNDLE_NAME = "bridge.js"
```

In `_detect_command()`, after bun fallback, before the `raise`:
```python
node = shutil.which("node")
if node:
    for dist_dir in (_PACKAGE_DIST_DIR, _REPO_DIST_DIR):
        bundle = dist_dir / _NODE_BUNDLE_NAME
        if bundle.exists() and bundle.stat().st_size > 0:
            return [node, str(bundle)]
```

Update error message to mention Node.js as option 4.

---

## Feature 3 — Sync API

### `src/pi_agent/_sync.py` (new file)
```python
class SyncPiAgent:
    def __init__(self, options: PiAgentOptions) -> None: ...
    def __enter__(self) -> "SyncPiAgent":
        self._loop = asyncio.new_event_loop()
        self._agent = PiAgent(self._options)
        self._loop.run_until_complete(self._agent.connect())
        return self
    def __exit__(self, *args) -> bool:
        try:
            self._loop.run_until_complete(self._agent.disconnect())
        finally:
            self._loop.close()
        return False
    def _run(self, coro): return self._loop.run_until_complete(coro)
    def prompt(self, text, attachments=None) -> list[dict]:
        async def _collect(): return [e async for e in self._agent.prompt(text, attachments)]
        return self._run(_collect())
    def continue_(self) -> list[dict]: ...  # same collect pattern
    def wait_for_idle(self) -> None: ...
    def abort(self) -> None: ...
    def reset(self) -> None: ...
    def steer(self, message: str) -> None: ...
    def follow_up(self, message: str) -> None: ...
    def clear_steering_queue(self) -> None: ...
    def clear_follow_up_queue(self) -> None: ...
    def clear_all_queues(self) -> None: ...
    def set_model(self, model) -> None: ...
    def set_system_prompt(self, prompt: str) -> None: ...
    def set_thinking_level(self, level: str) -> None: ...
    # Session methods added after Feature 4:
    def get_state(self) -> dict: ...
    def save_session(self, path: str) -> None: ...
```

### `src/pi_agent/__init__.py`
Add: `from ._sync import SyncPiAgent` and `"SyncPiAgent"` to `__all__`.

---

## Feature 4 — Session Persistence

### `bridge/bridge.ts`
Add `get_state` case in `handleLine` switch (goes through serial chain — safe):
```typescript
case "get_state": {
  const state = agent!.state as any;
  send({
    type: "state_result",
    request_id,
    messages:      state.messages ?? [],
    systemPrompt:  state.systemPrompt ?? "",
    model:         state.model,
    thinkingLevel: state.thinkingLevel ?? null,
  });
  break;
}
```

### `src/pi_agent/client.py`

In `_read_loop`, add branch (routes like `idle` but carries payload):
```python
elif t == "state_result":
    q = self._active_queues.get(rid)
    if q:
        await q.put(("done", msg))   # NOTE: payload is the full msg dict, not None
```

Add `get_state()` method (uses active_queues pattern):
```python
async def get_state(self) -> dict:
    rid = _next_request_id()
    q: asyncio.Queue = asyncio.Queue()
    self._active_queues[rid] = q
    try:
        await self._transport.write({"type": "get_state", "request_id": rid})
        kind, payload = await q.get()
        if kind == "error": raise PiBridgeError(payload)
        return {
            "messages":      payload.get("messages", []),
            "systemPrompt":  payload.get("systemPrompt", ""),
            "model":         payload.get("model"),
            "thinkingLevel": payload.get("thinkingLevel"),
        }
    finally:
        self._active_queues.pop(rid, None)
```

Add `save_session()` method:
```python
async def save_session(self, path: str) -> None:
    import json as _json
    state = await self.get_state()
    with open(path, "w", encoding="utf-8") as f:
        _json.dump({"messages": state["messages"]}, f, ensure_ascii=False, indent=2)
```

Add module-level `load_session()` function:
```python
def load_session(path: str) -> list[dict]:
    import json as _json
    with open(path, encoding="utf-8") as f:
        return _json.load(f).get("messages", [])
```

### `src/pi_agent/__init__.py`
Add: `from .client import PiAgent, load_session` and `"load_session"` to `__all__`.

### `src/pi_agent/_sync.py`
Add sync wrappers for `get_state()` and `save_session()`.

**Protocol**:
- Python→Bridge: `{"type": "get_state", "request_id": "req_N"}`
- Bridge→Python: `{"type": "state_result", "request_id": "req_N", "messages": [...], "systemPrompt": "...", "model": {...}, "thinkingLevel": null}`

---

## Feature 5 — beforeToolCall Hook

### `src/pi_agent/types.py`
Add to `PiAgentOptions` after `get_api_key`:
```python
before_tool_call: Callable[[dict], Awaitable[dict | None]] | None = None
# dict in:  {"id": str, "name": str, "params": dict}
# dict out: None (allow) | {"block": True, "reason": str}
```

### `src/pi_agent/client.py` — `_serialize_options()`
```python
"has_before_tool_call": o.before_tool_call is not None,
```

### `bridge/bridge.ts`
Add Map:
```typescript
const pendingBeforeTool = new Map<string, { resolve: (r: any) => void; reject: (e: Error) => void }>();
```

Add bypass resolver:
```typescript
function resolveBeforeToolResult(msg: any): void {
  const p = pendingBeforeTool.get(msg.id);
  if (!p) return;
  pendingBeforeTool.delete(msg.id);
  p.resolve(msg.block ? { block: true, reason: msg.reason ?? "" } : undefined);
}
```

Add bypass check in `rl.on("line")`:
```typescript
if (parsed.type === "before_tool_call_result") { resolveBeforeToolResult(parsed); return; }
```

Wire in `case "initialize"`:
```typescript
if (opts.has_before_tool_call) {
  agentOptions.beforeToolCall = async (ctx: any) =>
    new Promise((resolve, reject) => {
      pendingBeforeTool.set(ctx.toolCallId, { resolve, reject });
      send({ type: "before_tool_call", id: ctx.toolCallId, name: ctx.name, params: ctx.params });
    });
}
```

### `src/pi_agent/client.py`
Dispatch in `_read_loop`:
```python
elif t == "before_tool_call":
    asyncio.create_task(self._handle_before_tool_call(msg))
```

Handler (always replies — error case allows the tool call to proceed):
```python
async def _handle_before_tool_call(self, msg: dict) -> None:
    tool_id = msg["id"]
    try:
        result = await self._options.before_tool_call(
            {"id": msg["id"], "name": msg["name"], "params": msg["params"]}
        )
        if result and result.get("block"):
            await self._transport.write(
                {"type": "before_tool_call_result", "id": tool_id,
                 "block": True, "reason": result.get("reason", "")}
            )
        else:
            await self._transport.write(
                {"type": "before_tool_call_result", "id": tool_id, "block": False}
            )
    except Exception:
        await self._transport.write(
            {"type": "before_tool_call_result", "id": tool_id, "block": False}
        )
```

**Protocol**:
- Bridge→Python: `{"type": "before_tool_call", "id": "tc_1", "name": "read_file", "params": {...}}`
- Python→Bridge (allow): `{"type": "before_tool_call_result", "id": "tc_1", "block": false}`
- Python→Bridge (block): `{"type": "before_tool_call_result", "id": "tc_1", "block": true, "reason": "..."}`

---

## Feature 6 — afterToolCall Hook

Identical pattern to beforeToolCall. Different fields and return shape.

### `src/pi_agent/types.py`
```python
after_tool_call: Callable[[dict], Awaitable[dict | None]] | None = None
# dict in:  {"id": str, "name": str, "params": dict, "result": any}
# dict out: None | {"terminate": True} | {"details": dict}
```

### `src/pi_agent/client.py` — `_serialize_options()`
```python
"has_after_tool_call": o.after_tool_call is not None,
```

### `bridge/bridge.ts`
Add Map, resolver, bypass check, and `case "initialize"` wiring — same pattern as beforeToolCall.
Resolver:
```typescript
function resolveAfterToolResult(msg: any): void {
  const p = pendingAfterTool.get(msg.id);
  if (!p) return;
  pendingAfterTool.delete(msg.id);
  p.resolve(msg.terminate ? { terminate: true }
          : msg.details !== undefined ? { details: msg.details }
          : undefined);
}
```

Wire:
```typescript
if (opts.has_after_tool_call) {
  agentOptions.afterToolCall = async (ctx: any) =>
    new Promise((resolve, reject) => {
      pendingAfterTool.set(ctx.toolCallId, { resolve, reject });
      send({ type: "after_tool_call", id: ctx.toolCallId,
             name: ctx.name, params: ctx.params, result: ctx.result });
    });
}
```

### `src/pi_agent/client.py`
Handler (neutral reply on error — does not terminate):
```python
async def _handle_after_tool_call(self, msg: dict) -> None:
    tool_id = msg["id"]
    try:
        result = await self._options.after_tool_call(
            {"id": msg["id"], "name": msg["name"],
             "params": msg["params"], "result": msg.get("result")}
        )
        payload = {"type": "after_tool_call_result", "id": tool_id}
        if result and result.get("terminate"):
            payload["terminate"] = True
        elif result and "details" in result:
            payload["details"] = result["details"]
        await self._transport.write(payload)
    except Exception:
        await self._transport.write({"type": "after_tool_call_result", "id": tool_id})
```

**Protocol**:
- Bridge→Python: `{"type": "after_tool_call", "id": "tc_1", "name": "...", "params": {...}, "result": {...}}`
- Python→Bridge: `{"type": "after_tool_call_result", "id": "tc_1"}` (+ optional `terminate` or `details`)

---

## Feature 7 — transformContext Callback

Same bypass pattern but uses a bridge-generated `request_id` (tx_N) instead of toolCallId.

### `src/pi_agent/types.py`
```python
transform_context: Callable[[list[dict]], Awaitable[list[dict]]] | None = None
```

### `src/pi_agent/client.py` — `_serialize_options()`
```python
"has_transform_context": o.transform_context is not None,
```

### `bridge/bridge.ts`
Add Map + counter:
```typescript
const pendingTransform = new Map<string, { resolve: (m: any[]) => void; reject: (e: Error) => void }>();
let _txCounter = 0;
function nextTxId(): string { return `tx_${++_txCounter}`; }
```

Bypass resolver:
```typescript
function resolveTransformResult(msg: any): void {
  const p = pendingTransform.get(msg.request_id);
  if (!p) return;
  pendingTransform.delete(msg.request_id);
  msg.error ? p.reject(new Error(msg.error)) : p.resolve(msg.messages ?? []);
}
```

Bypass check:
```typescript
if (parsed.type === "transform_context_result") { resolveTransformResult(parsed); return; }
```

Wire (note: ignores AbortSignal — Python has no equivalent):
```typescript
if (opts.has_transform_context) {
  agentOptions.transformContext = async (messages: any[], _signal: AbortSignal) =>
    new Promise((resolve, reject) => {
      const txId = nextTxId();
      pendingTransform.set(txId, { resolve, reject });
      send({ type: "transform_context", request_id: txId, messages });
    });
}
```

### `src/pi_agent/client.py`
Dispatch: `elif t == "transform_context": asyncio.create_task(self._handle_transform_context(msg))`

Handler (on error: pass messages through unchanged):
```python
async def _handle_transform_context(self, msg: dict) -> None:
    tx_id = msg["request_id"]
    original = msg.get("messages", [])
    try:
        transformed = await self._options.transform_context(original)
        await self._transport.write(
            {"type": "transform_context_result", "request_id": tx_id, "messages": transformed}
        )
    except Exception as exc:
        await self._transport.write(
            {"type": "transform_context_result", "request_id": tx_id,
             "messages": original, "error": str(exc)}
        )
```

**Protocol**:
- Bridge→Python: `{"type": "transform_context", "request_id": "tx_1", "messages": [...]}`
- Python→Bridge: `{"type": "transform_context_result", "request_id": "tx_1", "messages": [...]}`

---

## All bypass checks in `rl.on("line")` (final state)
```typescript
if (parsed.type === "tool_result")              { resolveToolResult(parsed);       return; }
if (parsed.type === "api_key_result")           { resolveApiKeyResult(parsed);     return; }
if (parsed.type === "before_tool_call_result")  { resolveBeforeToolResult(parsed); return; }
if (parsed.type === "after_tool_call_result")   { resolveAfterToolResult(parsed);  return; }
if (parsed.type === "transform_context_result") { resolveTransformResult(parsed);  return; }
chain = chain.then(() => handleLine(parsed)).catch(() => {});
```

---

## Files Modified / Created

| File | Action |
|------|--------|
| `bridge/build.ts` | Add musl+win_arm64 targets; add Node.js bundle step |
| `bridge/bridge.ts` | Add get_state; add 3 hook roundtrips + 3 bypass resolvers |
| `src/pi_agent/types.py` | Add 3 fields to `PiAgentOptions` |
| `src/pi_agent/client.py` | Add 3 handlers, get_state, save_session, load_session; update _serialize_options, _read_loop |
| `src/pi_agent/_transport.py` | musl detection; extend _BINARY_MAP; Node.js fallback |
| `src/pi_agent/_sync.py` | **New** — SyncPiAgent class |
| `src/pi_agent/__init__.py` | Export SyncPiAgent, load_session |
