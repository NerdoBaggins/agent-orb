// The settings panel's folder sections: nothing here touches the page, so node can test it.

// One section per kind of thing a user adds, in the order they are shown: by name.
const FOLDER_SECTIONS = [
  {
    kind: "panel", name: "Panel folders", noun: "panels", where: "the panels menu",
    purpose: "Saved panels in these folders show in the panels menu. They are searched after the folders for this repo and before the ones for all repos.",
  },
  {
    kind: "skin", name: "Skin folders", noun: "skins", where: "the skin picker",
    purpose: "Skins in these folders appear in the skin picker. They are searched before the bundled skins, so a skin here replaces a bundled one of the same name.",
  },
  {
    kind: "theme", name: "Theme folders", noun: "themes", where: "the theme picker",
    purpose: "Themes in these folders appear in the theme picker. They are searched before the bundled themes, so a theme here replaces a bundled one of the same name.",
  },
  {
    kind: "widget", name: "Widget folders", noun: "widgets", where: "the widgets list",
    purpose: "Widgets in these folders appear in the bar's widgets list. They are searched after the folders for this repo and before the ones for all repos.",
  },
];

const DEFAULT_LABELS = { mine: "This repo", user: "All repos", "built-in": "Bundled with the orb" };

function defaultLabel(scope) {
  return DEFAULT_LABELS[scope] || scope;
}

// What a collapsed header says at a glance, and whether the section has something wrong in it.
function folderSummary(state) {
  const dirs = (state && state.dirs) || [];
  const missing = dirs.filter((d) => !d.exists).length;
  const extraText = dirs.length ? `${dirs.length} extra` : "defaults only";
  const missingText = missing ? `${missing} missing` : "";
  return { extra: dirs.length, missing, extraText, missingText, text: [extraText, missingText].filter(Boolean).join(" · "), problem: missing > 0 };
}

// A path in pieces that end at a separator, so the page can let it wrap between folder names and never inside one.
function pathPieces(path) {
  return String(path).split(/(?<=[\\/])/).filter((piece) => piece !== "");
}

// One of the user's extra folders as the list shows it.
function extraRow(dir) {
  const chips = [];
  if (!dir.exists) chips.push({ text: "missing", level: "warn", title: "Nothing is found here until this folder exists." });
  if (!dir.editable) chips.push({ text: "set in another config file", level: "dim", title: "Remove it in the file that sets it." });
  return { path: dir.path, resolved: dir.resolved, chips, removable: dir.editable, openable: dir.exists };
}

// One of the folders always searched.
function defaultRow(dir) {
  const chips = dir.exists ? [] : [{ text: "not created yet", level: "dim", title: "Created when you save the first one here." }];
  return { label: defaultLabel(dir.scope), path: dir.path, chips, openable: dir.exists };
}

// Which sections start open: the ones the user left open, and any with a problem in it.
function initiallyOpen(stored, states) {
  const open = new Set(Array.isArray(stored) ? stored.filter((k) => typeof k === "string") : []);
  for (const section of FOLDER_SECTIONS) if (folderSummary(states && states[section.kind]).problem) open.add(section.kind);
  return open;
}

// A folder typed into the editor, trimmed; the server decides whether it is a good one.
function typedFolder(text) {
  const folder = String(text).trim();
  return folder ? { ok: true, folder } : { ok: false, error: "Give a folder to add." };
}

if (typeof module !== "undefined") module.exports = { FOLDER_SECTIONS, defaultLabel, folderSummary, pathPieces, extraRow, defaultRow, initiallyOpen, typedFolder };
