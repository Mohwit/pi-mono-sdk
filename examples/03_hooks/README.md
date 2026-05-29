# 03 · Hooks

Intercept tool calls before and after execution.

| File | Run | What you learn |
|------|-----|----------------|
| `before_tool_call.py` | `python before_tool_call.py` | Security gate, interactive confirmation, rate limiting |
| `after_tool_call.py` | `python after_tool_call.py` | Result monitoring, output enrichment, circuit breaker, PII filtering |

## beforeToolCall

Called before every tool execution. Return `{"block": True, "reason": "..."}` to prevent it,
or `None` to allow.

```python
async def before_tool_call(ctx: dict) -> dict | None:
    # ctx = {"id": str, "name": str, "params": dict}
    if ctx["name"] == "delete_file":
        confirm = input(f"Delete {ctx['params']['path']}? [y/N] ")
        if confirm.lower() != "y":
            return {"block": True, "reason": "User declined"}
    return None
```

## afterToolCall

Called after every tool execution. Return `{"terminate": True}` to abort the turn,
or `{"details": {...}}` to enrich the result the model sees.

```python
async def after_tool_call(ctx: dict) -> dict | None:
    # ctx = {"id": str, "name": str, "params": dict, "result": dict, "is_error": bool}
    if ctx.get("is_error"):
        error_count += 1
        if error_count >= 3:
            return {"terminate": True}   # circuit breaker
    return None
```
