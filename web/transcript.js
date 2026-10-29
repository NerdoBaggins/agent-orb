// The transcript panel's pure logic: nothing here touches the page, so node can test it.

// The server sends only the feed items that changed, and the ids of all of them in order.
function mergeFeed(previous, changed, order) {
  const byId = new Map(previous.map((item) => [item.id, item]));
  for (const item of changed) byId.set(item.id, item);
  return order.map((id) => byId.get(id)).filter(Boolean);
}

// "42s", "3m 05s", "1h 02m": how long a turn has been running. Nothing before its start is known, so nothing shows.
function elapsed(since, now) {
  const start = Date.parse(since);
  if (Number.isNaN(start)) return "";
  const s = Math.max(0, Math.floor((now - start) / 1000));
  const pad = (n) => String(n).padStart(2, "0");
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${pad(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${pad(Math.floor((s % 3600) / 60))}m`;
}

// When a feed item happened, from its `ts` (the session log's own timestamp), in local time. An item with no readable
// `ts` shows no time rather than a guess.
const { entryTime, dateLines } = (() => {
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const pad2 = (n) => String(n).padStart(2, "0");
  const happened = (ts) => {
    const at = Date.parse(ts);
    return Number.isNaN(at) ? null : new Date(at);
  };
  // "Fri 23 Oct 2026"
  const day = (ts) => {
    const at = happened(ts);
    return at ? `${DAYS[at.getDay()]} ${at.getDate()} ${MONTHS[at.getMonth()]} ${at.getFullYear()}` : "";
  };
  return {
    // "14:05:09"
    entryTime(ts) {
      const at = happened(ts);
      return at ? [at.getHours(), at.getMinutes(), at.getSeconds()].map(pad2).join(":") : "";
    },
    // The date line each item opens with, or null: one before the first timed item and one wherever the day differs
    // from the last timed item's. An untimed item opens none and leaves the day as it was.
    dateLines(items) {
      let last = "";
      return items.map((item) => {
        const label = day(item.ts);
        if (!label || label === last) return null;
        last = label;
        return label;
      });
    },
  };
})();

// Replies are markdown. Everything is escaped first and only the tags built here are ever emitted, so a reply can
// never inject markup; a link is made only for http and https addresses.
function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function inlineMarkdown(text) {
  const parts = escapeHtml(text).split("`");
  return parts.map((part, i) => {
    const code = i % 2 === 1;
    if (code && i < parts.length - 1) return `<code>${part}</code>`;  // a backtick with no closing one is just a backtick
    const formatted = part
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\s][^*]*)\*(?!\w)/g, "$1<em>$2</em>");
    return code ? "`" + formatted : formatted;
  }).join("");
}

function markdown(text) {
  const html = [];
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [];
  let list = null;
  const flushParagraph = () => { if (paragraph.length) html.push(`<p>${paragraph.map(inlineMarkdown).join("<br>")}</p>`); paragraph = []; };
  const flushList = () => { if (list) html.push(`<${list.tag}>${list.items.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</${list.tag}>`); list = null; };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^```/.test(line)) {
      flushParagraph(); flushList();
      const code = [];
      for (i += 1; i < lines.length && !/^```/.test(lines[i]); i++) code.push(lines[i]);
      html.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }
    const item = /^\s*(?:([-*+])|(\d+)[.)])\s+(.*)$/.exec(line);
    if (item) {
      flushParagraph();
      const tag = item[1] ? "ul" : "ol";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(item[3]);
      continue;
    }
    flushList();
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    if (heading) { flushParagraph(); html.push(`<p><strong>${inlineMarkdown(heading[1])}</strong></p>`); continue; }
    if (!line.trim()) { flushParagraph(); continue; }
    paragraph.push(line);
  }
  flushParagraph(); flushList();
  return html.join("");
}

// Whether the reader is at the bottom, within `slack` pixels, so a new row scrolls into view only for one who is.
function nearBottom(scrollTop, clientHeight, scrollHeight, slack = 40) {
  return scrollHeight - scrollTop - clientHeight <= slack;
}

if (typeof module !== "undefined") module.exports = { mergeFeed, elapsed, entryTime, dateLines, markdown, nearBottom };
