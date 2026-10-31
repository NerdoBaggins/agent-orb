import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { explainChange, explainRelease, releaseRows } = createRequire(import.meta.url)("./releases.js");

const released = { version: "0.4.0", date: "2026-10-30", sections: [
  { heading: "Changed", entries: ["The orb opens only in Chrome (#97)."] },
  { heading: "Fixed", entries: ["A widget can be moved by any edge (#93).", "A 2D skin is no longer stretched (#91)."] },
] };
const unreleased = { version: "Unreleased", date: null, sections: [{ heading: "Added", entries: ["A release notes panel."] }] };

test("a request to explain one change names its release, its date, its heading and the change itself", () => {
  const text = explainChange(released, released.sections[1], released.sections[1].entries[0]);
  for (const part of ["0.4.0", "2026-10-30", "Fixed", "A widget can be moved by any edge (#93)."]) assert.ok(text.includes(part), part);
});

test("a change that is not released yet says so instead of giving a date", () => {
  const text = explainChange(unreleased, unreleased.sections[0], unreleased.sections[0].entries[0]);
  assert.ok(text.includes("not released yet") && !text.includes("null"));
});

test("a request to explain a release lists every change with its heading", () => {
  const text = explainRelease(released);
  for (const part of ["0.4.0, released 2026-10-30", "Changed: The orb opens only in Chrome (#97). Fixed: A widget can be moved by any edge (#93). Fixed: A 2D skin is no longer stretched (#91)."]) assert.ok(text.includes(part), part);
});

test("a change under no heading is explained without a heading and a release with only such changes lists them bare", () => {
  const first = { version: "0.1.0", date: "2026-10-27", sections: [{ heading: "", entries: ["Push to talk."] }] };
  assert.ok(!explainChange(first, first.sections[0], "Push to talk.").includes("under"));
  assert.ok(explainRelease(first).includes("Its changes: Push to talk. Give"));
  assert.deepEqual(releaseRows([first]).map((r) => r.kind), ["release", "entry"]);
});

test("the panel lists each release with its date, then each heading, then each change, and only releases and changes can be explained", () => {
  const rows = releaseRows([released, unreleased]);
  assert.deepEqual(rows.map((r) => [r.kind, r.text]), [
    ["release", "0.4.0 — 2026-10-30"], ["section", "Changed"], ["entry", "The orb opens only in Chrome (#97)."],
    ["section", "Fixed"], ["entry", "A widget can be moved by any edge (#93)."], ["entry", "A 2D skin is no longer stretched (#91)."],
    ["release", "Unreleased"], ["section", "Added"], ["entry", "A release notes panel."],
  ]);
  assert.ok(rows.filter((r) => r.kind !== "section").every((r) => typeof r.request === "string"));
  assert.ok(rows.filter((r) => r.kind === "section").every((r) => r.request === undefined));
});

test("no releases give no rows", () => {
  assert.deepEqual(releaseRows([]), []);
});
