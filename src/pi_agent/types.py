from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Literal


@dataclass
class ModelConfig:
    provider: str   # "anthropic" | "openai" | "google" | ...
    name: str       # "claude-sonnet-4-20250514", "gpt-4o", ...


@dataclass
class LocalModelConfig:
    """Raw model config for local or custom LLM endpoints (e.g. Ollama via openai-completions)."""
    id: str                             # model id sent to the API, e.g. "llama3.1"
    provider: str                       # arbitrary label, e.g. "local"
    api: str                            # provider API to use, e.g. "openai-completions"
    base_url: str                       # endpoint, e.g. "http://localhost:11434/v1/"
    name: str = ""                      # human-readable name (defaults to id)
    reasoning: bool = False
    input: list[str] = field(default_factory=lambda: ["text"])
    context_window: int = 8192
    max_tokens: int = 2048
    cost: dict = field(default_factory=lambda: {
        "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0
    })


@dataclass
class ToolResult:
    content: list[dict]                     # [{"type": "text", "text": "..."}]
    details: dict = field(default_factory=dict)
    terminate: bool = False


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict                        # JSON Schema object
    execute: Any                            # async (params: dict) -> ToolResult | dict
    label: str = ""
    execution_mode: Literal["sequential", "parallel"] = "parallel"


@dataclass
class PiAgentOptions:
    system_prompt: str
    model: ModelConfig | LocalModelConfig
    thinking_level: str | None = None
    tools: list[ToolDefinition] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    tool_execution: Literal["parallel", "sequential"] = "parallel"
    steering_mode: Literal["one-at-a-time", "all"] = "one-at-a-time"
    follow_up_mode: Literal["one-at-a-time", "all"] = "one-at-a-time"
    # v1: if set, bridge calls this callback for every API key request;
    # if None, the bridge relies on provider env vars (ANTHROPIC_API_KEY, etc.)
    get_api_key: Callable[[str], Awaitable[str]] | None = None
    # v2: called before each tool execution; return {"block": True, "reason": "..."} to prevent it
    before_tool_call: Callable[[dict], Awaitable[dict | None]] | None = None
    # v2: called after each tool execution; return {"terminate": True} or {"details": dict} optionally
    after_tool_call: Callable[[dict], Awaitable[dict | None]] | None = None
    # v2: called before each LLM request to transform the message context
    transform_context: Callable[[list[dict]], Awaitable[list[dict]]] | None = None


@dataclass
class AgentState:
    system_prompt: str
    model: ModelConfig
    thinking_level: str | None
    tools: list[dict]
    messages: list[dict]
    is_streaming: bool
    pending_tool_calls: list[str]
    error_message: str | None
