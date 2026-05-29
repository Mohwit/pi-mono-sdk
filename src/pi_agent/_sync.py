from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any

from .client import PiAgent
from .types import ModelConfig, PiAgentOptions


class SyncPiAgent:
    """
    Synchronous wrapper around PiAgent.

    Runs a dedicated asyncio event loop in a background thread so the loop is
    *always* running (never "stopped" between calls). This prevents subprocess
    I/O from stalling on macOS when `run_until_complete` is called in bursts.

    Usage::

        with SyncPiAgent(PiAgentOptions(
            system_prompt="You are helpful.",
            model=ModelConfig(provider="anthropic", name="claude-sonnet-4-20250514"),
        )) as agent:
            events = agent.prompt("Hello!")
            for event in events:
                if event.get("type") == "message_update":
                    ae = event.get("assistantMessageEvent", {})
                    if ae.get("type") == "text_delta":
                        print(ae["delta"], end="", flush=True)
    """

    def __init__(self, options: PiAgentOptions) -> None:
        self._options = options
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._agent: PiAgent | None = None

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    def __enter__(self) -> "SyncPiAgent":
        self._loop  = asyncio.new_event_loop()
        self._agent = PiAgent(self._options)

        # Run the event loop forever in a background thread so background tasks
        # (reader loop, stderr drain) never get suspended between API calls.
        def _run_forever() -> None:
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()  # type: ignore[union-attr]

        self._thread = threading.Thread(target=_run_forever, daemon=True)
        self._thread.start()

        # Connect on the background loop (blocks until bridge is ready)
        future: Future = asyncio.run_coroutine_threadsafe(
            self._agent.connect(), self._loop
        )
        future.result(timeout=30)
        return self

    def __exit__(self, *args: object) -> bool:
        assert self._loop is not None
        assert self._agent is not None
        assert self._thread is not None
        try:
            future: Future = asyncio.run_coroutine_threadsafe(
                self._agent.disconnect(), self._loop
            )
            future.result(timeout=10)
        except Exception:
            pass
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            self._loop.close()
            self._loop   = None
            self._agent  = None
            self._thread = None
        return False

    def _run(self, coro: Any) -> Any:
        """Submit a coroutine to the background loop and block until it finishes."""
        assert self._loop is not None
        future: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    # ─── Core turn methods ────────────────────────────────────────────────────

    def prompt(self, text: str, attachments: list | None = None) -> list[dict]:
        """Send a user message and return all agent events as a list."""
        assert self._agent is not None

        async def _collect() -> list[dict]:
            return [e async for e in self._agent.prompt(text, attachments)]  # type: ignore[union-attr]

        return self._run(_collect())

    def continue_(self) -> list[dict]:
        """Resume from existing context without a new user message."""
        assert self._agent is not None

        async def _collect() -> list[dict]:
            return [e async for e in self._agent.continue_()]  # type: ignore[union-attr]

        return self._run(_collect())

    def wait_for_idle(self) -> None:
        assert self._agent is not None
        self._run(self._agent.wait_for_idle())

    # ─── Fire-and-forget controls ─────────────────────────────────────────────

    def abort(self) -> None:
        assert self._agent is not None
        self._run(self._agent.abort())

    def reset(self) -> None:
        assert self._agent is not None
        self._run(self._agent.reset())

    def steer(self, message: str) -> None:
        assert self._agent is not None
        self._run(self._agent.steer(message))

    def follow_up(self, message: str) -> None:
        assert self._agent is not None
        self._run(self._agent.follow_up(message))

    def clear_steering_queue(self) -> None:
        assert self._agent is not None
        self._run(self._agent.clear_steering_queue())

    def clear_follow_up_queue(self) -> None:
        assert self._agent is not None
        self._run(self._agent.clear_follow_up_queue())

    def clear_all_queues(self) -> None:
        assert self._agent is not None
        self._run(self._agent.clear_all_queues())

    # ─── State mutation ───────────────────────────────────────────────────────

    def set_model(self, model: ModelConfig) -> None:
        assert self._agent is not None
        self._run(self._agent.set_model(model))

    def set_system_prompt(self, prompt: str) -> None:
        assert self._agent is not None
        self._run(self._agent.set_system_prompt(prompt))

    def set_thinking_level(self, level: str) -> None:
        assert self._agent is not None
        self._run(self._agent.set_thinking_level(level))

    # ─── Session persistence ──────────────────────────────────────────────────

    def get_state(self) -> dict:
        assert self._agent is not None
        return self._run(self._agent.get_state())

    def save_session(self, path: str) -> None:
        assert self._agent is not None
        self._run(self._agent.save_session(path))
