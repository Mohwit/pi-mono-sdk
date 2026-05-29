"""
getApiKey Callback — v1 Feature

Demonstrates how to wire a custom API key provider instead of relying
on environment variables. Useful for:
  - Multi-tenant apps where each user has their own API key
  - Key rotation systems (fetch fresh keys from a vault each call)
  - Development mode with a local LLM (return a dummy key)
  - Key selection based on the provider (route to different key stores)

How it works:
  1. Python passes `get_api_key=<callback>` in PiAgentOptions
  2. The bridge signals `has_get_api_key=True` to the TypeScript side
  3. Before each API call, the bridge sends {"type": "get_api_key", "provider": "..."}
  4. Python calls the callback and sends {"type": "api_key_result", "key": "..."}
  5. The bridge Promise resolves and the API call proceeds

Use cases shown:
  1. Local LLM          — always return "ollama" (no real key needed)
  2. Vault lookup       — simulate fetching a key from a secrets manager
  3. Per-provider keys  — route to different key sources based on provider
  4. Audit + rotation   — log key usage and refresh on a timer

Run:
  python examples/get_api_key.py [local|vault|audit]

Requires Ollama:
  ollama serve && ollama pull llama3.1
"""

import asyncio
import sys
from datetime import datetime

from pi_agent import PiAgent, PiAgentOptions, LocalModelConfig


LOCAL_MODEL = LocalModelConfig(
    id="llama3.1",
    name="Llama 3.1",
    api="openai-completions",
    provider="local",
    base_url="http://localhost:11434/v1/",
)


async def stream_response(agent, prompt: str) -> str:
    parts = []
    async for event in agent.prompt(prompt):
        t = event.get("type")
        if (
            t == "message_update"
            and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
        ):
            delta = event["assistantMessageEvent"]["delta"]
            parts.append(delta)
            print(delta, end="", flush=True)
    return "".join(parts)


# ─── Use Case 1: Local LLM — dummy key ────────────────────────────────────────

async def demo_local() -> None:
    print("=" * 60)
    print("USE CASE 1: Local LLM — dummy key via get_api_key")
    print("=" * 60 + "\n")

    async def get_api_key(provider: str) -> str:
        """Ollama accepts any non-empty string as the API key."""
        print(f"  [get_api_key] provider={provider!r} → returning 'ollama'")
        return "ollama"

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a helpful assistant.",
        model=LOCAL_MODEL,
        get_api_key=get_api_key,
    )) as agent:
        print("AI: ", end="", flush=True)
        await stream_response(agent, "Hello! What is 7 * 8?")
        print()


# ─── Use Case 2: Vault lookup ──────────────────────────────────────────────────

# Simulated secrets vault
_VAULT: dict[str, str] = {
    "anthropic": "sk-ant-demo-key-from-vault",
    "openai":    "sk-openai-demo-key-from-vault",
    "local":     "ollama",
}


async def demo_vault() -> None:
    print("=" * 60)
    print("USE CASE 2: Vault Lookup — fetch key per provider")
    print("=" * 60 + "\n")

    call_log: list[dict] = []

    async def get_api_key(provider: str) -> str:
        """
        Simulate an async call to a secrets manager (e.g. HashiCorp Vault,
        AWS Secrets Manager, or a database row).
        """
        await asyncio.sleep(0.05)  # simulate network latency
        key = _VAULT.get(provider)
        if key is None:
            raise ValueError(f"No key configured for provider: {provider!r}")
        entry = {"ts": datetime.now().isoformat(), "provider": provider, "key_prefix": key[:8]}
        call_log.append(entry)
        print(f"  [vault] {provider} → {key[:8]}…")
        return key

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a concise assistant.",
        model=LOCAL_MODEL,
        get_api_key=get_api_key,
    )) as agent:
        print("AI: ", end="", flush=True)
        await stream_response(agent, "Summarise the CAP theorem in two sentences.")
        print()

    print(f"\nVault lookup log ({len(call_log)} calls):")
    for entry in call_log:
        print(f"  [{entry['ts'][11:19]}] provider={entry['provider']} key={entry['key_prefix']}…")


# ─── Use Case 3: Audit + automatic renewal simulation ─────────────────────────

class KeyManager:
    """
    Simulates a key manager that tracks usage and refreshes keys on demand.
    In production this would talk to a real vault or key rotation service.
    """

    def __init__(self) -> None:
        self._keys = {"local": "ollama"}
        self._usage: dict[str, int] = {}
        self._refreshes: int = 0

    async def get_key(self, provider: str) -> str:
        self._usage[provider] = self._usage.get(provider, 0) + 1
        uses = self._usage[provider]

        # Simulate key refresh every 3 uses
        if uses % 3 == 0:
            self._refreshes += 1
            print(f"\n  [key_manager] refreshing key for '{provider}' (use #{uses})")
            await asyncio.sleep(0.02)  # simulate vault round-trip

        key = self._keys.get(provider, "unknown")
        print(f"  [key_manager] {provider} → {key[:8]}… (use #{uses})")
        return key

    def report(self) -> None:
        print(f"\nKey usage report:")
        for provider, count in self._usage.items():
            print(f"  {provider}: {count} uses")
        print(f"  Total refreshes: {self._refreshes}")


async def demo_audit() -> None:
    print("=" * 60)
    print("USE CASE 3: Key Manager with Usage Audit")
    print("=" * 60 + "\n")

    manager = KeyManager()

    async with PiAgent(PiAgentOptions(
        system_prompt="You are a concise assistant. Keep answers to one sentence.",
        model=LOCAL_MODEL,
        get_api_key=manager.get_key,
    )) as agent:
        questions = [
            "What is asyncio?",
            "What is a coroutine?",
            "What is an event loop?",
            "What is async/await?",
        ]
        for q in questions:
            print(f"\nYou: {q}")
            print("AI: ", end="", flush=True)
            await stream_response(agent, q)
            print()

    manager.report()


# ─── Main ─────────────────────────────────────────────────────────────────────

DEMOS = {
    "local": demo_local,
    "vault": demo_vault,
    "audit": demo_audit,
}

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "vault"
    if mode not in DEMOS:
        print(f"Unknown mode '{mode}'. Choose from: {', '.join(DEMOS)}")
        sys.exit(1)
    asyncio.run(DEMOS[mode]())
