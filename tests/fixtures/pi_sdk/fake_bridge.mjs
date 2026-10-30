import process from "node:process";
import readline from "node:readline";

let ignoreShutdown = false;
let writeChain = Promise.resolve();
let helloCount = 0;
let runCount = 0;
const activeRuns = new Map();

function frame(value) {
  return Buffer.from(`${JSON.stringify(value)}\n`, "utf8");
}

function write(value, fragmented = false) {
  const data = frame(value);
  writeChain = writeChain.then(() => new Promise((resolve) => {
    if (!fragmented) {
      process.stdout.write(data, resolve);
      return;
    }
    const split = Math.max(1, Math.floor(data.length / 2));
    process.stdout.write(data.subarray(0, split));
    setTimeout(() => process.stdout.write(data.subarray(split), resolve), 2);
  }));
  return writeChain;
}

function response(command, data) {
  write({ v: 1, type: "response", requestId: command.requestId, ok: true, data }, command.payload?.mode === "fragmented");
}

function handle(command) {
  const mode = command.payload?.mode || "healthy";
  if (command.type === "hello") {
    helloCount += 1;
    response(command, {
      protocols: mode === "incompatible" ? [99] : [1],
      bridge: { version: "fake" },
      sdk: {
        package: "@earendil-works/pi-coding-agent",
        version: mode === "sdk_mismatch" ? "9.9.9" : "0.84.2",
      },
      capabilities: { health: true, run: true, shutdown: true },
      limits: { maxFrameBytes: 1024, maxRunFrameBytes: 4096, maxImageBytes: 2048 },
      pid: process.pid,
    });
    return;
  }
  if (command.type === "health") {
    const modelRef = String(command.payload?.model || "");
    const [provider, id] = modelRef.split("/", 2);
    response(command, {
      status: "ready",
      ...(command.payload?.includeStats ? {
        helloCount,
        runCount,
        rssBytes: process.memoryUsage().rss,
      } : {}),
      ...(modelRef ? {
        model: {
          configured: true,
          available: modelRef === "provider/model",
          ...(modelRef === "provider/model" ? { provider, id } : { reason: "MODEL_UNAVAILABLE" }),
        },
      } : {}),
    });
    return;
  }
  if (command.type === "cancel") {
    const active = activeRuns.get(command.jobId);
    if (active) {
      clearTimeout(active.timer);
      activeRuns.delete(command.jobId);
      write({
        v: 1,
        type: "result",
        requestId: active.command.requestId,
        jobId: active.command.jobId,
        ok: false,
        error: { code: "CANCELED", message: "canceled", retryable: false },
      });
    }
    response(command, { canceled: Boolean(active) });
    return;
  }
  if (command.type === "approval_response") {
    const active = activeRuns.get(command.jobId);
    if (
      active?.command?.payload?.mode === "approval_wait"
      && command.approvalId === "appr-1"
      && command.approved === true
    ) {
      response(command, { accepted: true });
      active.finish();
      return;
    }
    response(command, {
      accepted: command.jobId === "job-approval"
        && command.approvalId === "appr-1"
        && command.approved === true
        && command.reason === "approved"
        && command.value?.choice === "yes"
        && command.payload === undefined,
    });
    return;
  }
  if (command.type === "shutdown") {
    if (ignoreShutdown) return;
    write({ v: 1, type: "response", ok: true, data: { status: "shutting_down" } }).then(() => process.exit(0));
    return;
  }
  if (command.type !== "run") {
    response(command, { ok: true });
    return;
  }

  if (mode === "exit") process.exit(23);
  if (mode === "stderr_exit") {
    process.stderr.write("Authorization: bearer-secret api_key=fixture-key token=fixture-token\n", () => process.exit(24));
    return;
  }
  if (mode === "stderr_sensitive") {
    const lines = [
      "noise-1",
      "Authorization: Bearer bearer-sensitive-token",
      "api_key=sensitive-api-key",
      'prompt="TOP SECRET PROMPT"',
      'data="QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="',
      "noise-2",
    ];
    process.stderr.write(`${lines.join("\n")}\n`, () => process.exit(25));
    return;
  }
  if (mode === "malformed") {
    process.stdout.write(Buffer.from("{not-json}\n"));
    return;
  }
  if (mode === "ignore_shutdown") ignoreShutdown = true;

  const sendEvent = () => write({
    v: 1,
    type: "event",
    jobId: command.jobId,
    sequence: 1,
    event: {
      kind: "message_delta",
      text: `event:${command.jobId}`,
      partial: true,
    },
  });

  const sendRun = () => {
    activeRuns.delete(command.jobId);
    runCount += 1;
    const eventCount = mode === "event_flood"
      ? Math.max(1, Number(command.payload?.eventCount || 8))
      : 1;
    if (mode !== "event_then_delayed") {
      for (let index = 1; index <= eventCount; index += 1) {
        write({
          v: 1,
          type: "event",
          jobId: command.jobId,
          sequence: index,
          event: {
            kind: "message_delta",
            text: eventCount === 1 ? `event:${command.jobId}` : `event:${command.jobId}:${index}`,
            partial: true,
          },
        }, mode === "fragmented");
      }
    }
    write({
      v: 1,
      type: "result",
      requestId: command.requestId,
      jobId: command.jobId,
      ok: true,
      data: { text: command.payload?.text || command.jobId || "ok", sideEffectStarted: false },
    }, mode === "fragmented");
  };

  const delay = Number(command.payload?.delayMs || (mode === "delayed" ? 200 : 0));
  if (mode === "event_then_delayed") sendEvent();
  if (mode === "approval_wait") {
    write({
      v: 1,
      type: "event",
      jobId: command.jobId,
      sequence: 1,
      event: {
        kind: "approval_request",
        approvalId: "appr-1",
        toolName: "write",
        summary: "Approve delayed completion",
      },
    });
    activeRuns.set(command.jobId, { command, timer: null, finish: sendRun });
  } else if (delay > 0) {
    const timer = setTimeout(sendRun, delay);
    activeRuns.set(command.jobId, { command, timer, finish: sendRun });
  } else sendRun();
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  if (!line.trim()) return;
  try {
    handle(JSON.parse(line));
  } catch (error) {
    process.stderr.write(`fake bridge parse error: ${error.message}\n`);
  }
});
rl.on("close", () => {
  if (!ignoreShutdown) process.exit(0);
});
