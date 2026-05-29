# 05 · Agent Control

Manipulate the agent while it's running or between turns.

| File | Run | What you learn |
|------|-----|----------------|
| `steering_and_followup.py` | `python steering_and_followup.py [pipeline\|steer\|followup\|abort]` | `steer()`, `follow_up()`, `abort()`, `reset()`, `set_model()`, `set_system_prompt()` |

## API quick reference

| Method | When to use |
|--------|-------------|
| `agent.steer(msg)` | Inject an instruction the model sees during the *current* turn |
| `agent.follow_up(msg)` | Queue a message to be sent automatically after the current turn ends |
| `agent.abort()` | Cancel a running response immediately |
| `agent.reset()` | Clear conversation history (fresh start, keeps tools & system prompt) |
| `agent.set_model(model)` | Hot-swap the model mid-session |
| `agent.set_system_prompt(text)` | Replace the system prompt mid-session |
| `agent.set_thinking_level(level)` | `"off"` \| `"low"` \| `"medium"` \| `"high"` |

## Demo modes

```bash
python steering_and_followup.py pipeline   # chains steer + followup in sequence
python steering_and_followup.py steer      # injects a steer mid-response
python steering_and_followup.py followup   # queues a follow-up message
python steering_and_followup.py abort      # aborts a long response mid-stream
```
