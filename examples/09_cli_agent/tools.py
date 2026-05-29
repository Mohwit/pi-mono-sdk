"""
Coding tools for the CLI agent.

Ten tools covering the full file-system + shell workflow:
  read_file, write_file, edit_file, append_to_file,
  list_directory, create_directory, delete_file, move_file,
  run_command, search_files
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from pi_agent import ToolDefinition


# ─── Implementations ──────────────────────────────────────────────────────────

async def read_file(params: dict) -> dict:
    path       = params["path"]
    start_line = params.get("start_line")
    end_line   = params.get("end_line")
    try:
        text  = Path(path).read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        total = len(lines)
        if start_line is not None or end_line is not None:
            s    = (int(start_line) - 1) if start_line is not None else 0
            e    = int(end_line)          if end_line   is not None else total
            text = "".join(lines[s:e])
            text = f"[lines {s + 1}–{min(e, total)} of {total} in {path}]\n" + text
        return {"content": [{"type": "text", "text": text}]}
    except FileNotFoundError:
        return {"content": [{"type": "text", "text": f"File not found: {path}"}]}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Error reading {path}: {exc}"}]}


async def write_file(params: dict) -> dict:
    path    = params["path"]
    content = params["content"]
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        lines = content.count("\n") + 1
        return {"content": [{"type": "text", "text": f"Wrote {lines} line(s) to {path}"}]}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Error writing {path}: {exc}"}]}


async def edit_file(params: dict) -> dict:
    path        = params["path"]
    old_string  = params["old_string"]
    new_string  = params["new_string"]
    replace_all = bool(params.get("replace_all", False))
    try:
        text  = Path(path).read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            return {"content": [{"type": "text", "text": f"String not found in {path}.\nMake sure the old_string matches exactly (including whitespace and indentation)."}]}
        if count > 1 and not replace_all:
            return {"content": [{"type": "text", "text": (
                f"Found {count} occurrences in {path}. "
                "Provide a more specific old_string, or set replace_all=true to replace all."
            )}]}
        new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        Path(path).write_text(new_text, encoding="utf-8")
        replaced = count if replace_all else 1
        return {"content": [{"type": "text", "text": f"Replaced {replaced} occurrence(s) in {path}"}]}
    except FileNotFoundError:
        return {"content": [{"type": "text", "text": f"File not found: {path}"}]}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Error editing {path}: {exc}"}]}


async def append_to_file(params: dict) -> dict:
    path    = params["path"]
    content = params["content"]
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return {"content": [{"type": "text", "text": f"Appended {len(content)} char(s) to {path}"}]}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Error appending to {path}: {exc}"}]}


async def list_directory(params: dict) -> dict:
    path = params.get("path", ".")
    try:
        entries = sorted(Path(path).iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines   = []
        for e in entries:
            if e.is_dir():
                lines.append(f"  {e.name}/")
            else:
                size = e.stat().st_size
                size_str = f"{size / 1_048_576:>6.1f} MB" if size >= 1_048_576 else f"{size:>8,} B"
                lines.append(f"  {e.name:<45} {size_str}")
        body = "\n".join(lines) if lines else "  (empty)"
        return {"content": [{"type": "text", "text": f"{path}/\n{body}"}]}
    except FileNotFoundError:
        return {"content": [{"type": "text", "text": f"Directory not found: {path}"}]}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Error listing {path}: {exc}"}]}


async def create_directory(params: dict) -> dict:
    path = params["path"]
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return {"content": [{"type": "text", "text": f"Created: {path}"}]}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Error creating {path}: {exc}"}]}


async def delete_file(params: dict) -> dict:
    path = params["path"]
    try:
        p = Path(path)
        if not p.exists():
            return {"content": [{"type": "text", "text": f"Not found: {path}"}]}
        if p.is_dir():
            shutil.rmtree(p)
            return {"content": [{"type": "text", "text": f"Deleted directory: {path}"}]}
        p.unlink()
        return {"content": [{"type": "text", "text": f"Deleted file: {path}"}]}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Error deleting {path}: {exc}"}]}


async def move_file(params: dict) -> dict:
    src  = params["source"]
    dest = params["destination"]
    try:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dest)
        return {"content": [{"type": "text", "text": f"Moved {src} → {dest}"}]}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Error moving {src}: {exc}"}]}


async def run_command(params: dict) -> dict:
    command = params["command"]
    cwd     = params.get("cwd") or None
    timeout = int(params.get("timeout", 30))
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"content": [{"type": "text", "text": f"Command timed out after {timeout}s: {command}"}]}

        out = stdout.decode(errors="replace").rstrip()
        err = stderr.decode(errors="replace").rstrip()
        parts = [f"$ {command}", f"exit code: {proc.returncode}"]
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        if not out and not err:
            parts.append("(no output)")
        return {"content": [{"type": "text", "text": "\n".join(parts)}]}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Error running command: {exc}"}]}


async def search_files(params: dict) -> dict:
    pattern     = params["pattern"]
    path        = params.get("path", ".")
    file_glob   = params.get("file_glob")
    ignore_case = bool(params.get("ignore_case", False))
    try:
        rg_path   = shutil.which("rg")
        grep_path = shutil.which("grep")
        if not rg_path and not grep_path:
            return {"content": [{"type": "text", "text": "Neither rg nor grep found in PATH"}]}

        if rg_path:
            cmd = [rg_path, "-n", "--no-heading"]
            if ignore_case:
                cmd.append("-i")
            if file_glob:
                cmd.extend(["-g", file_glob])
            cmd.extend([pattern, path])
        else:
            cmd = [grep_path, "-r", "-n"]  # type: ignore[list-item]
            if ignore_case:
                cmd.append("-i")
            if file_glob:
                cmd.extend(["--include", file_glob])
            cmd.extend([pattern, path])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode(errors="replace").strip()

        if not output:
            return {"content": [{"type": "text", "text": f"No matches for '{pattern}'"}]}

        lines = output.splitlines()
        truncated = len(lines) > 100
        result = "\n".join(lines[:100])
        if truncated:
            result += f"\n… ({len(lines) - 100} more lines)"
        return {"content": [{"type": "text", "text": result}]}
    except asyncio.TimeoutError:
        return {"content": [{"type": "text", "text": "Search timed out"}]}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Error searching: {exc}"}]}


# ─── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    ToolDefinition(
        name="read_file",
        description=(
            "Read the full contents of a file. "
            "Optionally specify start_line and end_line to read only a range (1-indexed)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path":       {"type": "string",  "description": "File path"},
                "start_line": {"type": "integer", "description": "First line to return (1-indexed)"},
                "end_line":   {"type": "integer", "description": "Last line to return (inclusive)"},
            },
            "required": ["path"],
        },
        execute=read_file,
    ),
    ToolDefinition(
        name="write_file",
        description="Create a new file or completely overwrite an existing one with the given content.",
        parameters={
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Destination file path"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["path", "content"],
        },
        execute=write_file,
    ),
    ToolDefinition(
        name="edit_file",
        description=(
            "Replace an exact string in a file with new text. "
            "Prefer this over write_file for targeted edits — it is safer and uses fewer tokens. "
            "old_string must be unique in the file unless replace_all=true."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path":        {"type": "string",  "description": "File path to edit"},
                "old_string":  {"type": "string",  "description": "Exact text to find (must match including whitespace)"},
                "new_string":  {"type": "string",  "description": "Replacement text"},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        execute=edit_file,
    ),
    ToolDefinition(
        name="append_to_file",
        description="Append text to the end of a file, creating it if it does not exist.",
        parameters={
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "Text to append"},
            },
            "required": ["path", "content"],
        },
        execute=append_to_file,
    ),
    ToolDefinition(
        name="list_directory",
        description="List the files and subdirectories inside a directory, with file sizes.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to list (default: current directory)"},
            },
            "required": [],
        },
        execute=list_directory,
    ),
    ToolDefinition(
        name="create_directory",
        description="Create a directory (and any missing parent directories).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to create"},
            },
            "required": ["path"],
        },
        execute=create_directory,
    ),
    ToolDefinition(
        name="delete_file",
        description="Delete a file or directory. Directories are deleted recursively.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory path to delete"},
            },
            "required": ["path"],
        },
        execute=delete_file,
    ),
    ToolDefinition(
        name="move_file",
        description="Move or rename a file or directory.",
        parameters={
            "type": "object",
            "properties": {
                "source":      {"type": "string", "description": "Current path"},
                "destination": {"type": "string", "description": "New path"},
            },
            "required": ["source", "destination"],
        },
        execute=move_file,
    ),
    ToolDefinition(
        name="run_command",
        description=(
            "Execute a shell command and return stdout, stderr, and exit code. "
            "Use for running tests, installing packages, compiling, linting, git operations, etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string",  "description": "Shell command to execute"},
                "cwd":     {"type": "string",  "description": "Working directory (default: current)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
            },
            "required": ["command"],
        },
        execute=run_command,
    ),
    ToolDefinition(
        name="search_files",
        description=(
            "Search for a pattern in files using ripgrep (or grep as fallback). "
            "Returns matching lines with file:line prefixes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern":     {"type": "string",  "description": "Regex or literal search pattern"},
                "path":        {"type": "string",  "description": "File or directory to search (default: .)"},
                "file_glob":   {"type": "string",  "description": "File filter glob, e.g. '*.py' or '*.ts'"},
                "ignore_case": {"type": "boolean", "description": "Case-insensitive search (default: false)"},
            },
            "required": ["pattern"],
        },
        execute=search_files,
    ),
]

TOOL_NAMES = [t.name for t in TOOLS]
