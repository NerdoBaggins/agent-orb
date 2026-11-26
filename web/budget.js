const MAX_CANVAS_PIXELS = 1.5e6;
const ACTIVE_STATES = ["listening", "speaking", "thinking"];
const VSYNC_SLACK_MS = 2;

function pixelRatio(width, height, deviceRatio, maxDpr) {
  const ratio = Math.min(deviceRatio, maxDpr);
  const area = width * height;
  return area * ratio * ratio > MAX_CANVAS_PIXELS ? Math.sqrt(MAX_CANVAS_PIXELS / area) : ratio;
}

function paintRate({ state, visibility, focused, rendering }, { fps, idle_fps }) {
  if (visibility === "hidden") return 0;
  const busy = ACTIVE_STATES.includes(state) && focused && !rendering;
  return busy ? fps : Math.min(fps, idle_fps);
}

function isPaintDue(now, lastPaint, rate) {
  return rate > 0 && now - lastPaint >= 1000 / rate - VSYNC_SLACK_MS;
}

if (typeof module !== "undefined") module.exports = { MAX_CANVAS_PIXELS, VSYNC_SLACK_MS, pixelRatio, paintRate, isPaintDue };
