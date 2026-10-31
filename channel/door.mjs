import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import path from "node:path";

const HOST = "127.0.0.1";
const MAX_BODY_BYTES = 32 * 1024;
const BIND_RETRY_MS = 3000;

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

export class Rejection extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function previousDoor(recordFile) {
  try {
    const { port, secret } = JSON.parse(fs.readFileSync(recordFile, "utf8"));
    return { port: Number.isInteger(port) && port >= 1024 && port <= 65535 ? port : null, secret: String(secret ?? "") };
  } catch {
    return { port: null, secret: "" };
  }
}

function answersAsDoor({ port, secret }) {
  return new Promise((resolve) => {
    const probe = http.get({ host: HOST, port, path: "/health", headers: { "x-orb-secret": secret }, timeout: 1000 },
      (res) => { res.resume(); resolve(res.statusCode === 200); });
    probe.on("timeout", () => probe.destroy());
    probe.on("error", () => resolve(false));
  });
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

function writeRecord(recordFile, fields) {
  fs.mkdirSync(path.dirname(recordFile), { recursive: true, mode: 0o700 });
  const tmp = recordFile + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(fields, null, 2), { mode: 0o600 });
  fs.renameSync(tmp, recordFile);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    req.on("data", (c) => {
      total += c.length;
      if (total > MAX_BODY_BYTES) {
        reject(new Rejection(413, "request body too large"));
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"));
      } catch {
        reject(new Rejection(400, "body is not JSON"));
      }
    });
    req.on("error", reject);
  });
}

function reply(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function secretsMatch(given, secret) {
  if (typeof given !== "string") return false;
  const a = Buffer.from(given), b = Buffer.from(secret);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

export function openDoor({ recordFile, record, routes, log }) {
  const secret = crypto.randomBytes(24).toString("base64url");
  const server = http.createServer(async (req, res) => {
    try {
      if (req.headers.origin !== undefined) throw new Rejection(403, "browser origins are not accepted");
      if (!secretsMatch(req.headers["x-orb-secret"], secret)) throw new Rejection(403, "missing or wrong secret");
      const handler = routes[`${req.method} ${req.url}`];
      if (!handler) throw new Rejection(404, "not found");
      if (req.method === "POST" && !/^application\/json\b/i.test(req.headers["content-type"] ?? "")) {
        throw new Rejection(415, "Content-Type must be application/json");
      }
      const body = req.method === "POST" ? await readJson(req) : {};
      reply(res, ...(await handler(body)));
    } catch (err) {
      reply(res, err instanceof Rejection ? err.status : 500, { error: String(err?.message ?? err) });
    }
  });

  (async () => {
    const previous = previousDoor(recordFile);
    const port = previous.port ?? (await freePort());
    let announced = false;
    server.on("error", async (err) => {
      if (err?.code !== "EADDRINUSE") {
        log(`http error: ${err.message}`);
        return;
      }
      if (!(await answersAsDoor(previousDoor(recordFile)))) {
        server.listen(await freePort(), HOST);
        return;
      }
      if (!announced) {
        log(`port ${port} is held by another live session of this repo; retrying every ${BIND_RETRY_MS} ms`);
        announced = true;
      }
      setTimeout(() => server.listen(port, HOST), BIND_RETRY_MS);
    });
    server.on("listening", () => {
      writeRecord(recordFile, { port: server.address().port, secret, pid: process.pid, ...record, startedAt: new Date().toISOString() });
      log(`listening on http://${HOST}:${server.address().port}`);
    });
    server.listen(port, HOST);
  })();
  return server;
}
