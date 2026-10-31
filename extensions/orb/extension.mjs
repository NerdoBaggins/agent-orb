// The Copilot side of the agent link. Copilot CLI starts it from the plugin's extensions folder and joins it to the
// foreground session over stdio; it opens the orb's locked door into that session.
import os from "node:os";
import path from "node:path";
import { joinSession } from "@github/copilot-sdk/extension";
import { openDoor, stateDir } from "../../channel/door.mjs";
import { copilotRoutes, recordName } from "../../copilot/routes.mjs";

const selector = process.env.ORB_COPILOT_PORT || null;
const session = await joinSession({});
const repoRoot = process.cwd();
openDoor({
  recordFile: path.join(stateDir(process.env.ORB_HOME || os.homedir(), repoRoot), recordName(selector)),
  record: { sessionId: session.sessionId, repoRoot },
  routes: copilotRoutes(session),
  log: (message) => process.stderr.write(`[orb-extension] ${message}\n`),
  port: selector ? Number(selector) : null,
});
