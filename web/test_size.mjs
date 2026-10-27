import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { NORMAL_SIZE, snapSize, sizeLabel } = createRequire(import.meta.url)("./size.js");

test("a size close to normal snaps to normal", () => {
  assert.equal(snapSize(1.05), NORMAL_SIZE);
  assert.equal(snapSize(0.95), NORMAL_SIZE);
});

test("a size well away from normal is kept", () => {
  assert.equal(snapSize(1.1), 1.1);
  assert.equal(snapSize(0.5), 0.5);
});

test("normal is labelled as normal and any other size as a multiplier", () => {
  assert.equal(sizeLabel(NORMAL_SIZE), "normal");
  assert.equal(sizeLabel(1.5), "1.50×");
});
