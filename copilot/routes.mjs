// What the orb may ask of the Copilot session its extension joined. Must match orb/state.py's copilot_channel_file.
import { Rejection } from "../channel/door.mjs";

export function recordName(selector) {
  return selector ? `copilot-channel-${selector}.json` : "copilot-channel.json";
}

export function copilotRoutes(session) {
  return {
    "GET /health": async () => [200, { ok: true, sessionId: session.sessionId }],
    "POST /prompt": async (body) => {
      const text = String(body.text ?? "").trim();
      if (!text) throw new Rejection(400, "body needs 'text'");
      return [200, { messageId: await session.send({ prompt: text }) }];
    },
    "POST /abort": async () => {
      await session.abort();
      return [200, { aborted: true }];
    },
    "GET /tasks": async () => [200, { tasks: (await session.rpc.tasks.list()).tasks }],
  };
}
