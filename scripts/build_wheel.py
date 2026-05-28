"""
Build a platform-specific wheel containing the pre-compiled bridge binary.

Usage:
    python scripts/build_wheel.py \\
        --platform macosx_11_0_arm64 \\
        --binary bin/pi-agent-bridge-darwin-arm64

This script:
  1. Copies the binary into src/pi_agent/bin/ with executable permissions
  2. Runs `hatch build --target wheel` with the correct platform tag
  3. Cleans up src/pi_agent/bin/ after the build
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT   = Path(__file__).parent.parent
BIN_STAGING = REPO_ROOT / "src" / "pi_agent" / "bin"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a platform-specific wheel")
    parser.add_argument(
        "--platform",
        required=True,
        help="Wheel platform tag, e.g. macosx_11_0_arm64",
    )
    parser.add_argument(
        "--binary",
        required=True,
        help="Path to the compiled bridge binary (relative to repo root or absolute)",
    )
    args = parser.parse_args()

    binary_src = Path(args.binary)
    if not binary_src.is_absolute():
        binary_src = REPO_ROOT / binary_src

    if not binary_src.exists():
        sys.exit(f"Binary not found: {binary_src}")

    # 1. Stage binary
    BIN_STAGING.mkdir(parents=True, exist_ok=True)
    dest = BIN_STAGING / binary_src.name
    shutil.copy2(binary_src, dest)

    # Ensure executable bit is set before archiving — Python's zipfile preserves
    # permissions set at archive time, so this is critical for Unix wheels.
    mode = dest.stat().st_mode
    dest.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Staged: {dest} ({dest.stat().st_size / 1_048_576:.1f} MB)")

    # 2. Build wheel
    env = os.environ.copy()
    env["HATCH_BUILD_WHEEL_PLATFORM_TAG"] = args.platform
    try:
        subprocess.run(
            [sys.executable, "-m", "hatch", "build", "--target", "wheel"],
            cwd=REPO_ROOT,
            env=env,
            check=True,
        )
    finally:
        # 3. Clean up staging regardless of build outcome
        shutil.rmtree(BIN_STAGING, ignore_errors=True)
        print("Cleaned up staging directory.")


if __name__ == "__main__":
    main()
