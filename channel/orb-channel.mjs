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
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Copilot reads the plugin's .mcp.json too; under it the channel would bind a port and write channel.json for
// nothing (test 0.1). COPILOT_CLI is the one variable only Copilot sets.
if (process.env.COPILOT_CLI === "1") process.exit(0);

const HOST = "127.0.0.1";
const MAX_BODY_BYTES = 32 * 1024;
const BIND_RETRY_MS = 3000;
const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = path.resolve(HERE, "..");
const INSTRUCTIONS = fs.readFileSync(path.join(PLUGIN_ROOT, "orb", "answer_style.txt"), "utf8").trim();

// Must match orb/state.py: <sanitized basename>-<first 12 hex of sha256(realpath, casefolded on Windows)>.
export function repoId(root) {
  const real = fs.realpathSync.native(root);
  const normalized = process.platform === "win32" ? real.toLowerCase() : real;
  const digest = crypto.createHash("sha256").update(normalized, "utf8").digest("hex").slice(0, 12);
  const name = path.basename(real).replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "repo";
  return `${name}-${digest}`;
}

export function stateDir(home, root) {
  return path.join(home, ".config", "orb", "state", repoId(root));
}

const REPO_ROOT = process.cwd();
const STATE_DIR = stateDir(process.env.ORB_HOME || os.homedir(), REPO_ROOT);
const CHANNEL_FILE = path.join(STATE_DIR, "channel.json");
const SECRET = crypto.randomBytes(24).toString("base64url");

function log(message) {
  process.stderr.write(`[orb-channel] ${message}\n`);
}

function recordedPort() {
  try {
    const port = JSON.parse(fs.readFileSync(CHANNEL_FILE, "utf8")).port;
    return Number.isInteger(port) && port >= 1024 && port <= 65535 ? port : null;
  } catch {
    return null;
  }
}

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", reject);
    probe.listen(0, HOST, () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

function writeRecord(port) {
  fs.mkdirSync(STATE_DIR, { recursive: true, mode: 0o700 });
  const tmp = CHANNEL_FILE + ".tmp";
  // sessionPid is the process that spawned this server: the Claude Code session itself. The interrupt adapter
  // sends Escape to that console and to no other.
  fs.writeFileSync(tmp, JSON.stringify({ port, secret: SECRET, pid: process.pid, sessionPid: process.ppid, repoRoot: REPO_ROOT, startedAt: new Date().toISOString() }, null, 2), { mode: 0o600 });
  fs.renameSync(tmp, CHANNEL_FILE);
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

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    req.on("data", (c) => {
      total += c.length;
      if (total > MAX_BODY_BYTES) {
        reject(new Error("request body too large"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"));
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

function reply(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function secretsMatch(given) {
  if (typeof given !== "string") return false;
  const a = Buffer.from(given), b = Buffer.from(SECRET);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

const httpServer = http.createServer(async (req, res) => {
  try {
    // A browser request always carries Origin; nothing in a browser may reach this server.
    if (req.headers.origin !== undefined) {
      reply(res, 403, { error: "browser origins are not accepted" });
      return;
    }
    if (!secretsMatch(req.headers["x-orb-secret"])) {
      reply(res, 403, { error: "missing or wrong channel secret" });
      return;
    }
    if (req.method === "GET" && req.url === "/health") {
      reply(res, 200, { ok: sessionBound });
      return;
    }
    if (req.method !== "POST" || req.url !== "/prompt") {
      reply(res, 404, { error: "not found" });
      return;
    }
    if (!/^application\/json\b/i.test(req.headers["content-type"] ?? "")) {
      reply(res, 415, { error: "Content-Type must be application/json" });
      return;
    }
    if (!sessionBound) {
      reply(res, 503, { error: "channel not bound to a Claude Code session" });
      return;
    }
    const body = await readJson(req);
    const text = String(body.text ?? "").trim();
    const requestId = String(body.req ?? "").trim();
    if (!text || !requestId) {
      reply(res, 400, { error: "body needs 'text' and 'req'" });
      return;
    }
    send({ jsonrpc: "2.0", method: "notifications/claude/channel", params: { content: text, meta: { req: requestId } } });
    reply(res, 202, { ok: true, req: requestId });
  } catch (err) {
    reply(res, 500, { error: String(err?.message ?? err) });
  }
});

async function listen() {
  const port = recordedPort() ?? (await freePort());
  let announced = false;
  httpServer.on("error", (err) => {
    if (err?.code !== "EADDRINUSE") {
      log(`http error: ${err.message}`);
      return;
    }
    // Another session of this repo holds the port. Keep MCP alive and take it when it frees; never steal it.
    if (!announced) {
      log(`port ${port} is held by another session; retrying every ${BIND_RETRY_MS} ms`);
      announced = true;
    }
    setTimeout(() => httpServer.listen(port, HOST), BIND_RETRY_MS);
  });
  httpServer.on("listening", () => {
    writeRecord(port);
    log(`listening on http://${HOST}:${port} for ${REPO_ROOT}`);
  });
  httpServer.listen(port, HOST);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  listen();
}
