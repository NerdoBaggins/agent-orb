// A zone away from UTC, whose day boundary falls inside a UTC day, so a time or date read in UTC is told from local.
process.env.TZ = "America/Los_Angeles";
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { mergeFeed, elapsed, entryTime, dateLines, markdown, nearBottom } = createRequire(import.meta.url)("./transcript.js");

const item = (id, text = id) => ({ id, kind: "reply", text });

test("a delta keeps the unchanged items, replaces the changed ones and adds the new ones, in the server's order", () => {
  const previous = [item("1"), item("2", "old"), item("3")];
  const feed = mergeFeed(previous, [item("2", "new"), item("4")], ["1", "2", "3", "4"]);
  assert.deepEqual(feed.map((i) => i.text), ["1", "new", "3", "4"]);
});

test("an item the order no longer lists is dropped, so the feed stays at the server's length", () => {
  assert.deepEqual(mergeFeed([item("1"), item("2")], [], ["2"]).map((i) => i.id), ["2"]);
});

test("an id in the order that nobody has is skipped rather than shown empty", () => {
  assert.deepEqual(mergeFeed([item("1")], [], ["1", "9"]).map((i) => i.id), ["1"]);
});

const at = (s) => new Date(Date.UTC(2026, 9, 23, 12, 0, 0) + s * 1000).toISOString();
const now = Date.UTC(2026, 9, 23, 12, 0, 0);

test("a turn's elapsed time reads in seconds, then minutes and seconds, then hours and minutes", () => {
  assert.equal(elapsed(at(-42), now), "42s");
  assert.equal(elapsed(at(-185), now), "3m 05s");
  assert.equal(elapsed(at(-3720), now), "1h 02m");
});

test("a start that is missing, unreadable or still ahead of the clock shows no negative time", () => {
  assert.equal(elapsed(null, now), "");
  assert.equal(elapsed("not a time", now), "");
  assert.equal(elapsed(at(5), now), "0s");
});

test("an item's time is its local hour, minute and second, each two digits", () => {
  assert.equal(entryTime("2026-10-23T16:05:09Z"), "09:05:09");
  assert.equal(entryTime("2026-10-24T06:59:01.920Z"), "23:59:01");
});

test("an item whose time is missing or unreadable shows no time", () => {
  assert.equal(entryTime(undefined), "");
  assert.equal(entryTime(null), "");
  assert.equal(entryTime("t"), "");
});

const stamped = (...stamps) => stamps.map((ts, i) => ({ id: String(i), kind: "reply", text: "", ...(ts ? { ts } : {}) }));

test("the first timed item opens with its date and the rest of that local day opens none", () => {
  // 23:30 and 00:30 UTC are two UTC days but both on Friday the 23rd in Los Angeles.
  assert.deepEqual(dateLines(stamped("2026-10-23T23:30:00Z", "2026-10-24T00:30:00Z")), ["Fri 23 Oct 2026", null]);
});

test("an item on a later local day than the one before it opens with its date", () => {
  assert.deepEqual(dateLines(stamped("2026-10-24T06:59:00Z", "2026-10-24T07:01:00Z", "2026-10-24T07:02:00Z")),
    ["Fri 23 Oct 2026", "Sat 24 Oct 2026", null]);
});

test("an untimed item opens no date and the next timed item is compared with the last timed one", () => {
  assert.deepEqual(dateLines(stamped(undefined, "2026-10-23T20:00:00Z", undefined, "2026-10-23T21:00:00Z", undefined, "2026-10-25T20:00:00Z")),
    [null, "Fri 23 Oct 2026", null, null, null, "Sun 25 Oct 2026"]);
});

test("markup in a reply is shown as text, never emitted", () => {
  const html = markdown('<script>alert(1)</script> & <img src=x onerror="y">');
  assert.ok(!/<script|<img/.test(html));
  assert.ok(html.includes("&lt;script&gt;") && html.includes("&amp;"));
});

test("a fenced block is code, left as written and escaped, with no formatting applied inside it", () => {
  assert.equal(markdown("```js\nconst a = **1** < 2;\n```"), "<pre><code>const a = **1** &lt; 2;</code></pre>");
});

test("inline code keeps its asterisks and bold and italics wrap their words", () => {
  assert.equal(markdown("run `a*b*c` then **now** and *later*"), "<p>run <code>a*b*c</code> then <strong>now</strong> and <em>later</em></p>");
});

test("only http and https links become links", () => {
  assert.ok(markdown("[docs](https://example.com/a?b=1)").includes('<a href="https://example.com/a?b=1" target="_blank" rel="noopener noreferrer">docs</a>'));
  assert.ok(!markdown("[x](javascript:alert(1))").includes("<a "));
});

test("bullets and numbers become lists and a heading becomes a bold line", () => {
  assert.equal(markdown("- one\n- two"), "<ul><li>one</li><li>two</li></ul>");
  assert.equal(markdown("1. a\n2) b"), "<ol><li>a</li><li>b</li></ol>");
  assert.equal(markdown("## Title\ntext"), "<p><strong>Title</strong></p><p>text</p>");
});

test("a blank line starts a paragraph and a single newline is a line break", () => {
  assert.equal(markdown("a\nb\n\nc"), "<p>a<br>b</p><p>c</p>");
});

test("the reader follows new rows only while at the bottom, within the slack", () => {
  assert.equal(nearBottom(600, 400, 1000), true);
  assert.equal(nearBottom(570, 400, 1000), true);
  assert.equal(nearBottom(500, 400, 1000), false);
  assert.equal(nearBottom(0, 400, 300), true);
});

test("a backtick that never closes is shown as a backtick and the rest is still formatted", () => {
  assert.equal(markdown("it's `half and **bold**"), "<p>it&#39;s `half and <strong>bold</strong></p>");
});
