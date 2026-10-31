// Run with: node web/test_panel_api.mjs   (pytest runs it through orb/test_panel_api.py)
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const createOrbApi = createRequire(import.meta.url)("./panel.js");

function harness({ active = true } = {}) {
  const posted = [];
  let deliver, key;
  const api = createOrbApi({ post: (m) => posted.push(m), isActive: () => active, listen: (fn) => { deliver = fn; }, onKey: (fn) => { key = fn; } });
  return { api, posted, deliver: (m) => deliver(m), key: (ev) => key(ev) };
}

// rejected without user activation
{
  const h = harness({ active: false });
  await assert.rejects(h.api.send("hi"), /click or key press/);
  assert.equal(h.posted.length, 0);
}
// sent while busy: the orb holds it until the turn ends
{
  const h = harness();
  h.deliver({ orb: "state", state: "thinking" });
  h.api.send("hi");
  assert.deepEqual(h.posted, [{ orb: "send", id: h.posted[0].id, text: "hi", speak: true }]);
}
// a click sends, and the reply resolves the promise; speak:false travels with it
{
  const h = harness();
  const p = h.api.send("Explain this", { speak: false });
  assert.deepEqual(h.posted, [{ orb: "send", id: h.posted[0].id, text: "Explain this", speak: false }]);
  h.deliver({ orb: "reply", id: h.posted[0].id, text: "It is a chart." });
  assert.equal(await p, "It is a chart.");
  const p2 = h.api.send("Again");
  assert.equal(h.posted[1].speak, true);
  h.deliver({ orb: "reject", id: h.posted[1].id, error: "interrupted" });
  await assert.rejects(p2, /interrupted/);
}
// a reply to a send the page made before it reloaded does not settle the new page's send
{
  const before = harness();
  before.api.send("Explain this");
  const after = harness();
  let settled = false;
  after.api.send("Explain that").then(() => { settled = true; }, () => { settled = true; });
  after.deliver({ orb: "reply", id: before.posted[0].id, text: "About this." });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(settled, false);
}
// speak reads text aloud only from a click and only while idle; stop needs neither
{
  const idle = harness();
  await idle.api.speak("Good morning.");
  assert.deepEqual(idle.posted, [{ orb: "speak", text: "Good morning." }]);
  idle.api.stop();
  assert.deepEqual(idle.posted[1], { orb: "stop" });
  const passive = harness({ active: false });
  await assert.rejects(passive.api.speak("hi"), /click or key press/);
  passive.api.stop();
  assert.deepEqual(passive.posted, [{ orb: "stop" }]);
  const busy = harness();
  busy.deliver({ orb: "state", state: "speaking" });
  await assert.rejects(busy.api.speak("hi"), /busy/);
  assert.equal(busy.posted.length, 0);
}
// state follows the page
{
  const h = harness();
  const seen = [];
  h.api.on("state", (s) => seen.push(s));
  h.deliver({ orb: "state", state: "speaking" });
  assert.equal(h.api.state, "speaking");
  assert.deepEqual(seen, ["speaking"]);
}
// data from the feed reaches the page, and a listener that subscribes late gets the latest at once
{
  const h = harness();
  const seen = [];
  h.api.on("data", (d, at) => seen.push([d, at]));
  h.deliver({ orb: "data", data: { prs: 2 }, at: 100 });
  assert.deepEqual([h.api.data, h.api.dataAt], [{ prs: 2 }, 100]);
  const late = [];
  h.api.on("data", (d) => late.push(d));
  assert.deepEqual(seen, [[{ prs: 2 }, 100]]);
  assert.deepEqual(late, [{ prs: 2 }]);
}
// an action posts its name and args with no activation check of its own, and resolves with the result or rejects
{
  const h = harness({ active: false });
  const p = h.api.action("star", { id: 7 });
  assert.deepEqual(h.posted, [{ orb: "action", id: h.posted[0].id, name: "star", args: { id: 7 } }]);
  h.deliver({ orb: "result", id: h.posted[0].id, result: { starred: true } });
  assert.deepEqual(await p, { starred: true });
  const p2 = h.api.action("reauth");
  assert.equal(h.posted[1].args, null);
  h.deliver({ orb: "reject", id: h.posted[1].id, error: "reauth works only during a click or key press" });
  await assert.rejects(p2, /only during a click/);
}
console.log("panel api ok");
// Escape pressed inside the panel reaches the orb, unless the panel handled it or the user is typing
{
  const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
  const h = harness();
  h.key({ code: "Escape", target: { tagName: "DIV" }, defaultPrevented: false });
  await tick();
  assert.deepEqual(h.posted, [{ orb: "key", code: "Escape" }]);
  h.key({ code: "Escape", target: { tagName: "DIV" }, defaultPrevented: true });
  h.key({ code: "Escape", target: { tagName: "INPUT" }, defaultPrevented: false });
  h.key({ code: "KeyA", target: { tagName: "DIV" }, defaultPrevented: false });
  // the panel's own listener runs after this one and prevents the key during the same dispatch
  const used = { code: "Escape", target: { tagName: "DIV" }, defaultPrevented: false };
  h.key(used);
  used.defaultPrevented = true;
  await tick();
  assert.equal(h.posted.length, 1);
}
// Enter pressed inside the panel opens the orb's text box, unless it does something in the panel
{
  const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
  const h = harness();
  h.key({ key: "Enter", code: "Enter", target: { tagName: "DIV" }, defaultPrevented: false });
  await tick();
  assert.deepEqual(h.posted, [{ orb: "key", code: "Enter" }]);
  for (const tagName of ["A", "BUTTON", "SELECT", "SUMMARY", "INPUT", "TEXTAREA"]) h.key({ key: "Enter", code: "Enter", target: { tagName }, defaultPrevented: false });
  h.key({ key: "Enter", code: "Enter", target: { tagName: "DIV" }, defaultPrevented: true });
  h.key({ key: "Enter", code: "Enter", shiftKey: true, target: { tagName: "DIV" }, defaultPrevented: false });
  await tick();
  assert.equal(h.posted.length, 1);
}
