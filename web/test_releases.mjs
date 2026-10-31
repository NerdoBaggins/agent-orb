import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { explainChange, explainRelease } = createRequire(import.meta.url)("./releases.js");

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
  for (const part of ["Changed: The orb opens only in Chrome (#97).", "Fixed: A widget can be moved by any edge (#93).", "Fixed: A 2D skin is no longer stretched (#91)."]) assert.ok(text.includes(part), part);
});
