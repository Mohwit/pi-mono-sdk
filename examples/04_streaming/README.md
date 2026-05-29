# 04 · Streaming Events

Complete reference for every event the agent emits.

| File | Run | What you learn |
|------|-----|----------------|
| `streaming_events.py` | `python streaming_events.py` | All event types with annotated output |

## Event type reference

```
agent_start          — agent is initialised and connected
turn_start           — a new request/response cycle begins
message_update       — incremental assistant output (text_delta, thinking_delta)
tool_execution_start — a tool is about to run (name + params visible)
tool_execution_end   — tool finished (result + timing)
turn_end             — LLM finished; includes stopReason
agent_done           — agent is shutting down
```

## Minimal consumer

```python
async for event in agent.prompt("Hello"):
    t = event.get("type")
    if t == "message_update":
        ae = event.get("assistantMessageEvent", {})
        if ae.get("type") == "text_delta":
            print(ae["delta"], end="", flush=True)
    elif t == "tool_execution_start":
        print(f"\n[tool] {event['toolName']}")
    elif t == "turn_end":
        print(f"\n[stop: {event.get('stopReason')}]")
```
