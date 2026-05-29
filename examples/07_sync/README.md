# 07 · Synchronous API

Use pi-agent without `asyncio` — ideal for scripts, data pipelines, and Jupyter notebooks.

| File | Run | What you learn |
|------|-----|----------------|
| `sync_api.py` | `python sync_api.py [batch\|repl]` | `SyncPiAgent`, sync prompt loop, batch processing |

## When to use SyncPiAgent

- Plain Python scripts where you don't want to manage an event loop
- Jupyter notebooks (avoid `asyncio.run` inside cells)
- Flask / Django request handlers
- Batch jobs processing many inputs sequentially

## Quick example

```python
from pi_agent import SyncPiAgent, PiAgentOptions

with SyncPiAgent(PiAgentOptions(...)) as agent:
    for event in agent.prompt("Summarise this paragraph: ..."):
        ae = event.get("assistantMessageEvent", {})
        if ae.get("type") == "text_delta":
            print(ae["delta"], end="", flush=True)
```

## Demo modes

```bash
python sync_api.py batch   # process a list of inputs and write results to a file
python sync_api.py repl    # simple synchronous REPL (no asyncio)
```
