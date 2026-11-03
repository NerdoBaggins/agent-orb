import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { FOLDER_SECTIONS, defaultLabel, folderSummary, pathPieces, extraRow, defaultsSummary, initiallyOpen, typedFolder } =
  createRequire(import.meta.url)("./folders.js");

const dir = (over = {}) => ({ path: "/a", resolved: "/a", exists: true, editable: true, ...over });

test("the sections are one per kind and sorted by name", () => {
  assert.deepEqual(FOLDER_SECTIONS.map((s) => s.kind), ["panel", "skin", "theme", "widget"]);
  assert.deepEqual(FOLDER_SECTIONS.map((s) => s.name), [...FOLDER_SECTIONS.map((s) => s.name)].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase())));
});

test("a section with no added folders says so and has no problem", () => {
  assert.deepEqual(folderSummary({ defaults: [], dirs: [] }), { extraText: "no added folders", missingText: "", problem: false });
  assert.equal(folderSummary(undefined).extraText, "no added folders");
});

test("a summary counts the extras and the missing ones, and a missing one is a problem", () => {
  const s = folderSummary({ dirs: [dir(), dir({ exists: false }), dir({ exists: false })] });
  assert.deepEqual(s, { extraText: "3 added folders", missingText: "2 not found", problem: true });
  assert.equal(folderSummary({ dirs: [dir()] }).extraText, "1 added folder");
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
  assert.deepEqual(missing.chips.map((c) => [c.text, c.level]), [["Folder not found", "warn"]]);
  assert.equal(missing.openable, false);
  assert.match(missing.notes[0], /isn't on disk right now/);
  const locked = extraRow(dir({ editable: false, source: "~/.config/orb/config.json" }));
  assert.deepEqual(locked.chips.map((c) => [c.text, c.level]), [["Set in ~/.config/orb/config.json", "dim"]]);
  assert.match(locked.notes[0], /remove it from ~\/\.config\/orb\/config\.json/);
  assert.equal(extraRow(dir({ editable: false })).chips[0].text, "Set in another config file");
  assert.equal(locked.removable, false);
  assert.equal(extraRow(dir()).removable, true);
  assert.deepEqual(extraRow(dir()).chips, []);
});

test("the defaults line names who each default folder is for and never prints a path", () => {
  assert.equal(defaultLabel("mine"), "Just this repo");
  assert.equal(defaultLabel("user"), "Every repo");
  assert.equal(defaultLabel("built-in"), "Built in");
  assert.equal(defaultLabel("brand-new"), "brand-new");
  assert.equal(defaultsSummary([{ scope: "mine", path: "/m/secret-9419" }, { scope: "user", path: "/u" }]), "Also searched: Just this repo and Every repo (see Default folders above).");
  assert.equal(defaultsSummary([{ scope: "built-in", path: "/plugin/cache/0.4.0" }]), "Also searched: Built in (see Default folders above).");
  assert.doesNotMatch(defaultsSummary([{ scope: "built-in", path: "/plugin/cache/0.4.0" }]), /cache/);
  assert.equal(defaultsSummary([]), "");
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
