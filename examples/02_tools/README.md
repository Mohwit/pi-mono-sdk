# 02 · Tools

Teach the agent new capabilities by registering Python functions as tools.

| File | Run | What you learn |
|------|-----|----------------|
| `tools_example.py` | `python tools_example.py` | `ToolDefinition`, JSON Schema parameters, parallel & sequential execution, error handling |

## Key pattern

```python
from pi_agent import ToolDefinition

async def my_tool(params: dict) -> dict:
    result = do_something(params["input"])
    return {"content": [{"type": "text", "text": result}]}

tool = ToolDefinition(
    name="my_tool",
    description="Short description the model uses to decide when to call this tool.",
    parameters={
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "The input value"},
        },
        "required": ["input"],
    },
    execute=my_tool,
    execution_mode="parallel",   # or "sequential"
)
```
