# Orb skins

A skin is a folder:

```
skin.json   the manifest (below)
skin.js     sets window.orbSkin = { init?(canvas, ctx, env), draw(ctx, frame) }
...         anything else the skin needs: modules, shaders, a vendored library
```

```json
{
  "name": "<folder name>",
  "version": "1.0.0",
  "canvas": "2d",
  "states": ["idle", "listening", "transcribing", "thinking", "speaking"],
  "options": {
    "background": { "label": "Background", "choices": ["nebula", "hud", "eye"], "default": "nebula", "key": "KeyB" }
  }
}
```

- `canvas`: `"2d"` (the default) gets a 2D context with the device pixel ratio already applied; `"webgl"` gets a
  WebGL2 context and handles size and pixel ratio itself.
- `states`: the states the skin draws, out of `idle`, `wake_word`, `listening`, `transcribing`, `thinking`,
  `working`, `waiting`, `speaking`. Left out, it is the first five. A state the skin does not list is drawn as its
  stand-in: `wake_word` as `listening`, `working` as `thinking`, `waiting` as `idle`.
- `options`: choices the user makes from the orb's bar (hover it) or by the option's key while the window has focus.
  A choice is kept in the user's config for that repo. Left out, there are none.

`draw(ctx, frame)` is called once per animation frame. It must clear and draw the whole frame, and draw well at any
size.

```js
frame = {
  state:   "idle" | "wake_word" | "listening" | "transcribing" | "thinking" | "working" | "waiting" | "speaking",
  level:   0..1     // mic level while listening, playback level while speaking, else 0
  time:    ms       // monotonic, from performance.now()
  width:   px       // canvas size in CSS pixels
  height:  px
  scale:   0.5..2   // the size the user picked from the bar; 1 is the skin's own fit, larger may crop
  options: { background: "nebula", ... }   // the current choice per option
}
```

`init(canvas, ctx, env)`, optional, runs once before the first frame and may be async. `env.options` is the current
choices and `env.url(path)` the URL of a file in the skin's own folder, for a fetch or an image. A module import
needs no token: `await import("/skin/vendor/three.module.js")` works, and so do the relative imports inside that
module. Files in the folder are served to the skin and nothing outside it is.

Rules: no network, no DOM outside the canvas. A skin that fails to load, throws in `init` or `draw`, or asks for
WebGL where there is none is replaced by the built-in `pulse` for the rest of the session and the orb says why.

Where skins live: `~/.config/orb/skins/<name>/` (yours), then this folder (built-ins). Select one with
`"skin": "<name>"` in any config layer (`README.md`, Config). A name that is not found, or a folder that fails
validation, falls back to `pulse` with the reason shown in the orb.

## Minimal 2D example

```js
// ~/.config/orb/skins/dot/skin.js   (with skin.json: { "name": "dot", "version": "1.0.0" })
window.orbSkin = {
  draw(ctx, { state, level, width, height }) {
    ctx.clearRect(0, 0, width, height);
    const r = Math.min(width, height) * (0.15 + level * 0.1);
    ctx.fillStyle = state === "speaking" ? "#ff7ab6" : state === "listening" ? "#7ee787" : "#5ad1ff";
    ctx.beginPath();
    ctx.arc(width / 2, height / 2, r, 0, Math.PI * 2);
    ctx.fill();
  },
};
```

## Minimal WebGL example with an option

```json
{ "name": "tint", "version": "1.0.0", "canvas": "webgl",
  "options": { "tone": { "label": "Tone", "choices": ["cool", "warm"], "key": "KeyB" } } }
```

```js
window.orbSkin = {
  draw(gl, { state, level, width, height, options }) {
    gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
    const warm = options.tone === "warm";
    const pulse = state === "speaking" ? level : state === "idle" ? 0.1 : 0.4;
    gl.clearColor(warm ? 0.3 + pulse * 0.5 : 0.05, 0.1 + pulse * 0.2, warm ? 0.05 : 0.3 + pulse * 0.5, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
  },
};
```
