# 01 · Quickstart

The three files every new user should read first.

| File | Run | What you learn |
|------|-----|----------------|
| `simple_chat.py` | `python simple_chat.py` | Core async lifecycle, streaming text deltas, multi-turn conversation |
| `local_llm.py` | `python local_llm.py` | `LocalModelConfig` for Ollama, tool calling with a local model |
| `get_api_key.py` | `python get_api_key.py [vault\|audit\|rotate]` | Custom API key callback, key rotation, per-request audit log |

## Prerequisites

```bash
ollama serve
ollama pull llama3.1
```

## Recommended reading order

1. `simple_chat.py` — understand `PiAgent`, `PiAgentOptions`, and the event loop
2. `local_llm.py` — add tools and use a local model
3. `get_api_key.py` — control how API keys are fetched at runtime
