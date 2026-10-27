// The Copilot side of the agent link. Driven by orb/copilot.py: argv is
//   <sdk index.js file URL> <port> <foreground|send|abort|tasks> <timeout ms> <expected session id>
// The prompt arrives on stdin for `send`. One JSON object is printed on success; errors go to stderr, exit 1.
// No npm dependencies: the SDK is the one Copilot ships inside its own install.
const [sdkUrl, port, mode, timeoutMs, expected] = process.argv.slice(2);

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

const sdk = await import(sdkUrl);
const client = new sdk.CopilotClient({ connection: sdk.RuntimeConnection.forUri(`127.0.0.1:${port}`) });
const budget = Number(timeoutMs) + 15000;
const deadline = setTimeout(() => {
  console.error(`no answer from 127.0.0.1:${port} within ${budget} ms`);
  process.exit(1);
}, budget);

let result;
try {
  await client.start();
  const sessionId = (await client.getForegroundSessionId()) ?? null;
  if (mode === "foreground") {
    result = { sessionId };
  } else {
    if (sessionId !== expected) {
      throw new Error(`the Copilot window on port ${port} shows ${sessionId ?? "no session"}, not ${expected}; nothing sent`);
    }
    const session = await client.resumeSession(sessionId, { suppressResumeEvent: true });
    if (mode === "abort") {
      await session.abort();
      result = { sessionId, aborted: true };
    } else if (mode === "tasks") {
      result = { sessionId, tasks: (await session.rpc.tasks.list()).tasks };
    } else if (mode === "send") {
      // Returns as soon as Copilot has the message. sendAndWait waits for session.idle, which an attached background
      // shell holds off until it finishes; orb/copilot.py follows the reply in the session's event log instead, by
      // the message id returned here, which is the id the replies carry as originatingMessageId.
      const messageId = await session.send({ prompt: await readStdin() });
      result = { sessionId, messageId };
    } else {
      throw new Error(`unknown mode ${mode}`);
    }
  }
} catch (e) {
  console.error(e?.message ?? String(e));
  process.exitCode = 1;
} finally {
  await client.stop().catch(() => {});
  clearTimeout(deadline);
}
if (result) console.log(JSON.stringify(result));
