import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { MAX_CANVAS_PIXELS, VSYNC_SLACK_MS, pixelRatio, paintRate, isPaintDue } = createRequire(import.meta.url)("./budget.js");

const CAPS = { fps: 30, idle_fps: 10 };
const ACTIVE = { state: "speaking", visibility: "visible", focused: true, rendering: false };

test("a Retina pane is drawn at the configured pixel ratio, not the display's", () => {
  assert.equal(pixelRatio(400, 300, 2, 1.5), 1.5);
});

test("a display below the configured ratio is drawn at its own ratio, never upscaled", () => {
  assert.equal(pixelRatio(400, 300, 1, 1.5), 1);
});

test("a full-screen pane is scaled down so its canvas holds no more than the pixel budget", () => {
  const ratio = pixelRatio(2560, 1440, 2, 1.5);
  assert.ok(ratio < 1.5);
  assert.ok(Math.abs(2560 * ratio * 1440 * ratio - MAX_CANVAS_PIXELS) < 1);
});

test("a raised ratio setting draws a small pane at the display's full ratio", () => {
  assert.equal(pixelRatio(400, 300, 2, 4), 2);
});

test("an active, focused window paints at the full frame rate", () => {
  assert.equal(paintRate(ACTIVE, CAPS), 30);
});

test("an idle window paints at the idle rate", () => {
  assert.equal(paintRate({ ...ACTIVE, state: "idle" }, CAPS), 10);
});

test("each of listening, speaking and thinking counts as active", () => {
  for (const state of ["listening", "speaking", "thinking"]) assert.equal(paintRate({ ...ACTIVE, state }, CAPS), 30, state);
});

test("an unfocused window paints at the idle rate however active it is", () => {
  assert.equal(paintRate({ ...ACTIVE, focused: false }, CAPS), 10);
});

test("every window paints at the idle rate while a streamed speech render is in flight", () => {
  assert.equal(paintRate({ ...ACTIVE, rendering: true }, CAPS), 10);
});

test("a hidden window does not paint", () => {
  assert.equal(paintRate({ ...ACTIVE, visibility: "hidden" }, CAPS), 0);
});

test("an idle rate set above the frame rate does not raise an idle window past the frame rate", () => {
  assert.equal(paintRate({ ...ACTIVE, state: "idle" }, { fps: 30, idle_fps: 60 }), 30);
});

test("a frame inside the interval since the last paint is skipped", () => {
  assert.equal(isPaintDue(1016.7, 1000, 30), false);
});

test("a frame a full interval after the last paint is painted", () => {
  assert.equal(isPaintDue(1033.4, 1000, 30), true);
});

test("a frame that lands a little early from display jitter is still painted", () => {
  assert.equal(isPaintDue(1000 + 1000 / 30 - VSYNC_SLACK_MS / 2, 1000, 30), true);
});

test("nothing is due at a rate of zero, however long since the last paint", () => {
  assert.equal(isPaintDue(1e9, 0, 0), false);
});
