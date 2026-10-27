const MIN_WIDTH = 48;
const MIN_HEIGHT = 24;
const CORNER_INSET = 12;
const SCALE_FLOOR = 0.25;  // a widget never shrinks below this fraction of the size it was placed at, so it stays legible

// The pane a place was made in, or null when the place does not say.
function referenceOf(place) {
  return place && place.pane_width > 0 && place.pane_height > 0 ? { width: place.pane_width, height: place.pane_height } : null;
}

// One factor for both sides: how the pane's shorter side has changed, which is what sizes the orb itself (the orb's
// radius is a fraction of min(width, height)), so the widgets and the orb stay in proportion. A pane that only gets
// wider or only gets taller leaves them alone, as it does the orb.
function paneScale(reference, pane) {
  if (!reference || !(reference.width > 0) || !(reference.height > 0) || !(pane.width > 0) || !(pane.height > 0)) return 1;
  return Math.max(SCALE_FLOOR, Math.min(pane.width, pane.height) / Math.min(reference.width, reference.height));
}

function scaledSize(size, reference, pane) {
  const scale = paneScale(reference, pane);
  return { width: size.width * scale, height: size.height * scale };
}

// A widget's place in the pane as it is now, from the one it was made at. Each side is measured from the nearer edge
// of the reference pane, and that distance scales with the widget, so widgets that sit together keep their spacing and
// the whole block stays in proportion to the orb however the pane's shape changes.
function scaledPlace(place, reference, pane) {
  const scale = paneScale(reference, pane);
  if (!reference || !(pane.width > 0) || !(pane.height > 0)) return { x: place.x, y: place.y, width: place.width, height: place.height };
  const width = place.width * scale, height = place.height * scale;
  const side = (start, length, whole, now, scaled) => {
    const before = start, after = whole - start - length;
    return before <= after ? before * scale : now - after * scale - scaled;
  };
  const left = side(place.x * reference.width, place.width, reference.width, pane.width, width);
  const top = side(place.y * reference.height, place.height, reference.height, pane.height, height);
  return { x: left / pane.width, y: top / pane.height, width, height };
}

function clampPlace(place, pane) {
  if (!(pane.width > 0) || !(pane.height > 0)) return place;
  const width = Math.min(Math.max(place.width, MIN_WIDTH), pane.width);
  const height = Math.min(Math.max(place.height, MIN_HEIGHT), pane.height);
  const x = Math.min(Math.max(place.x, 0), 1 - width / pane.width);
  const y = Math.min(Math.max(place.y, 0), 1 - height / pane.height);
  return { x, y, width, height };
}

function cornerPlace(corner, size, pane) {
  if (!(pane.width > 0) || !(pane.height > 0)) return null;
  const [vertical, horizontal] = corner.split("-");
  const x = horizontal === "right" ? 1 - (size.width + CORNER_INSET) / pane.width : CORNER_INSET / pane.width;
  const y = vertical === "bottom" ? 1 - (size.height + CORNER_INSET) / pane.height : CORNER_INSET / pane.height;
  return clampPlace({ x, y, width: size.width, height: size.height }, pane);
}

function toPixels(place, pane) {
  return { left: place.x * pane.width, top: place.y * pane.height, width: place.width, height: place.height };
}

function fromPixels(rect, pane) {
  const fraction = (value) => Math.round(value * 10000) / 10000;
  return { x: fraction(rect.left / pane.width), y: fraction(rect.top / pane.height), width: Math.round(rect.width), height: Math.round(rect.height) };
}

if (typeof module !== "undefined") module.exports = { MIN_WIDTH, MIN_HEIGHT, CORNER_INSET, SCALE_FLOOR, referenceOf, paneScale, scaledSize, scaledPlace, clampPlace, cornerPlace, toPixels, fromPixels };
