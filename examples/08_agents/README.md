# 08 · Full Agents

Complete, production-style agent examples that combine every feature.

| File | Run | What it builds |
|------|-----|----------------|
| `research_agent.py` | `python research_agent.py [--resume]` | Research assistant with tools, all hooks, context transforms, and session persistence |

## research_agent.py

A distributed-systems research assistant that demonstrates:

- **Tools**: web search (simulated), calculator, note-taking, note listing/reading
- **beforeToolCall**: interactive confirmation for destructive writes + audit log
- **afterToolCall**: result enrichment, circuit breaker on repeated errors
- **transformContext**: sliding-window to prevent context overflow
- **Session persistence**: save after each run, resume with `--resume`
- **SyncPiAgent**: entire agent runs via the sync API

```bash
# Fresh research session
python research_agent.py

# Resume previous session and continue with new topic
python research_agent.py --resume
```

Notes are saved to `data/research_notes/` as Markdown files.
The session is saved to `data/research_agent_session.json`.
