import { Rejection } from "../channel/door.mjs";

export const RECORD_NAME = "copilot-channel.json";

// Copilot 1.0.109's commands.enqueue accepts /compact and never runs it.
const COMPACT = /^\/compact(?:\s+([\s\S]*))?$/;

export function copilotRoutes(session, log) {
  const sameSession = (body) => {
    if (body.session !== session.sessionId) throw new Rejection(409, `this door is session ${session.sessionId} now`);
  };
  return {
    "GET /health": async () => [200, { ok: true, sessionId: session.sessionId }],
    "POST /prompt": async (body) => {
      sameSession(body);
      const text = String(body.text ?? "").trim();
      if (!text) throw new Rejection(400, "body needs 'text'");
      return [200, { messageId: await session.send({ prompt: text }) }];
    },
    "POST /command": async (body) => {
      sameSession(body);
      const compact = COMPACT.exec(String(body.text ?? "").trim());
      if (!compact) throw new Rejection(400, "body needs 'text', a /compact command");
      session.rpc.history.compact({ trigger: "manual", customInstructions: compact[1] })
        .catch((error) => log(`/compact failed: ${error}`));
      return [202, { compacting: true }];
    },
    "POST /abort": async (body) => {
      sameSession(body);
      await session.abort();
      return [200, { aborted: true }];
    },
    "GET /tasks": async () => [200, { tasks: (await session.rpc.tasks.list()).tasks }],
  };
}
