import os from "node:os";
import path from "node:path";
import { joinSession } from "@github/copilot-sdk/extension";
import { openDoor, stateDir } from "../../channel/door.mjs";
import { RECORD_NAME, copilotRoutes } from "../../copilot/routes.mjs";

const session = await joinSession({});
const repoRoot = process.cwd();
openDoor({
  recordFile: path.join(stateDir(process.env.ORB_HOME || os.homedir(), repoRoot), RECORD_NAME),
  record: { sessionId: session.sessionId, repoRoot },
  routes: copilotRoutes(session),
  log: (message) => process.stderr.write(`[orb-extension] ${message}\n`),
});
