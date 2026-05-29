from __future__ import annotations

import asyncio
import itertools
from typing import AsyncIterator

from ._errors import PiAgentError, PiBridgeError, PiConnectionError, PiToolError
from ._transport import SubprocessTransport
from .types import (
    LocalModelConfig,
    ModelConfig,
    PiAgentOptions,
    ToolDefinition,
    ToolResult,
)

PROTOCOL_VERSION = 1

_id_counter = itertools.count(1)


def _next_request_id() -> str:
    return f"req_{next(_id_counter)}"


class PiAgent:
    """
    Python interface to the pi-agent-core TypeScript SDK.

    Spawns a bridge subprocess and communicates via JSON lines over stdio.

    Usage::

        async with PiAgent(PiAgentOptions(
            system_prompt="You are helpful.",
            model=ModelConfig(provider="anthropic", name="claude-sonnet-4-20250514"),
        )) as agent:
            async for event in agent.prompt("Hello!"):
                if event.get("type") == "message_update":
                    print(event.get("delta", ""), end="", flush=True)
    """

    def __init__(self, options: PiAgentOptions) -> None:
        self._options = options
        self._transport: SubprocessTransport | None = None
        self._reader_task: asyncio.Task | None = None
        self._ready_event: asyncio.Event | None = None
        # Maps request_id → asyncio.Queue[(kind, payload)]
        self._active_queues: dict[str, asyncio.Queue] = {}

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._transport = SubprocessTransport()
        await self._transport.connect()

        self._ready_event = asyncio.Event()
        self._reader_task = asyncio.create_task(self._read_loop())

        await self._transport.write(
            {
                "type": "initialize",
                "request_id": "init",
                "options": self._serialize_options(),
            }
        )

        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            raise PiConnectionError(
                "Bridge did not send 'ready' within 30 seconds. "
                "Check [pi-bridge] lines in stderr for details."
            )

    async def disconnect(self) -> None:
        if self._transport:
            await self._transport.disconnect()
            self._transport = None
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            self._reader_task = None

    async def __aenter__(self) -> "PiAgent":
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> bool:
        await self.disconnect()
        return False

    # ─── Options serialization ────────────────────────────────────────────────

    def _serialize_options(self) -> dict:
        o = self._options
        if isinstance(o.model, LocalModelConfig):
            m = o.model
            serialized_model = {
                "api": m.api,
                "id": m.id,
                "name": m.name or m.id,
                "provider": m.provider,
                "baseUrl": m.base_url,
                "reasoning": m.reasoning,
                "input": m.input,
                "cost": m.cost,
                "contextWindow": m.context_window,
                "maxTokens": m.max_tokens,
            }
        else:
            serialized_model = {"provider": o.model.provider, "name": o.model.name}
        return {
            "systemPrompt": o.system_prompt,
            "model": serialized_model,
            "thinkingLevel": o.thinking_level,
            "tools": [
                {
                    "name": t.name,
                    "label": t.label,
                    "description": t.description,
                    "parameters": t.parameters,
                    "execution_mode": t.execution_mode,
                }
                for t in o.tools
            ],
            "messages": o.messages,
            "toolExecution": o.tool_execution,
            "steeringMode": o.steering_mode,
            "followUpMode": o.follow_up_mode,
            # Tell bridge whether to set up getApiKey roundtrip
            "has_get_api_key": o.get_api_key is not None,
            # v2 hook flags
            "has_before_tool_call": o.before_tool_call is not None,
            "has_after_tool_call": o.after_tool_call is not None,
            "has_transform_context": o.transform_context is not None,
        }

    def _find_tool(self, name: str) -> ToolDefinition | None:
        for t in self._options.tools:
            if t.name == name:
                return t
        return None

    # ─── Background reader loop ───────────────────────────────────────────────

    async def _read_loop(self) -> None:
        assert self._transport is not None
        try:
            async for msg in self._transport.iter_lines():
                t = msg.get("type")
                rid = msg.get("request_id")

                if t == "ready":
                    version = msg.get("protocol_version", 0)
                    if version != PROTOCOL_VERSION:
                        raise PiBridgeError(
                            f"Protocol version mismatch: "
                            f"expected {PROTOCOL_VERSION}, got {version}"
                        )
                    assert self._ready_event is not None
                    self._ready_event.set()

                elif t == "event":
                    q = self._active_queues.get(rid)
                    if q:
                        await q.put(("event", msg["event"]))

                elif t in ("prompt_done", "continue_done", "idle"):
                    q = self._active_queues.get(rid)
                    if q:
                        await q.put(("done", None))

                elif t == "state_result":
                    q = self._active_queues.get(rid)
                    if q:
                        # carry the full message so get_state() can unpack it
                        await q.put(("done", msg))

                elif t == "tool_call":
                    asyncio.create_task(self._handle_tool_call(msg))

                elif t == "get_api_key":
                    asyncio.create_task(self._handle_get_api_key(msg))

                elif t == "before_tool_call":
                    asyncio.create_task(self._handle_before_tool_call(msg))

                elif t == "after_tool_call":
                    asyncio.create_task(self._handle_after_tool_call(msg))

                elif t == "transform_context":
                    asyncio.create_task(self._handle_transform_context(msg))

                elif t == "error":
                    q = self._active_queues.get(rid)
                    if q:
                        await q.put(
                            ("error", msg.get("message", "Unknown bridge error"))
                        )

        except asyncio.CancelledError:
            pass
        finally:
            # Bridge died — drain all waiting coroutines with an error sentinel
            for q in self._active_queues.values():
                await q.put(("error", "Bridge process exited unexpectedly"))
            self._active_queues.clear()

    # ─── Tool call roundtrip ──────────────────────────────────────────────────

    async def _handle_tool_call(self, msg: dict) -> None:
        """Execute a Python tool and send the result back to the bridge.

        Always sends tool_result — never leaves the bridge JS Promise pending.
        """
        assert self._transport is not None
        tool_id = msg["id"]
        try:
            tool = self._find_tool(msg["name"])
            if tool is None:
                raise PiToolError(f"Unknown tool: {msg['name']!r}")
            result = await tool.execute(msg["params"])
            if isinstance(result, ToolResult):
                result = {"content": result.content, "details": result.details}
            await self._transport.write(
                {
                    "type": "tool_result",
                    "id": tool_id,
                    "result": result,
                    "is_error": False,
                }
            )
        except Exception as exc:
            await self._transport.write(
                {
                    "type": "tool_result",
                    "id": tool_id,
                    "result": {"content": [{"type": "text", "text": str(exc)}]},
                    "is_error": True,
                }
            )

    # ─── getApiKey roundtrip ──────────────────────────────────────────────────

    async def _handle_get_api_key(self, msg: dict) -> None:
        """Call the user's get_api_key callback and send the result to the bridge."""
        assert self._transport is not None
        rid = msg["request_id"]
        try:
            if self._options.get_api_key is None:
                raise PiAgentError("get_api_key callback not provided")
            key = await self._options.get_api_key(msg["provider"])
            await self._transport.write(
                {
                    "type": "api_key_result",
                    "request_id": rid,
                    "key": key,
                }
            )
        except Exception as exc:
            await self._transport.write(
                {
                    "type": "api_key_result",
                    "request_id": rid,
                    "key": None,
                    "error": str(exc),
                }
            )

    # ─── beforeToolCall roundtrip ─────────────────────────────────────────────

    async def _handle_before_tool_call(self, msg: dict) -> None:
        """Ask the before_tool_call callback whether to allow/block the tool call.

        Always sends a reply — on error, allows the call to proceed.
        """
        assert self._transport is not None
        tool_id = msg["id"]
        try:
            result = await self._options.before_tool_call(  # type: ignore[misc]
                {"id": msg["id"], "name": msg["name"], "params": msg["params"]}
            )
            if result and result.get("block"):
                await self._transport.write(
                    {
                        "type": "before_tool_call_result",
                        "id": tool_id,
                        "block": True,
                        "reason": result.get("reason", ""),
                    }
                )
            else:
                await self._transport.write(
                    {
                        "type": "before_tool_call_result",
                        "id": tool_id,
                        "block": False,
                    }
                )
        except Exception:
            await self._transport.write(
                {
                    "type": "before_tool_call_result",
                    "id": tool_id,
                    "block": False,
                }
            )

    # ─── afterToolCall roundtrip ──────────────────────────────────────────────

    async def _handle_after_tool_call(self, msg: dict) -> None:
        """Notify the after_tool_call callback of a completed tool execution.

        Always sends a reply — on error, sends a neutral reply (no terminate/details).
        """
        assert self._transport is not None
        tool_id = msg["id"]
        try:
            result = await self._options.after_tool_call(  # type: ignore[misc]
                {
                    "id":       msg["id"],
                    "name":     msg["name"],
                    "params":   msg["params"],
                    "result":   msg.get("result"),
                    "is_error": msg.get("is_error", False),
                }
            )
            payload: dict = {"type": "after_tool_call_result", "id": tool_id}
            if result and result.get("terminate"):
                payload["terminate"] = True
            elif result and "details" in result:
                payload["details"] = result["details"]
            await self._transport.write(payload)
        except Exception:
            await self._transport.write(
                {"type": "after_tool_call_result", "id": tool_id}
            )

    # ─── transformContext roundtrip ───────────────────────────────────────────

    async def _handle_transform_context(self, msg: dict) -> None:
        """Pass the message context through the transform_context callback.

        On error, passes the original messages through unchanged.
        """
        assert self._transport is not None
        tx_id = msg["request_id"]
        original = msg.get("messages", [])
        try:
            transformed = await self._options.transform_context(original)  # type: ignore[misc]
            await self._transport.write(
                {
                    "type": "transform_context_result",
                    "request_id": tx_id,
                    "messages": transformed,
                }
            )
        except Exception as exc:
            await self._transport.write(
                {
                    "type": "transform_context_result",
                    "request_id": tx_id,
                    "messages": original,
                    "error": str(exc),
                }
            )

    # ─── Core turn methods ────────────────────────────────────────────────────

    async def prompt(
        self,
        text: str,
        attachments: list | None = None,
    ) -> AsyncIterator[dict]:
        """Send a user message and yield agent events until the turn ends."""
        assert self._transport is not None
        rid = _next_request_id()
        q: asyncio.Queue = asyncio.Queue()
        self._active_queues[rid] = q
        try:
            await self._transport.write(
                {
                    "type": "prompt",
                    "request_id": rid,
                    "text": text,
                    "attachments": attachments or [],
                }
            )
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

    async def continue_(self) -> AsyncIterator[dict]:
        """Resume from existing context without a new user message."""
        assert self._transport is not None
        rid = _next_request_id()
        q: asyncio.Queue = asyncio.Queue()
        self._active_queues[rid] = q
        try:
            await self._transport.write({"type": "continue", "request_id": rid})
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

    # ─── Await idle ───────────────────────────────────────────────────────────

    async def wait_for_idle(self) -> None:
        """Block until the agent is not streaming (essential for steer/follow-up workflows)."""
        assert self._transport is not None
        rid = _next_request_id()
        q: asyncio.Queue = asyncio.Queue()
        self._active_queues[rid] = q
        try:
            await self._transport.write({"type": "wait_for_idle", "request_id": rid})
            kind, payload = await q.get()
            if kind == "error":
                raise PiBridgeError(payload)
        finally:
            self._active_queues.pop(rid, None)

    # ─── Fire-and-forget controls ─────────────────────────────────────────────

    async def abort(self) -> None:
        assert self._transport is not None
        await self._transport.write({"type": "abort", "request_id": _next_request_id()})

    async def reset(self) -> None:
        assert self._transport is not None
        await self._transport.write({"type": "reset", "request_id": _next_request_id()})

    async def steer(self, message: str) -> None:
        assert self._transport is not None
        await self._transport.write(
            {
                "type": "steer",
                "request_id": _next_request_id(),
                "message": message,
            }
        )

    async def follow_up(self, message: str) -> None:
        assert self._transport is not None
        await self._transport.write(
            {
                "type": "follow_up",
                "request_id": _next_request_id(),
                "message": message,
            }
        )

    async def clear_steering_queue(self) -> None:
        assert self._transport is not None
        await self._transport.write(
            {
                "type": "clear_steering",
                "request_id": _next_request_id(),
            }
        )

    async def clear_follow_up_queue(self) -> None:
        assert self._transport is not None
        await self._transport.write(
            {
                "type": "clear_follow_up",
                "request_id": _next_request_id(),
            }
        )

    async def clear_all_queues(self) -> None:
        assert self._transport is not None
        await self._transport.write(
            {
                "type": "clear_all",
                "request_id": _next_request_id(),
            }
        )

    # ─── State mutation ───────────────────────────────────────────────────────

    async def set_model(self, model: ModelConfig) -> None:
        assert self._transport is not None
        await self._transport.write(
            {
                "type": "set_state",
                "request_id": _next_request_id(),
                "field": "model",
                "value": {"provider": model.provider, "name": model.name},
            }
        )

    async def set_system_prompt(self, prompt: str) -> None:
        assert self._transport is not None
        await self._transport.write(
            {
                "type": "set_state",
                "request_id": _next_request_id(),
                "field": "systemPrompt",
                "value": prompt,
            }
        )

    async def set_thinking_level(self, level: str) -> None:
        assert self._transport is not None
        await self._transport.write(
            {
                "type": "set_state",
                "request_id": _next_request_id(),
                "field": "thinkingLevel",
                "value": level,
            }
        )

    # ─── Session persistence ──────────────────────────────────────────────────

    async def get_state(self) -> dict:
        """Return current agent state (messages, model, systemPrompt, thinkingLevel)."""
        assert self._transport is not None
        rid = _next_request_id()
        q: asyncio.Queue = asyncio.Queue()
        self._active_queues[rid] = q
        try:
            await self._transport.write({"type": "get_state", "request_id": rid})
            kind, payload = await q.get()
            if kind == "error":
                raise PiBridgeError(payload)
            return {
                "messages": payload.get("messages", []),
                "systemPrompt": payload.get("systemPrompt", ""),
                "model": payload.get("model"),
                "thinkingLevel": payload.get("thinkingLevel"),
            }
        finally:
            self._active_queues.pop(rid, None)

    async def save_session(self, path: str) -> None:
        """Save the current conversation to a JSON file for later resumption."""
        import json as _json

        state = await self.get_state()
        with open(path, "w", encoding="utf-8") as f:
            _json.dump({"messages": state["messages"]}, f, ensure_ascii=False, indent=2)


def load_session(path: str) -> list[dict]:
    """Load messages from a previously saved session file."""
    import json as _json

    with open(path, encoding="utf-8") as f:
        return _json.load(f).get("messages", [])
