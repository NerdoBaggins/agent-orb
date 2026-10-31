import { Rejection } from "../channel/door.mjs";

export const RECORD_NAME = "copilot-channel.json";

export function copilotRoutes(session) {
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
    "POST /abort": async (body) => {
      sameSession(body);
      await session.abort();
      return [200, { aborted: true }];
    },
    "GET /tasks": async () => [200, { tasks: (await session.rpc.tasks.list()).tasks }],
  };
}
