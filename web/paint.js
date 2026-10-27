function paintFrame(ctx, { skin, dpr, frame }) {
  const flat = !skin.info || skin.info.canvas !== "webgl";
  if (flat) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (skin.draw) skin.draw(ctx, frame);
  else if (flat) skin.fallback(ctx, frame);
}
if (typeof module !== "undefined") module.exports = paintFrame;
