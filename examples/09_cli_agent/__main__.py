"""
pi-agent CLI Coding Agent

Full-screen TUI similar to Claude Code:
  · Scrollable output pane above a fixed input bar
  · Esc aborts the current AI response
  · Typing while AI responds auto-queues a follow-up
  · Auto-saves session after every turn

Usage:
    python cli_agent/
    python cli_agent/ --model anthropic/claude-sonnet-4-6
    python cli_agent/ --session /path/to/session.json
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path

from pi_agent import PiAgent, PiAgentOptions, LocalModelConfig, ModelConfig, load_session

try:
    from .tools import TOOLS, TOOL_NAMES
except ImportError:
    from tools import TOOLS, TOOL_NAMES  # type: ignore[no-redef]

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout.containers import ConditionalContainer, Float, FloatContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style


# ─── slash-command catalogue ───────────────────────────────────────────────────

COMMAND_DEFS: list[tuple[str, str]] = [
    ("reset",          "Clear conversation history"),
    ("abort",          "Cancel the current response  (also Esc)"),
    ("steer",          "Inject instruction mid-turn:  /steer <msg>"),
    ("followup",       "Queue a follow-up message:  /followup <msg>"),
    ("clear-steering", "Clear the steering queue"),
    ("clear-followup", "Clear the follow-up queue"),
    ("clear-queues",   "Clear all queues"),
    ("system",         "Replace system prompt:  /system <text>"),
    ("model",          "Swap model:  /model <spec>"),
    ("thinking",       "Thinking level:  /thinking off|low|medium|high"),
    ("save",           "Save session:  /save [path]"),
    ("load",           "Load session:  /load <path>"),
    ("state",          "Show agent state (model, msgs, system prompt)"),
    ("tools",          "List available tools"),
    ("help",           "Show help"),
    ("exit",           "Exit"),
    ("quit",           "Exit"),
]


class SlashCompleter(Completer):
    """Autocomplete /command names when the line starts with /."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        typed = text[1:].lower()
        if " " in typed:          # already past the command name
            return
        for name, desc in COMMAND_DEFS:
            if name.startswith(typed):
                yield Completion(
                    "/" + name,
                    start_position=-len(text),
                    display=f"/{name}",
                    display_meta=desc,
                )


# ─── prompt_toolkit style ──────────────────────────────────────────────────────

STYLE = Style.from_dict({
    # ── output pane ──
    "ai-text":        "fg:#abb2bf",
    "ai-header":      "fg:#e5c07b bold",
    "user-header":    "fg:#61afef bold",
    "user-text":      "fg:#c8ccd4",
    "tool-border":    "fg:#3e4451",
    "tool-name":      "fg:#56b6c2 bold",
    "tool-ok":        "fg:#98c379",
    "tool-err":       "fg:#e06c75",
    "tool-meta":      "fg:#5c6370",
    "followup-hint":  "fg:#c678dd bold",
    "followup-bar":   "bg:#2d2a3e fg:#c678dd",
    "aborted":        "fg:#e5c07b",
    "system-ok":      "fg:#98c379",
    "system-warn":    "fg:#e5c07b",
    "system-err":     "fg:#e06c75",
    "system-info":    "fg:#5c6370",
    # ── bottom bar ──
    "separator":      "fg:#3e4451",
    "input-area":     "fg:#abb2bf bg:#21252b",
    "input-prompt":   "fg:#61afef bold",
    "status-bar":     "bg:#21252b fg:#5c6370",
    "sb-model-label": "fg:#61afef bold",
    "sb-model-value": "fg:#98c379",
    "sb-streaming":   "fg:#e5c07b bold",
    "sb-queued":      "fg:#c678dd",
    # ── completions ──
    "completion-menu.completion":              "bg:#282c34 fg:#abb2bf",
    "completion-menu.completion.current":      "bg:#3e4451 fg:#61afef bold",
    "completion-menu.meta.completion":         "bg:#21252b fg:#5c6370",
    "completion-menu.meta.completion.current": "bg:#3e4451 fg:#98c379",
})


# ─── model helpers ─────────────────────────────────────────────────────────────

def _make_local(model_id: str) -> LocalModelConfig:
    return LocalModelConfig(
        id=model_id, name=model_id,
        api="openai-completions", provider="local",
        base_url="http://localhost:11434/v1/",
    )


def _make_model(spec: str):
    if "/" in spec:
        provider, name = spec.split("/", 1)
        return ModelConfig(provider=provider, name=name)
    return _make_local(spec)


async def _get_api_key(provider: str) -> str:
    env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
           "google": "GOOGLE_API_KEY", "mistral": "MISTRAL_API_KEY"}
    if provider in env:
        return os.environ.get(env[provider], "")
    return "ollama"


# ─── constants ─────────────────────────────────────────────────────────────────

DEFAULT_MODEL   = "llama3.1"
DEFAULT_SESSION = Path(".pi_agent_session.json")

def _build_system_prompt() -> str:
    cwd = os.getcwd()
    return f"""\
You are an expert software engineering assistant — a coding agent similar to GitHub Copilot \
Workspace or Claude Code. You help developers write, debug, refactor, and understand code.

## Environment
- Working directory: {cwd}
- OS: {os.uname().sysname if hasattr(os, "uname") else "unknown"}
- Shell tools and file system tools are available to you (see tool list)

## Decision rules — when to use tools vs. when to reply directly

USE tools when the task requires it:
  · "read / show / open <file>"            → read_file
  · "list files / what's in this dir"      → list_directory
  · "create / write / generate a file"     → write_file
  · "edit / change / fix <file>"           → read_file first, then edit_file
  · "run / execute / test"                 → run_command
  · "find / search for <pattern>"          → search_files

DO NOT use tools for:
  · Greetings, questions about your capabilities, conversational replies
  · Explaining code that was already shown in context
  · Simple yes/no or factual answers
  · NEVER output raw JSON like {{"name": "tool_name", "parameters": {{...}}}} as plain text —
    tools are invoked automatically by the system; you never write them out yourself.

## Coding workflow
1. Understand the request before touching any file.
2. Read a file before editing it — never assume its current contents.
3. Prefer edit_file (surgical find-replace) over write_file (full overwrite) for existing files.
4. After making changes, run tests or the program to verify correctness.
5. If a command fails, read the error message carefully and address the root cause.

## Code quality standards
- Write idiomatic, production-quality code in the language used by the project.
- Follow the existing style and conventions in the codebase.
- Include error handling at system boundaries (user input, I/O, network).
- Comment non-obvious logic; omit comments that restate what the code does.
- Keep functions focused; split large tasks into small, testable units.

## Response style
- Lead with working code or a direct answer — explain after.
- Use fenced code blocks with the correct language tag.
- Be concise: one clear recommendation rather than a list of options.
- If you discover a bug while completing the task, mention it but do not fix it \
unless asked (avoid scope creep).
"""

HELP_TEXT = """\
Commands  (type / to autocomplete):

  /reset            clear conversation history
  /abort            cancel current response  (also Esc)
  /steer <msg>      inject instruction into running/next turn
  /followup <msg>   queue a message after the current turn
  /clear-steering   clear steering queue
  /clear-followup   clear follow-up queue
  /clear-queues     clear all queues
  /system <prompt>  replace the system prompt
  /model <spec>     swap model  (llama3.1 | anthropic/claude-sonnet-4-6)
  /thinking <lvl>   thinking level:  off | low | medium | high
  /save [path]      save session to disk
  /load <path>      restore a saved session
  /state            show context size, model, system prompt
  /tools            list all available tools
  /help             show this help
  /exit | /quit     exit

Shell:
  ! <cmd>           run a shell command inline  (! git status)

Keyboard:
  Enter   send  ·  Tab/↑↓ complete  ·  Esc  abort  ·  Ctrl+D  exit
"""


# ─── AgentCLI ──────────────────────────────────────────────────────────────────

# Type alias for formatted text fragments
_Frags = list[tuple[str, str]]


class AgentCLI:
    def __init__(self, model_spec: str, session_file: Path) -> None:
        self._model_spec   = model_spec
        self._session_file = session_file
        self._model        = _make_model(model_spec)
        self._system       = _build_system_prompt()
        self._messages: list[dict] = []
        self._agent: PiAgent | None  = None
        self._app:   Application | None = None

        self._msg_count  = 0
        self._streaming  = False
        self._followup_queue: list[str] = []
        self._tool_times: dict[str, float] = {}

        # ── output buffer ──────────────────────────────────────────────────
        # We store all output as (style_class, text) pairs.
        # FormattedTextControl renders this; Window scrolls to keep cursor visible.
        self._frags: _Frags = []
        self._line_count = 0       # number of \n chars written so far
        self._input_buf: Buffer | None = None

    # ── output helpers ──────────────────────────────────────────────────────────

    def _w(self, *pairs: tuple[str, str], flush: bool = True) -> None:
        """Append (style, text) pairs to the output buffer."""
        for pair in pairs:
            self._frags.append(pair)
            self._line_count += pair[1].count("\n")
        if flush and self._app:
            self._app.invalidate()

    def _wl(self, *pairs: tuple[str, str], flush: bool = True) -> None:
        """Append pairs then a newline."""
        self._w(*pairs, flush=False)
        self._w(("", "\n"), flush=flush)

    # Status line helpers
    def _ok(self, msg: str) -> None:
        self._wl(("class:system-ok",   "  ✓ "), ("class:ai-text", msg)); self._app and self._app.invalidate()

    def _err(self, msg: str) -> None:
        self._wl(("class:system-err",  "  ✗ "), ("class:ai-text", msg)); self._app and self._app.invalidate()

    def _warn(self, msg: str) -> None:
        self._wl(("class:system-warn", "  ! "), ("class:ai-text", msg)); self._app and self._app.invalidate()

    def _info(self, msg: str) -> None:
        self._wl(("class:system-info", "  · "), ("class:ai-text", msg)); self._app and self._app.invalidate()

    # ── application layout ──────────────────────────────────────────────────────

    def _build_app(self) -> Application:
        self._input_buf = Buffer(
            name="input",
            completer=SlashCompleter(),
            complete_while_typing=True,
            history=InMemoryHistory(),
            accept_handler=self._on_enter,
            multiline=False,
        )

        # ── output control with auto-scroll ──────────────────────────────
        def _get_output() -> FormattedText:
            return FormattedText(self._frags)

        def _cursor_pos() -> Point:
            # Always point cursor to the last line → Window auto-scrolls to bottom
            return Point(x=0, y=self._line_count)

        output_ctrl = FormattedTextControl(
            text=_get_output,
            get_cursor_position=_cursor_pos,
            focusable=False,
        )

        # ── separator ─────────────────────────────────────────────────────
        def _sep_text() -> FormattedText:
            try:
                from prompt_toolkit.application.current import get_app as _gapp
                w = _gapp().output.get_size().columns
            except Exception:
                import shutil as _sh
                w = _sh.get_terminal_size((80, 24)).columns
            return FormattedText([("class:separator", "─" * w)])

        # ── status bar ────────────────────────────────────────────────────
        def _toolbar() -> FormattedText:
            parts: _Frags = [
                ("class:sb-model-label", " model "),
                ("class:sb-model-value", self._model_spec),
                ("class:separator",      "  │"),
                ("class:status-bar",     f"  msgs {self._msg_count}"),
                ("class:separator",      "  │"),
                ("class:status-bar",     "  Esc=abort  /=commands  !=shell"),
            ]
            if self._streaming:
                parts += [("class:separator", "  │"), ("class:sb-streaming", "  ⠿ responding…")]
            return FormattedText(parts)

        # ── key bindings ──────────────────────────────────────────────────
        kb = KeyBindings()

        @kb.add("escape")
        def _esc(event):
            if self._streaming:
                asyncio.ensure_future(self._abort_streaming())
            else:
                event.current_buffer.reset()

        @kb.add("c-d")
        def _ctrl_d(event):
            event.app.exit()

        # ── queued follow-up pane (sticky, above input bar) ────────────────
        def _followup_pane_text() -> FormattedText:
            parts: _Frags = []
            for i, msg in enumerate(self._followup_queue):
                if i > 0:
                    parts.append(("", "\n"))
                parts.append(("class:followup-hint", " ↩ "))
                display = msg if len(msg) <= 70 else msg[:70] + "…"
                parts.append(("class:system-info", display))
            return FormattedText(parts)

        followup_pane = ConditionalContainer(
            content=HSplit([
                Window(content=FormattedTextControl(_sep_text), height=1),
                Window(
                    content=FormattedTextControl(_followup_pane_text),
                    dont_extend_height=True,
                    style="class:followup-bar",
                ),
            ]),
            filter=Condition(lambda: bool(self._followup_queue)),
        )

        # ── layout ────────────────────────────────────────────────────────
        layout = Layout(
            FloatContainer(
                content=HSplit([
                    # scrolling output — takes all remaining height
                    Window(
                        content=output_ctrl,
                        wrap_lines=True,
                        style="class:ai-text",
                    ),
                    # sticky follow-up pane (hidden when empty)
                    followup_pane,
                    # thin separator line
                    Window(content=FormattedTextControl(_sep_text), height=1),
                    # fixed input line
                    Window(
                        content=BufferControl(
                            buffer=self._input_buf,
                            input_processors=[BeforeInput("❯ ", style="class:input-prompt")],
                            focusable=True,
                        ),
                        height=1,
                        style="class:input-area",
                        wrap_lines=False,
                    ),
                    # status bar
                    Window(
                        content=FormattedTextControl(_toolbar),
                        height=1,
                        style="class:status-bar",
                    ),
                ]),
                floats=[
                    Float(
                        xcursor=True, ycursor=True,
                        content=CompletionsMenu(max_height=14, scroll_offset=2),
                    ),
                ],
            ),
            focused_element=self._input_buf,
        )

        return Application(
            layout=layout,
            style=STYLE,
            key_bindings=kb,
            full_screen=True,
            mouse_support=False,
        )

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def _build_options(self) -> PiAgentOptions:
        return PiAgentOptions(
            system_prompt=self._system,
            model=self._model,
            tools=TOOLS,
            get_api_key=_get_api_key,
            messages=self._messages,
        )

    async def run(self) -> None:
        self._banner()

        async with PiAgent(self._build_options()) as agent:
            self._agent = agent
            self._app   = self._build_app()
            await self._app.run_async()

        self._agent = None
        self._app   = None
        print("\nGoodbye.\n")

    def _banner(self) -> None:
        self._wl(("class:user-header", "pi-agent"), ("class:system-info", " Coding Assistant"))
        self._wl(("class:system-info", "model  "), ("class:system-ok", self._model_spec),
                 ("class:system-info", f"  ·  {len(TOOLS)} tools  ·  type "),
                 ("class:user-header", "/"), ("class:system-info", " for commands"))
        self._wl(("", ""))

    # ── input handler ────────────────────────────────────────────────────────────

    def _on_enter(self, buf: Buffer) -> None:
        text = buf.text.strip()
        buf.reset()
        if not text:
            return

        # During streaming: queue plain messages as follow-ups; pass commands through.
        # The queued messages appear in the sticky pane above the input bar (not inline).
        if self._streaming and not text.startswith("/") and not text.startswith("!"):
            self._followup_queue.append(text)
            if self._app:
                self._app.invalidate()
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        loop.create_task(self._handle(text))

    async def _handle(self, text: str) -> None:
        if text.startswith("!"):
            await self._shell(text[1:].strip())
        elif text.startswith("/"):
            parts   = text[1:].split(None, 1)
            cmd     = parts[0].lower()
            args    = parts[1].strip() if len(parts) > 1 else ""
            if cmd in ("exit", "quit"):
                self._app and self._app.exit()
            else:
                await self._dispatch(cmd, args)
        else:
            await self._turn(text)

    # ── streaming turn ────────────────────────────────────────────────────────

    async def _turn(self, text: str) -> None:
        assert self._agent
        self._streaming = True
        self._app and self._app.invalidate()

        # Print user bubble
        self._wl(("class:user-header", "You"), ("class:system-info", ":"), flush=False)
        self._wl(("class:user-text", text), flush=False)
        self._wl(("", ""), flush=False)

        # AI response header — no newline yet (text streams inline)
        self._w(("class:ai-header", "AI"), ("class:system-info", ": "), flush=True)

        try:
            async for event in self._agent.prompt(text):
                self._event(event)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self._w(("class:aborted", " [aborted]"), flush=False)
            await self._agent.abort()
        finally:
            self._streaming = False
            self._msg_count += 2

        self._wl(("", ""))
        self._wl(("", ""))
        self._app and self._app.invalidate()

        # Auto-save
        try:
            await self._agent.save_session(str(self._session_file))
        except Exception:
            pass

        # Drain follow-up queue
        while self._followup_queue:
            nxt = self._followup_queue.pop(0)
            await self._turn(nxt)

    # ── event rendering ───────────────────────────────────────────────────────

    def _event(self, event: dict) -> None:
        t = event.get("type")

        if t == "message_update":
            ae = event.get("assistantMessageEvent", {})
            ae_t = ae.get("type")
            if ae_t == "text_delta":
                self._w(("class:ai-text", ae.get("delta", "")))
            elif ae_t == "thinking_delta":
                self._w(("class:system-info", f"[…] {ae.get('delta','')[:60]}"), flush=False)

        elif t == "tool_execution_start":
            tid  = event.get("toolCallId") or event.get("id", "?")
            name = event.get("toolName", "?")
            prms = event.get("params", {})
            self._tool_times[tid] = time.monotonic()

            compact = {k: (str(v)[:40] + "…" if len(str(v)) > 40 else str(v))
                       for k, v in prms.items()}
            pstr = "  ".join(f"{k}={repr(v)[:30]}" for k, v in compact.items())

            self._wl(("", ""), flush=False)
            self._w(("class:tool-border", "  ┌ "), ("class:tool-name", name), flush=False)
            if pstr:
                self._w(("class:tool-meta", f"  {pstr}"), flush=False)
            self._w(("", "\n"), flush=True)

        elif t == "tool_execution_end":
            tid    = event.get("toolCallId") or event.get("id", "?")
            result = event.get("result") or {}
            is_err = event.get("isError", False)
            ms     = (time.monotonic() - self._tool_times.pop(tid, time.monotonic())) * 1000
            txt    = " ".join(
                b.get("text", "") for b in result.get("content", [])
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if is_err:
                self._wl(("class:tool-border", "  └ "), ("class:tool-err", "error"),
                         ("class:tool-meta", f"  {ms:.0f}ms"))
            else:
                self._wl(("class:tool-border", "  └ "), ("class:tool-ok", "✓"),
                         ("class:tool-meta", f"  {len(txt)} chars  {ms:.0f}ms"))

        elif t == "turn_end":
            stop = event.get("stopReason", "")
            if stop and stop not in ("end_turn", "tool_use"):
                self._wl(("class:system-info", f"[{stop}]"))

    # ── shell command ─────────────────────────────────────────────────────────

    async def _shell(self, cmd: str) -> None:
        if not cmd:
            self._err("Usage:  ! <shell command>")
            return
        self._wl(("", ""), flush=False)
        self._wl(("class:system-info", f"  $ {cmd}"))
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout
        async for line in proc.stdout:
            self._w(("class:ai-text", "  " + line.decode(errors="replace")), flush=False)
        self._app and self._app.invalidate()
        await proc.wait()
        if proc.returncode != 0:
            self._warn(f"exit {proc.returncode}")
        self._wl(("", ""))

    # ── abort ─────────────────────────────────────────────────────────────────

    async def _abort_streaming(self) -> None:
        if self._agent and self._streaming:
            await self._agent.abort()

    # ── command dispatch ──────────────────────────────────────────────────────

    _CMD = {
        "help":            "_c_help",
        "reset":           "_c_reset",
        "abort":           "_c_abort",
        "steer":           "_c_steer",
        "followup":        "_c_followup",
        "follow-up":       "_c_followup",
        "clear-steering":  "_c_clear_steering",
        "clear-followup":  "_c_clear_followup",
        "clear-follow-up": "_c_clear_followup",
        "clear-queues":    "_c_clear_queues",
        "system":          "_c_system",
        "model":           "_c_model",
        "thinking":        "_c_thinking",
        "save":            "_c_save",
        "load":            "_c_load",
        "state":           "_c_state",
        "tools":           "_c_tools",
    }

    async def _dispatch(self, cmd: str, args: str) -> None:
        m = self._CMD.get(cmd)
        if m is None:
            self._err(f"Unknown command: /{cmd}  (type / to see all)")
            return
        await getattr(self, m)(args)

    # ── /commands ─────────────────────────────────────────────────────────────

    async def _c_help(self, _: str) -> None:
        for line in HELP_TEXT.splitlines():
            self._wl(("class:ai-text", line), flush=False)
        self._app and self._app.invalidate()

    async def _c_reset(self, _: str) -> None:
        assert self._agent
        await self._agent.reset()
        self._messages  = []
        self._msg_count = 0
        self._ok("Conversation cleared.")

    async def _c_abort(self, _: str) -> None:
        await self._abort_streaming()

    async def _c_steer(self, args: str) -> None:
        assert self._agent
        if not args:
            self._err("Usage:  /steer <message>")
            return
        await self._agent.steer(args)
        self._ok(f"Steering queued: {args[:80]}")

    async def _c_followup(self, args: str) -> None:
        assert self._agent
        if not args:
            self._err("Usage:  /followup <message>")
            return
        await self._agent.follow_up(args)
        self._ok(f"Follow-up queued: {args[:80]}")

    async def _c_clear_steering(self, _: str) -> None:
        assert self._agent
        await self._agent.clear_steering_queue()
        self._ok("Steering queue cleared.")

    async def _c_clear_followup(self, _: str) -> None:
        assert self._agent
        await self._agent.clear_follow_up_queue()
        self._ok("Follow-up queue cleared.")

    async def _c_clear_queues(self, _: str) -> None:
        assert self._agent
        await self._agent.clear_all_queues()
        self._ok("All queues cleared.")

    async def _c_system(self, args: str) -> None:
        assert self._agent
        if not args:
            self._err("Usage:  /system <new system prompt>")
            return
        self._system = args
        await self._agent.set_system_prompt(args)
        self._ok(f"System prompt updated ({len(args)} chars).")

    async def _c_model(self, args: str) -> None:
        assert self._agent
        spec = args.strip()
        if not spec:
            self._err("Usage:  /model <spec>")
            return
        try:
            self._model      = _make_model(spec)
            self._model_spec = spec
            await self._agent.set_model(self._model)
            self._app and self._app.invalidate()
            self._ok(f"Model → {spec}")
        except Exception as exc:
            self._err(f"Failed: {exc}")

    async def _c_thinking(self, args: str) -> None:
        assert self._agent
        level = args.strip().lower()
        if level not in ("off", "none", "low", "medium", "high"):
            self._err("Usage:  /thinking <off|low|medium|high>")
            return
        await self._agent.set_thinking_level(level)
        self._ok(f"Thinking → {level}")

    async def _c_save(self, args: str) -> None:
        assert self._agent
        path = args.strip() or str(self._session_file)
        try:
            await self._agent.save_session(path)
            self._ok(f"Session saved → {path}")
        except Exception as exc:
            self._err(f"Save failed: {exc}")

    async def _c_load(self, args: str) -> None:
        assert self._agent
        path = args.strip()
        if not path:
            self._err("Usage:  /load <path>")
            return
        if not Path(path).exists():
            self._err(f"Not found: {path}")
            return
        try:
            msgs = load_session(path)
            await self._agent.disconnect()
            self._messages  = msgs
            self._msg_count = len(msgs)
            new_agent = PiAgent(self._build_options())
            await new_agent.connect()
            self._agent = new_agent
            self._app and self._app.invalidate()
            self._ok(f"Session loaded ← {path}  ({len(msgs)} messages)")
        except Exception as exc:
            self._err(f"Load failed: {exc}")

    async def _c_state(self, _: str) -> None:
        assert self._agent
        try:
            state   = await self._agent.get_state()
            model   = state.get("model") or {}
            mid     = model.get("id") or model.get("name") or "?"
            think   = state.get("thinkingLevel") or "off"
            syspre  = (state.get("systemPrompt") or "")[:80].replace("\n", " ")
            ellip   = "…" if len(state.get("systemPrompt", "")) > 80 else ""
            self._wl(("", ""), flush=False)
            self._wl(("class:user-header", "  Agent state"), flush=False)
            self._wl(("class:system-info", "  " + "─" * 38), flush=False)
            self._info(f"model       {mid}")
            self._info(f"messages    {len(state.get('messages', []))}")
            self._info(f"thinking    {think}")
            self._info(f"system      {syspre}{ellip}")
            self._wl(("", ""))
        except Exception as exc:
            self._err(f"Could not fetch state: {exc}")

    async def _c_tools(self, _: str) -> None:
        self._wl(("", ""), flush=False)
        self._wl(("class:user-header", f"  Tools ({len(TOOLS)})"), flush=False)
        self._wl(("class:system-info", "  " + "─" * 38), flush=False)
        for tool in TOOLS:
            req    = tool.parameters.get("required", [])
            params = ", ".join(req) or "—"
            self._wl(("class:tool-name", f"  {tool.name:<26}"),
                     ("class:system-info", params), flush=False)
        self._wl(("", ""))


# ─── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="pi-agent CLI Coding Agent")
    p.add_argument("--model",   "-m", default=DEFAULT_MODEL,
                   help="local model (llama3.1) or provider/name (anthropic/claude-sonnet-4-6)")
    p.add_argument("--session", "-s", default=str(DEFAULT_SESSION),
                   help="session file path (default: .pi_agent_session.json)")
    args = p.parse_args()
    asyncio.run(AgentCLI(args.model, Path(args.session)).run())


if __name__ == "__main__":
    main()
