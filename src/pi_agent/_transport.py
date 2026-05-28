from __future__ import annotations

import asyncio
import json
import platform
import shutil
import stat
import sys
from pathlib import Path
from typing import AsyncIterator

from ._errors import PiConnectionError

# ─── Binary detection ─────────────────────────────────────────────────────────

_BINARY_MAP: dict[tuple[str, str], str] = {
    ("darwin", "arm64"):   "pi-agent-bridge-darwin-arm64",
    ("darwin", "x86_64"):  "pi-agent-bridge-darwin-x64",
    ("linux",  "x86_64"):  "pi-agent-bridge-linux-x64",
    ("linux",  "aarch64"): "pi-agent-bridge-linux-arm64",
    ("win32",  "AMD64"):   "pi-agent-bridge-win32-x64.exe",
}

# Installed wheel places the binary here
_PACKAGE_BIN_DIR = Path(__file__).parent / "bin"
# Local dev build places the binary here (repo root / bin)
_REPO_BIN_DIR    = Path(__file__).parent.parent.parent / "bin"
# Source for bun-run fallback
_BRIDGE_SRC      = Path(__file__).parent.parent.parent / "bridge" / "bridge.ts"


def _ensure_executable(path: Path) -> None:
    """Fix the missing +x bit that wheel ZIP extraction strips on Unix."""
    mode = path.stat().st_mode
    if not (mode & stat.S_IXUSR):
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _detect_command() -> list[str]:
    """
    Probe for the bridge executable in priority order:
      1. Compiled binary in <package>/bin/   (installed wheel)
      2. Compiled binary in <repo-root>/bin/ (local dev build)
      3. bun run bridge/bridge.ts            (bun in PATH + source present)
      4. Raise PiConnectionError with actionable message
    """
    plat = sys.platform
    arch = platform.machine()
    name = _BINARY_MAP.get((plat, arch))

    if name:
        for directory in (_PACKAGE_BIN_DIR, _REPO_BIN_DIR):
            candidate = directory / name
            if candidate.exists() and candidate.stat().st_size > 0:
                _ensure_executable(candidate)
                return [str(candidate)]

    bun = shutil.which("bun")
    if bun and _BRIDGE_SRC.exists():
        return [bun, "run", str(_BRIDGE_SRC)]

    raise PiConnectionError(
        f"No pi-agent-bridge found for {plat}/{arch}.\n"
        "  1. Install the platform wheel:  pip install pi-agent\n"
        f"  2. Build locally:               cd bridge && bun install && "
        "bun run build.ts --current\n"
        "  3. Install Bun (dev fallback):  https://bun.sh"
    )


# ─── Transport ────────────────────────────────────────────────────────────────

class SubprocessTransport:
    """Manages the bridge subprocess and provides line-level JSON I/O."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None

    async def connect(self) -> None:
        cmd = _detect_command()
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,  # pipe to prevent 64 KB buffer deadlock
        )
        # Drain stderr in the background to prevent deadlock and surface bridge crashes
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self._process is not None
        assert self._process.stderr is not None
        async for line in self._process.stderr:
            sys.stderr.buffer.write(b"[pi-bridge] " + line)
            sys.stderr.buffer.flush()

    async def write(self, obj: dict) -> None:
        assert self._process is not None
        assert self._process.stdin is not None
        data = (json.dumps(obj) + "\n").encode()
        self._process.stdin.write(data)
        await self._process.stdin.drain()

    async def iter_lines(self) -> AsyncIterator[dict]:
        assert self._process is not None
        assert self._process.stdout is not None
        while True:
            raw = await self._process.stdout.readline()
            if not raw:
                break
            try:
                yield json.loads(raw.strip())
            except json.JSONDecodeError:
                continue

    async def disconnect(self) -> None:
        if self._process is None:
            return
        try:
            await self.write({"type": "shutdown"})
            if self._process.stdin:
                self._process.stdin.close()
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            self._process.kill()
            await self._process.wait()
        finally:
            if self._stderr_task and not self._stderr_task.done():
                self._stderr_task.cancel()
            self._process = None
