// pulse: concentric rings that breathe at idle, ripple inward while listening, orbit while thinking, and expand
// with the audio level while speaking. Flat 2D canvas, no shader, no assets.
window.orbSkin = (() => {
  const TINT = {
    idle: [90, 209, 255],
    listening: [126, 231, 135],
    transcribing: [201, 168, 255],
    thinking: [255, 180, 84],
    speaking: [255, 122, 182],
  };
  const RINGS = 5;

  function rgba(c, a) {
    return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  }

  function draw(ctx, { state, level, time, width, height, scale }) {
    const cx = width / 2, cy = height / 2;
    const base = Math.min(width, height) * 0.16 * scale;
    const tint = TINT[state] || TINT.idle;
    const t = time / 1000;
    ctx.clearRect(0, 0, width, height);

    // core
    const breathe = state === "idle" ? Math.sin(t * 1.4) * 0.05 : 0;
    const core = base * (0.55 + breathe + (state === "speaking" ? level * 0.45 : 0));
    const glow = ctx.createRadialGradient(cx, cy, core * 0.1, cx, cy, core * 1.6);
    glow.addColorStop(0, rgba(tint, 0.95));
    glow.addColorStop(0.6, rgba(tint, 0.35));
    glow.addColorStop(1, rgba(tint, 0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, core * 1.6, 0, Math.PI * 2);
    ctx.fill();

    // rings
    for (let i = 0; i < RINGS; i++) {
      const phase = i / RINGS;
      let r, alpha = 0.35 - phase * 0.05, lw = 2;
      if (state === "listening") {
        const p = (1 - ((t * 0.6 + phase) % 1));
        r = base * (0.7 + p * 1.3);
        alpha *= 1 - p;
      } else if (state === "thinking") {
        r = base * (0.8 + phase * 0.9);
      } else if (state === "speaking") {
        r = base * (0.8 + phase * 0.9) * (1 + level * (0.3 + phase * 0.4));
        lw = 2 + level * 3;
      } else {
        r = base * (0.8 + phase * 0.9) * (1 + Math.sin(t * 1.4 + phase * 2) * 0.03);
      }
      ctx.beginPath();
      ctx.lineWidth = lw;
      ctx.strokeStyle = rgba(tint, Math.max(0, alpha));
      if (state === "thinking") {
        const start = t * (0.8 + phase) * (i % 2 ? -1 : 1);
        ctx.arc(cx, cy, r, start, start + Math.PI * 1.2);
      } else {
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
      }
      ctx.stroke();
    }

    // orbiting motes while thinking
    if (state === "thinking") {
      for (let i = 0; i < 3; i++) {
        const a = t * 1.5 + (i * Math.PI * 2) / 3;
        const r = base * 1.25;
        ctx.beginPath();
        ctx.fillStyle = rgba(tint, 0.9);
        ctx.arc(cx + Math.cos(a) * r, cy + Math.sin(a) * r, Math.max(2, base * 0.05), 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  return { draw };
})();
