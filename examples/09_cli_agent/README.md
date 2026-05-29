# 09 · CLI Coding Agent

A full-screen interactive coding assistant — similar to Claude Code — built entirely on pi-agent.

```
┌──────────────────────────────────────────────────────┐
│  pi-agent  Coding Assistant                          │
│  model  llama3.1  ·  10 tools  ·  type / for cmds   │
│                                                      │
│  You:                                                │
│  list the files in this directory                    │
│                                                      │
│  AI:                                                 │
│    ┌ list_directory  (path=.)                        │
│    └ ✓  248 chars  12ms                              │
│  Here are the files: ...                             │
│                                                      │
│ ↩ write a hello world script          ← follow-up   │
├──────────────────────────────────────────────────────┤
│ ❯ _                                                  │
├──────────────────────────────────────────────────────┤
│ model llama3.1  │  msgs 2  │  Esc=abort  /=commands  │
└──────────────────────────────────────────────────────┘
```

## Run

```bash
# Local Ollama (default)
python examples/09_cli_agent/

# Anthropic Claude
ANTHROPIC_API_KEY=sk-... python examples/09_cli_agent/ --model anthropic/claude-sonnet-4-6

# OpenAI
OPENAI_API_KEY=sk-... python examples/09_cli_agent/ --model openai/gpt-4o

# Resume a previous session
python examples/09_cli_agent/ --session my_session.json
```

## Tools available to the agent

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents (with optional line range) |
| `write_file` | Create or overwrite a file |
| `edit_file` | Targeted find-and-replace in a file |
| `append_to_file` | Append text to a file |
| `list_directory` | List directory contents with sizes |
| `create_directory` | Create directory (mkdir -p) |
| `delete_file` | Delete a file or directory |
| `move_file` | Move or rename a file |
| `run_command` | Execute a shell command |
| `search_files` | Search file contents with regex (ripgrep / grep) |

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `Esc` | Abort current AI response |
| `Tab` / `↑↓` | Navigate slash-command completions |
| `Ctrl+D` | Exit |

## Slash commands

Type `/` to see all commands with descriptions. Key commands:

| Command | Description |
|---------|-------------|
| `/reset` | Clear conversation history |
| `/abort` | Cancel current response (same as Esc) |
| `/steer <msg>` | Inject instruction into current/next turn |
| `/followup <msg>` | Queue message for after current response |
| `/model <spec>` | Hot-swap model (e.g. `anthropic/claude-sonnet-4-6`) |
| `/system <text>` | Replace system prompt |
| `/thinking <level>` | Set thinking: `off` \| `low` \| `medium` \| `high` |
| `/save [path]` | Save session to disk |
| `/load <path>` | Restore a saved session |
| `/state` | Show model, message count, system prompt |
| `/tools` | List all 10 tools |
| `/help` | Full command reference |

## Follow-up messages

While the AI is responding, you can type your next message and press Enter.
It will appear in a **sticky pane** above the input bar and be sent automatically
when the current response finishes.

## Session management

Sessions are auto-saved to `.pi_agent_session.json` after every turn.
Use `/save path.json` to save to a custom path, and `/load path.json` to restore.

## Shell commands

Prefix any shell command with `!` to run it directly:

```
❯ ! git status
❯ ! python -m pytest tests/
❯ ! cat pyproject.toml
```

## Security notice

> **The `run_command` tool gives the model unrestricted shell access on your machine.**
> Only use the CLI agent with models and sessions you trust.
> The `delete_file` tool deletes recursively with no confirmation — the agent's
> system prompt instructs it to confirm before deleting, but you should verify
> sensitive operations before they execute.
