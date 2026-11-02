// The panels dropdown's pure logic: nothing here touches the page, so node can test it.

const SCOPE_LABELS = { "built-in": "built-in", mine: "yours, this repo", custom: "custom folder", user: "global" };

function scopeLabel(scope) {
  return SCOPE_LABELS[scope] || scope;
}

// One dropdown row from the server's entry. `open` is the set of panel names showing now.
function panelRow(entry, open) {
  const where = entry.path || "built into the orb";
  const note = entry.shadowed ? `\nNot used: an earlier folder has a panel with this name, so showing "${entry.name}" opens that one.` : "";
  return {
    name: entry.name,
    title: entry.title,
    scope: scopeLabel(entry.scope),
    path: entry.path,
    shadowed: entry.shadowed,
    open: open.has(entry.name) && !entry.shadowed,
    tip: `${entry.description}\n${where}${note}`,
  };
}

if (typeof module !== "undefined") module.exports = { scopeLabel, panelRow };
