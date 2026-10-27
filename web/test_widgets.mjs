import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { MIN_WIDTH, MIN_HEIGHT, CORNER_INSET, SCALE_FLOOR, referenceOf, paneScale, scaledSize, scaledPlace, clampPlace, cornerPlace, toPixels, fromPixels } = createRequire(import.meta.url)("./widgets.js");

const pane = { width: 400, height: 200 };

test("a widget dragged past the right or bottom edge stops at it", () => {
  const place = clampPlace({ x: 0.95, y: 0.9, width: 100, height: 50 }, pane);
  assert.deepEqual(place, { x: 0.75, y: 0.75, width: 100, height: 50 });
});

test("a widget dragged past the left or top edge stops at it", () => {
  assert.deepEqual(clampPlace({ x: -0.2, y: -1, width: 100, height: 50 }, pane), { x: 0, y: 0, width: 100, height: 50 });
});

test("a widget is never smaller than the minimum nor larger than the pane", () => {
  const tiny = clampPlace({ x: 0, y: 0, width: 1, height: 1 }, pane);
  assert.deepEqual([tiny.width, tiny.height], [MIN_WIDTH, MIN_HEIGHT]);
  const huge = clampPlace({ x: 0.5, y: 0.5, width: 9000, height: 9000 }, pane);
  assert.deepEqual(huge, { x: 0, y: 0, width: 400, height: 200 });
});

test("a pane with no size leaves the place alone, so a hidden pane does not pile every widget at the origin", () => {
  const place = { x: 0.3, y: 0.4, width: 100, height: 50 };
  assert.deepEqual(clampPlace(place, { width: 0, height: 0 }), place);
});

test("each corner puts the widget inset from that corner", () => {
  const size = { width: 100, height: 50 };
  assert.deepEqual(cornerPlace("top-left", size, pane), { x: CORNER_INSET / 400, y: CORNER_INSET / 200, width: 100, height: 50 });
  assert.deepEqual(cornerPlace("bottom-right", size, pane), { x: (400 - 112) / 400, y: (200 - 62) / 200, width: 100, height: 50 });
  assert.equal(cornerPlace("top-right", size, pane).y, CORNER_INSET / 200);
  assert.equal(cornerPlace("bottom-left", size, pane).x, CORNER_INSET / 400);
});

test("a corner has no place yet in a pane with no size, rather than one at infinity", () => {
  assert.equal(cornerPlace("top-left", { width: 100, height: 50 }, { width: 0, height: 0 }), null);
  assert.equal(cornerPlace("bottom-right", { width: 100, height: 50 }, { width: 0, height: 0 }), null);
});

test("a corner place in a pane too small for the inset still lands inside the pane", () => {
  const place = cornerPlace("bottom-right", { width: 100, height: 50 }, { width: 60, height: 30 });
  assert.deepEqual(place, { x: 0, y: 0, width: 60, height: 30 });
});

test("pixels and fractions round-trip", () => {
  const place = { x: 0.25, y: 0.5, width: 100, height: 50 };
  assert.deepEqual(toPixels(place, pane), { left: 100, top: 100, width: 100, height: 50 });
  assert.deepEqual(fromPixels(toPixels(place, pane), pane), place);
});

test("a place read back from pixels is rounded, so the saved file holds whole pixels and short fractions", () => {
  assert.deepEqual(fromPixels({ left: 100.4, top: 33.333333, width: 170.49, height: 45.5 }, pane), { x: 0.251, y: 0.1667, width: 170, height: 46 });
});

test("a place names the pane it was made in only when both sides are positive numbers", () => {
  assert.deepEqual(referenceOf({ x: 0, y: 0, width: 1, height: 1, pane_width: 800, pane_height: 400 }), { width: 800, height: 400 });
  assert.equal(referenceOf({ x: 0, y: 0, width: 1, height: 1 }), null);
  assert.equal(referenceOf({ pane_width: 800 }), null);
  assert.equal(referenceOf({ pane_width: 0, pane_height: 400 }), null);
  assert.equal(referenceOf(null), null);
});

test("a widget scales with the pane's shorter side, the way the orb does", () => {
  const ref = { width: 800, height: 400 };
  assert.equal(paneScale(ref, { width: 400, height: 200 }), 0.5);
  assert.equal(paneScale(ref, { width: 800, height: 300 }), 0.75);
  assert.equal(paneScale(ref, { width: 1600, height: 800 }), 2);
  assert.deepEqual(scaledSize({ width: 200, height: 40 }, ref, { width: 800, height: 300 }), { width: 150, height: 30 });
});

test("a pane that only gets narrower or wider leaves the widget alone while the shorter side holds, as it does the orb", () => {
  const ref = { width: 2048, height: 1152 };
  assert.equal(paneScale(ref, { width: 1400, height: 1152 }), 1);
  assert.equal(paneScale(ref, { width: 3000, height: 1152 }), 1);
});

test("a pane whose shorter side is a third makes the widget a third the size, not stuck at half", () => {
  const scale = paneScale({ width: 900, height: 900 }, { width: 300, height: 900 });
  assert.ok(Math.abs(scale - 1 / 3) < 1e-9);
});

test("a widget stops shrinking at the floor, so it stays legible in a very small pane", () => {
  assert.equal(paneScale({ width: 800, height: 400 }, { width: 80, height: 40 }), SCALE_FLOOR);
  assert.deepEqual(scaledSize({ width: 200, height: 40 }, { width: 800, height: 400 }, { width: 80, height: 40 }), { width: 200 * SCALE_FLOOR, height: 40 * SCALE_FLOOR });
});

test("with no reference, or a pane with no size, the widget keeps its size", () => {
  assert.equal(paneScale(null, pane), 1);
  assert.equal(paneScale({ width: 800, height: 400 }, { width: 0, height: 0 }), 1);
  assert.equal(paneScale({ width: 0, height: 400 }, pane), 1);
  assert.deepEqual(scaledSize({ width: 200, height: 40 }, null, pane), { width: 200, height: 40 });
});

// Two widgets 5 px apart in a 2000 x 1000 pane: the left one 100 x 40 at (10, 10), the next 100 x 40 at (10, 55).
const reference = { width: 2000, height: 1000 };
const upper = { x: 10 / 2000, y: 10 / 1000, width: 100, height: 40 };
const lower = { x: 10 / 2000, y: 55 / 1000, width: 100, height: 40 };
const gapBetween = (a, b, pane) => b.y * pane.height - (a.y * pane.height + a.height);

test("a place in the pane it was made in comes back as it was", () => {
  const same = scaledPlace(upper, reference, reference);
  assert.ok(Math.abs(same.x - upper.x) < 1e-12 && Math.abs(same.y - upper.y) < 1e-12);
  assert.deepEqual([same.width, same.height], [100, 40]);
});

test("two widgets in one corner keep their spacing in proportion as the pane's shape changes", () => {
  for (const pane of [{ width: 1000, height: 500 }, { width: 600, height: 1000 }, { width: 3000, height: 500 }]) {
    const scale = paneScale(reference, pane);
    const a = scaledPlace(upper, reference, pane), b = scaledPlace(lower, reference, pane);
    assert.ok(Math.abs(gapBetween(a, b, pane) - 5 * scale) < 1e-9, `${pane.width}x${pane.height}`);
    assert.ok(Math.abs(a.x * pane.width - 10 * scale) < 1e-9);
  }
});

test("a widget nearer the right or bottom edge keeps its distance from that edge, scaled", () => {
  const right = { x: (2000 - 10 - 100) / 2000, y: (1000 - 10 - 40) / 1000, width: 100, height: 40 };
  const pane = { width: 1000, height: 1000 };  // narrower, same shorter side: nothing scales, the right edge moves in
  const placed = scaledPlace(right, reference, pane);
  assert.ok(Math.abs(pane.width - (placed.x * pane.width + placed.width) - 10) < 1e-9);
  assert.ok(Math.abs(pane.height - (placed.y * pane.height + placed.height) - 10) < 1e-9);
});

test("with no reference or a pane with no size, the place is left as it is", () => {
  assert.deepEqual(scaledPlace(upper, null, { width: 800, height: 400 }), upper);
  assert.deepEqual(scaledPlace(upper, reference, { width: 0, height: 0 }), upper);
});
