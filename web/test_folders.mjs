import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { FOLDER_SECTIONS, defaultLabel, folderSummary, pathPieces, extraRow, defaultRow, initiallyOpen, typedFolder } =
  createRequire(import.meta.url)("./folders.js");

const dir = (over = {}) => ({ path: "/a", resolved: "/a", exists: true, editable: true, ...over });

test("the sections are one per kind and sorted by name", () => {
  assert.deepEqual(FOLDER_SECTIONS.map((s) => s.kind), ["panel", "skin", "theme", "widget"]);
  assert.deepEqual(FOLDER_SECTIONS.map((s) => s.name), [...FOLDER_SECTIONS.map((s) => s.name)].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase())));
});

test("a section with no extra folders says defaults only and has no problem", () => {
  assert.deepEqual(folderSummary({ defaults: [], dirs: [] }), { extra: 0, missing: 0, extraText: "defaults only", missingText: "", text: "defaults only", problem: false });
  assert.equal(folderSummary(undefined).text, "defaults only");
});

test("a summary counts the extras and the missing ones, and a missing one is a problem", () => {
  const s = folderSummary({ dirs: [dir(), dir({ exists: false }), dir({ exists: false })] });
  assert.deepEqual(s, { extra: 3, missing: 2, extraText: "3 extra", missingText: "2 missing", text: "3 extra · 2 missing", problem: true });
  assert.equal(folderSummary({ dirs: [dir()] }).text, "1 extra");
});

test("a path is cut after each separator and the pieces join back to the path", () => {
  const win = "C:\\Users\\agon2\\panels";
  assert.deepEqual(pathPieces(win), ["C:\\", "Users\\", "agon2\\", "panels"]);
  assert.equal(pathPieces(win).join(""), win);
  assert.deepEqual(pathPieces("~/a/b"), ["~/", "a/", "b"]);
  assert.deepEqual(pathPieces(""), []);
});

test("a missing extra folder carries a warn chip, and one set in another file cannot be removed", () => {
  const missing = extraRow(dir({ exists: false }));
  assert.deepEqual(missing.chips.map((c) => [c.text, c.level]), [["missing", "warn"]]);
  assert.equal(missing.openable, false);
  const locked = extraRow(dir({ editable: false }));
  assert.deepEqual(locked.chips.map((c) => [c.text, c.level]), [["set in another config file", "dim"]]);
  assert.equal(locked.removable, false);
  assert.equal(extraRow(dir()).removable, true);
  assert.deepEqual(extraRow(dir()).chips, []);
});

test("an always-searched folder is labelled by who it is for and one not made yet is dim, not a warning", () => {
  assert.equal(defaultLabel("mine"), "This repo");
  assert.equal(defaultLabel("user"), "All repos");
  assert.equal(defaultLabel("built-in"), "Bundled with the orb");
  assert.equal(defaultLabel("brand-new"), "brand-new");
  const none = defaultRow({ scope: "mine", path: "/m", exists: false });
  assert.deepEqual(none.chips.map((c) => [c.text, c.level]), [["not created yet", "dim"]]);
  assert.equal(none.openable, false);
  assert.deepEqual(defaultRow({ scope: "user", path: "/u", exists: true }).chips, []);
});

test("a section starts open when the user left it open or when it has a problem, and not otherwise", () => {
  const states = { panel: { dirs: [] }, skin: { dirs: [dir({ exists: false })] }, theme: { dirs: [dir()] }, widget: { dirs: [] } };
  assert.deepEqual([...initiallyOpen(["theme"], states)].sort(), ["skin", "theme"]);
  assert.deepEqual([...initiallyOpen(null, states)], ["skin"]);
  assert.deepEqual([...initiallyOpen(["panel", 7, null], {})].sort(), ["panel"]);
});

test("a typed folder is trimmed and an empty one is refused with a sentence", () => {
  assert.deepEqual(typedFolder("  /a/b \n"), { ok: true, folder: "/a/b" });
  assert.deepEqual(typedFolder("   "), { ok: false, error: "Give a folder to add." });
});
