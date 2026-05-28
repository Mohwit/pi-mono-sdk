/**
 * Cross-compile bridge.ts to standalone Bun binaries for all supported platforms.
 *
 * Usage:
 *   bun run bridge/build.ts            — build all 5 targets
 *   bun run bridge/build.ts --current  — build only the current platform
 */

import { existsSync, mkdirSync, statSync } from "fs";
import { resolve, dirname } from "path";
import { $ } from "bun";

// ─── Platform targets ─────────────────────────────────────────────────────────

const ALL_TARGETS = [
  { target: "bun-darwin-arm64", out: "pi-agent-bridge-darwin-arm64"  },
  { target: "bun-darwin-x64",   out: "pi-agent-bridge-darwin-x64"    },
  { target: "bun-linux-x64",    out: "pi-agent-bridge-linux-x64"     },
  { target: "bun-linux-arm64",  out: "pi-agent-bridge-linux-arm64"   },
  { target: "bun-windows-x64",  out: "pi-agent-bridge-win32-x64.exe" },
] as const;

// Map from Bun target to the current-platform target string
const CURRENT_TARGET_MAP: Record<string, string> = {
  "darwin arm64":  "bun-darwin-arm64",
  "darwin x64":    "bun-darwin-x64",
  "linux x64":     "bun-linux-x64",
  "linux arm64":   "bun-linux-arm64",
  "win32 x64":     "bun-windows-x64",
};

// ─── Paths ────────────────────────────────────────────────────────────────────

const BRIDGE_ROOT = dirname(resolve(import.meta.path));
const REPO_ROOT   = resolve(BRIDGE_ROOT, "..");
const BIN_DIR     = resolve(REPO_ROOT, "bin");
const ENTRY       = resolve(BRIDGE_ROOT, "bridge.ts");

// ─── Helpers ──────────────────────────────────────────────────────────────────

const MB = 1024 * 1024;
const PYPI_HARD_LIMIT_MB = 100;
const WARN_THRESHOLD_MB  = 95;

function formatSize(bytes: number): string {
  return (bytes / MB).toFixed(1) + " MB";
}

function currentBunTarget(): string | undefined {
  const key = `${process.platform} ${process.arch}`;
  return CURRENT_TARGET_MAP[key];
}

// ─── Build ────────────────────────────────────────────────────────────────────

async function build(target: string, outName: string): Promise<void> {
  const outFile = resolve(BIN_DIR, outName);

  console.log(`\nBuilding ${outName} (${target}) …`);

  await $`bun build --compile --minify --bytecode --target=${target} ${ENTRY} --outfile ${outFile}`;

  if (!existsSync(outFile)) {
    throw new Error(`Build succeeded but output not found: ${outFile}`);
  }

  const bytes = statSync(outFile).size;
  const label = formatSize(bytes);

  if (bytes > PYPI_HARD_LIMIT_MB * MB) {
    console.error(
      `  ERROR: ${label} — exceeds PyPI hard limit of ${PYPI_HARD_LIMIT_MB} MB!` +
      "\n  Consider: (a) pi-agent-binary sub-package, or (b) post-install downloader from GitHub Releases.",
    );
    process.exitCode = 1;
  } else if (bytes > WARN_THRESHOLD_MB * MB) {
    console.warn(
      `  WARNING: ${label} — approaching PyPI ${PYPI_HARD_LIMIT_MB} MB limit.`,
    );
  } else {
    console.log(`  OK: ${label}`);
  }
}

// ─── Main ─────────────────────────────────────────────────────────────────────

const currentOnly = process.argv.includes("--current");

let targets = ALL_TARGETS;

if (currentOnly) {
  const currentTarget = currentBunTarget();
  if (!currentTarget) {
    console.error(
      `Unsupported platform: ${process.platform} ${process.arch}\n` +
      `Supported: ${Object.keys(CURRENT_TARGET_MAP).join(", ")}`,
    );
    process.exit(1);
  }
  targets = ALL_TARGETS.filter((t) => t.target === currentTarget) as typeof ALL_TARGETS;
  if (targets.length === 0) {
    console.error(`No entry found for target: ${currentTarget}`);
    process.exit(1);
  }
}

// Ensure output directory exists
if (!existsSync(BIN_DIR)) {
  mkdirSync(BIN_DIR, { recursive: true });
  console.log(`Created: ${BIN_DIR}`);
}

console.log(
  currentOnly
    ? `Building current platform (${process.platform}/${process.arch}) …`
    : `Building all ${targets.length} targets …`,
);
console.log(`Entry:  ${ENTRY}`);
console.log(`Output: ${BIN_DIR}\n`);

let failed = 0;
for (const { target, out } of targets) {
  try {
    await build(target, out);
  } catch (err: any) {
    console.error(`  FAILED: ${err?.message ?? err}`);
    failed++;
  }
}

console.log(`\n${"─".repeat(60)}`);
console.log(
  `Done: ${targets.length - failed}/${targets.length} succeeded` +
  (failed > 0 ? ` — ${failed} failed` : ""),
);

if (failed > 0) process.exit(1);
