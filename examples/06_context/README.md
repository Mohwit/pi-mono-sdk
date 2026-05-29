# 06 · Context Management

Control what the model sees: save/restore conversations and rewrite the message history.

| File | Run | What you learn |
|------|-----|----------------|
| `session_persistence.py` | `python session_persistence.py [--resume]` | `save_session()`, `load_session()`, `get_state()` |
| `transform_context.py` | `python transform_context.py [sliding\|inject\|scrub\|combined]` | Sliding window, live injection, PII scrubbing |

## Session persistence

```python
# Save after a turn
await agent.save_session("session.json")

# Resume in the next run
from pi_agent import load_session
messages = load_session("session.json")

async with PiAgent(PiAgentOptions(..., messages=messages)) as agent:
    ...   # history is restored
```

## transformContext

Called before every LLM request. Returns the message list that will actually be sent.

```python
async def transform_context(messages: list[dict]) -> list[dict]:
    return messages[-12:]   # keep only the last 6 turns
```

### Demo modes

```bash
python transform_context.py sliding   # drop old messages when context grows
python transform_context.py inject    # prepend a live system-status block
python transform_context.py scrub     # strip PII before sending to the model
python transform_context.py combined  # all three chained together
```
