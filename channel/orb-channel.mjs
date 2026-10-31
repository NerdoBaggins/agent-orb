#!/usr/bin/env node
// orb-channel: the Claude Code side of the agent link. Claude Code starts it as the plugin's MCP server
// (`plugin:agent-orb:orb-channel`); it declares the `claude/channel` capability and pushes each request the orb
// server POSTs to /prompt into the session as a channel message. Replies come back through the Stop hook, not here.
//
// No npm dependencies: the MCP surface used is initialize, notifications/initialized, ping and tools/list, all
// newline-delimited JSON-RPC on stdio.
//
// Pushes only reach the session when it was started with
//   --dangerously-load-development-channels plugin:agent-orb@<marketplace>
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Rejection, openDoor, stateDir } from "./door.mjs";

// Copilot reads the plugin's .mcp.json too; under it the channel would bind a port and write channel.json for
// nothing (test 0.1). COPILOT_CLI is the one variable only Copilot sets.
if (process.env.COPILOT_CLI === "1") process.exit(0);

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = path.resolve(HERE, "..");
const INSTRUCTIONS = fs.readFileSync(path.join(PLUGIN_ROOT, "orb", "answer_style.txt"), "utf8").trim();

const REPO_ROOT = process.cwd();
const CHANNEL_FILE = path.join(stateDir(process.env.ORB_HOME || os.homedir(), REPO_ROOT), "channel.json");

function log(message) {
  process.stderr.write(`[orb-channel] ${message}\n`);
}

// ---- MCP over stdio ----

let sessionBound = false;
const send = (message) => process.stdout.write(JSON.stringify(message) + "\n");

function handleMcp(message) {
  const { id, method } = message;
  if (method === "initialize") {
    send({ jsonrpc: "2.0", id, result: {
      protocolVersion: message.params?.protocolVersion ?? "2025-06-18",
      capabilities: { tools: {}, experimental: { "claude/channel": {} } },
      serverInfo: { name: "orb-channel", version: "0.1.0" },
      instructions: INSTRUCTIONS } });
  } else if (method === "notifications/initialized") {
    sessionBound = true;
  } else if (method === "ping") {
    send({ jsonrpc: "2.0", id, result: {} });
  } else if (method === "tools/list") {
    send({ jsonrpc: "2.0", id, result: { tools: [] } });
  } else if (id !== undefined && method) {
    send({ jsonrpc: "2.0", id, error: { code: -32601, message: `method not found: ${method}` } });
  }
}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let newline;
  while ((newline = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, newline).trim();
    buffer = buffer.slice(newline + 1);
    if (!line) continue;
    try {
      handleMcp(JSON.parse(line));
    } catch (err) {
      log(`bad message: ${err.message}`);
    }
  }
});
// With the session gone nothing can be delivered, and staying up would hold the port against the next one.
process.stdin.on("end", () => process.exit(0));
process.stdin.on("close", () => process.exit(0));

// ---- HTTP: the orb server's way in ----

const routes = {
  "GET /health": async () => [200, { ok: sessionBound }],
  "POST /prompt": async (body) => {
    if (!sessionBound) throw new Rejection(503, "channel not bound to a Claude Code session");
    const text = String(body.text ?? "").trim();
    const requestId = String(body.req ?? "").trim();
    if (!text || !requestId) throw new Rejection(400, "body needs 'text' and 'req'");
    send({ jsonrpc: "2.0", method: "notifications/claude/channel", params: { content: text, meta: { req: requestId } } });
    return [202, { ok: true, req: requestId }];
  },
};

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  openDoor({ recordFile: CHANNEL_FILE, record: { sessionPid: process.ppid, repoRoot: REPO_ROOT }, routes, log });
}
