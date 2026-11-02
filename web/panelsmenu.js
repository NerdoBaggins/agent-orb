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

// What the folder editor shows beside a folder, and whether it can be removed here.
function panelDirRow(dir) {
  const notes = [];
  if (!dir.exists) notes.push("missing");
  if (!dir.editable) notes.push("set in another config file");
  return { path: dir.path, resolved: dir.resolved, note: notes.join(", "), removable: dir.editable };
}

// A folder typed into the editor, trimmed; the server decides whether it is a good one.
function typedFolder(text) {
  const folder = String(text).trim();
  return folder ? { ok: true, folder } : { ok: false, error: "give a folder" };
}

if (typeof module !== "undefined") module.exports = { scopeLabel, panelRow, panelDirRow, typedFolder };
