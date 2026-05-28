import { Agent } from "@earendil-works/pi-agent-core";
import { getModel } from "@earendil-works/pi-ai";
// Register all provider APIs as side effects so raw model objects work at runtime
import "@earendil-works/pi-ai/anthropic";
import "@earendil-works/pi-ai/openai-completions";
import "@earendil-works/pi-ai/openai-responses";
import "@earendil-works/pi-ai/openai-codex-responses";
import "@earendil-works/pi-ai/google";
import "@earendil-works/pi-ai/mistral";
import { createInterface } from "readline";

// ─── Constants ───────────────────────────────────────────────────────────────

const PROTOCOL_VERSION = 1;
const MUTABLE_FIELDS = new Set(["model", "systemPrompt", "thinkingLevel", "tools"]);

// ─── State ────────────────────────────────────────────────────────────────────

let agent: Agent | null = null;

/** request_id of the currently in-flight prompt/continue call (for event routing). */
let activeRequestId: string | null = null;

/** Pending tool calls — keyed by toolCallId. */
const pendingTools = new Map<
  string,
  { resolve: (r: any) => void; reject: (e: Error) => void }
>();

/** Pending getApiKey calls — keyed by bridge-generated request_id. */
const pendingApiKeys = new Map<
  string,
  { resolve: (k: string) => void; reject: (e: Error) => void }
>();

// ─── Helpers ──────────────────────────────────────────────────────────────────

function send(obj: unknown): void {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

let _gakCounter = 0;
function nextGakId(): string {
  return `gak_${++_gakCounter}`;
}

/**
 * Build AgentTool array from serialized tool definitions sent by Python.
 * Each tool's execute() sends a tool_call to Python and awaits tool_result.
 */
function buildTools(toolDefs: any[]): any[] {
  return toolDefs.map((t: any) => ({
    name: t.name,
    label: t.label ?? "",
    description: t.description,
    parameters: t.parameters,
    executionMode: t.execution_mode ?? "parallel",
    execute: async (
      toolCallId: string,
      params: any,
      _signal: AbortSignal,
    ): Promise<any> => {
      return new Promise<any>((resolve, reject) => {
        pendingTools.set(toolCallId, { resolve, reject });
        send({ type: "tool_call", id: toolCallId, name: t.name, params });
      });
    },
  }));
}

// ─── Resolve result messages (bypass serial chain) ────────────────────────────

/**
 * tool_result and api_key_result resolve Promises that are suspended INSIDE
 * the serial chain (inside agent.prompt() / agent.continue()). They MUST be
 * handled immediately — queuing them through the serial chain would deadlock
 * because the chain is blocked waiting for the very Promise they resolve.
 */
function resolveToolResult(msg: any): void {
  const pending = pendingTools.get(msg.id);
  if (!pending) return;
  pendingTools.delete(msg.id);
  if (msg.is_error) {
    pending.reject(new Error(msg.result?.content?.[0]?.text ?? "Tool execution failed"));
  } else {
    pending.resolve(msg.result);
  }
}

function resolveApiKeyResult(msg: any): void {
  const pending = pendingApiKeys.get(msg.request_id);
  if (!pending) return;
  pendingApiKeys.delete(msg.request_id);
  if (msg.error) {
    pending.reject(new Error(msg.error));
  } else {
    pending.resolve(msg.key);
  }
}

// ─── Readline + serial handler ────────────────────────────────────────────────

const rl = createInterface({ input: process.stdin, terminal: false });

// Serial promise chain — prevents concurrent agent.prompt() calls.
// Node's readline fires the next "line" event before an async handler resolves,
// so without this a second prompt could launch a concurrent agent call.
let chain = Promise.resolve();

rl.on("line", (line: string) => {
  // Parse early so we can fast-path result messages before queuing.
  let parsed: any;
  try {
    parsed = JSON.parse(line);
  } catch {
    send({ type: "error", request_id: null, message: "Invalid JSON", stack: "" });
    return;
  }

  // Result messages resolve in-flight Promises inside agent.prompt()/continue().
  // They MUST bypass the serial chain — see resolveToolResult/resolveApiKeyResult.
  if (parsed.type === "tool_result") {
    resolveToolResult(parsed);
    return;
  }
  if (parsed.type === "api_key_result") {
    resolveApiKeyResult(parsed);
    return;
  }

  chain = chain.then(() => handleLine(parsed)).catch(() => {});
});

rl.on("close", () => {
  agent?.abort();
  process.stdout.end(() => process.exit(0));
});

// ─── Message handler ──────────────────────────────────────────────────────────

async function handleLine(msg: any): Promise<void> {
  const { request_id } = msg;

  try {
    switch (msg.type) {
      // ── initialize ─────────────────────────────────────────────────────────
      case "initialize": {
        const opts = msg.options ?? {};

        // Resolve model: raw object (local/custom) or getModel() lookup
        const model = opts.model.api
          ? opts.model          // already a complete Model object (e.g. LocalModelConfig)
          : getModel(opts.model.provider, opts.model.name);

        const agentOptions: any = {
          initialState: {
            systemPrompt: opts.systemPrompt,
            model,
            thinkingLevel: opts.thinkingLevel ?? undefined,
            tools: buildTools(opts.tools ?? []),
            messages: opts.messages ?? [],
          },
          toolExecution: opts.toolExecution,
          steeringMode: opts.steeringMode,
          followUpMode: opts.followUpMode,
        };

        // Wire getApiKey roundtrip if Python has a callback
        if (opts.has_get_api_key) {
          agentOptions.getApiKey = async (provider: string): Promise<string> => {
            return new Promise<string>((resolve, reject) => {
              const gakId = nextGakId();
              pendingApiKeys.set(gakId, { resolve, reject });
              send({ type: "get_api_key", request_id: gakId, provider });
            });
          };
        }

        agent = new Agent(agentOptions);

        // Forward all SDK events to Python, tagged with the active request_id
        agent.subscribe((event: any) => {
          send({ type: "event", request_id: activeRequestId, event });
        });

        send({ type: "ready", protocol_version: PROTOCOL_VERSION });
        break;
      }

      // ── prompt ─────────────────────────────────────────────────────────────
      case "prompt": {
        activeRequestId = request_id;
        try {
          const attachments =
            Array.isArray(msg.attachments) && msg.attachments.length > 0
              ? msg.attachments
              : undefined;
          await agent!.prompt(msg.text, attachments);
          send({ type: "prompt_done", request_id });
        } finally {
          activeRequestId = null;
        }
        break;
      }

      // ── continue ───────────────────────────────────────────────────────────
      case "continue": {
        activeRequestId = request_id;
        try {
          await (agent! as any).continue();
          send({ type: "continue_done", request_id });
        } finally {
          activeRequestId = null;
        }
        break;
      }

      // ── wait_for_idle ──────────────────────────────────────────────────────
      case "wait_for_idle": {
        await agent!.waitForIdle();
        send({ type: "idle", request_id });
        break;
      }

      // ── fire-and-forget controls ───────────────────────────────────────────
      case "abort":
        agent!.abort();
        break;

      case "reset":
        agent!.reset();
        break;

      case "steer":
        agent!.steer(msg.message);
        break;

      case "follow_up":
        agent!.followUp(msg.message);
        break;

      case "clear_steering":
        agent!.clearSteeringQueue();
        break;

      case "clear_follow_up":
        agent!.clearFollowUpQueue();
        break;

      case "clear_all":
        agent!.clearAllQueues();
        break;

      // ── set_state ──────────────────────────────────────────────────────────
      case "set_state": {
        if (!MUTABLE_FIELDS.has(msg.field)) {
          send({
            type: "error",
            request_id,
            message:
              `Field '${msg.field}' is not mutable. ` +
              `Mutable fields: ${[...MUTABLE_FIELDS].join(", ")}`,
            stack: "",
          });
          break;
        }

        let value = msg.value;
        if (msg.field === "model") {
          value = msg.value.api
            ? msg.value
            : getModel(msg.value.provider, msg.value.name);
        } else if (msg.field === "tools") {
          value = buildTools(msg.value ?? []);
        }

        (agent!.state as any)[msg.field] = value;
        break;
      }

      // ── shutdown ───────────────────────────────────────────────────────────
      case "shutdown":
        // Flush stdout before exiting to prevent truncation of buffered output
        process.stdout.end(() => process.exit(0));
        break;

      default:
        send({
          type: "error",
          request_id,
          message: `Unknown message type: ${String(msg.type)}`,
          stack: "",
        });
    }
  } catch (err: any) {
    // Backstop: surface any unexpected error to the waiting Python coroutine
    send({
      type: "error",
      request_id,
      message: err?.message ?? String(err),
      stack: err?.stack ?? "",
    });
  }
}
