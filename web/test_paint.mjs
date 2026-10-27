import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const paintFrame = createRequire(import.meta.url)("./paint.js");

const frame = { width: 100, height: 50 };

function recorder() {
  const calls = [];
  const ctx = { setTransform: (...a) => calls.push(["transform", ...a]) };
  return { calls, ctx, draw: (c, f) => calls.push(["draw", c, f]), fallback: (c, f) => calls.push(["fallback", c, f]) };
}

test("a loaded 2D skin is drawn, with the context and the frame, after the transform to CSS pixels is set", () => {
  const r = recorder();
  paintFrame(r.ctx, { skin: { info: { canvas: "2d" }, draw: r.draw, fallback: r.fallback }, dpr: 2, frame });
  assert.deepEqual(r.calls, [["transform", 2, 0, 0, 2, 0, 0], ["draw", r.ctx, frame]]);
});

test("the built-in orb is drawn, with the context and the frame, in CSS pixels when no skin has loaded", () => {
  const r = recorder();
  paintFrame(r.ctx, { skin: { info: null, fallback: r.fallback }, dpr: 1.5, frame });
  assert.deepEqual(r.calls, [["transform", 1.5, 0, 0, 1.5, 0, 0], ["fallback", r.ctx, frame]]);
});

test("a WebGL skin is drawn on a canvas whose transform is left alone", () => {
  const r = recorder();
  paintFrame(r.ctx, { skin: { info: { canvas: "webgl" }, draw: r.draw, fallback: r.fallback }, dpr: 2, frame });
  assert.deepEqual(r.calls, [["draw", r.ctx, frame]]);
});

test("nothing is drawn on a WebGL canvas while its skin is still loading", () => {
  const r = recorder();
  paintFrame(r.ctx, { skin: { info: { canvas: "webgl" }, fallback: r.fallback }, dpr: 2, frame });
  assert.deepEqual(r.calls, []);
});

test("a canvas kind other than webgl is treated as 2D", () => {
  const r = recorder();
  paintFrame(r.ctx, { skin: { info: { canvas: "bitmap" }, fallback: r.fallback }, dpr: 2, frame });
  assert.deepEqual(r.calls, [["transform", 2, 0, 0, 2, 0, 0], ["fallback", r.ctx, frame]]);
});
