import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { scopeLabel, panelRow: row, panelDirRow: dirRow, typedFolder } = createRequire(import.meta.url)("./panelsmenu.js");

const entry = (over = {}) => ({ name: "chart", title: "Chart", description: "A chart.", scope: "user", path: "/p/chart", shadowed: false, ...over });

test("a row names its scope in words and shows the folder in its tooltip", () => {
  const r = row(entry(), new Set());
  assert.equal(r.scope, "global");
  assert.match(r.tip, /A chart\.\n\/p\/chart/);
});

test("a built-in panel's tooltip says it is built in and its scope reads built-in", () => {
  const r = row(entry({ name: "transcript", scope: "built-in", path: null }), new Set());
  assert.equal(r.scope, "built-in");
  assert.match(r.tip, /built into the orb/);
});

test("a row is open only when its name is showing and it is not shadowed", () => {
  assert.equal(row(entry(), new Set(["chart"])).open, true);
  assert.equal(row(entry(), new Set(["other"])).open, false);
  assert.equal(row(entry({ shadowed: true }), new Set(["chart"])).open, false);
});

test("a shadowed row says which way showing its name resolves", () => {
  assert.match(row(entry({ shadowed: true }), new Set()).tip, /earlier folder has a panel with this name/);
  assert.doesNotMatch(row(entry(), new Set()).tip, /earlier folder/);
});

test("an unknown scope is shown as the server wrote it", () => {
  assert.equal(scopeLabel("brand-new"), "brand-new");
});

test("a folder row says when it is missing or set elsewhere, and only the user's own can be removed", () => {
  assert.deepEqual(dirRow({ path: "~/p", resolved: "/h/p", exists: true, editable: true }), { path: "~/p", resolved: "/h/p", note: "", removable: true });
  const other = dirRow({ path: "/g", resolved: "/g", exists: false, editable: false });
  assert.equal(other.note, "missing, set in another config file");
  assert.equal(other.removable, false);
});

test("a typed folder is trimmed and an empty one is refused", () => {
  assert.deepEqual(typedFolder("  /a/b \n"), { ok: true, folder: "/a/b" });
  assert.equal(typedFolder("   ").ok, false);
});
