const FOLDER_SECTIONS = [
  {
    kind: "panel", name: "Panel folders", noun: "panels", where: "the panels menu",
    ask: "In the orb's Settings page, explain the panel folders section to me aloud in a few sentences: what an added folder is, how it differs from the default folders, and which folders I have added right now. Plain language, no file paths read out.",
    purpose: "Saved panels in these folders show in the panels menu.",
  },
  {
    kind: "skin", name: "Skin folders", noun: "skins", where: "the skin picker",
    ask: "In the orb's Settings page, explain the skin folders section to me aloud in a few sentences: what an added folder is, how it differs from the default folders, and which folders I have added right now. Plain language, no file paths read out.",
    purpose: "Skins in these folders appear in the skin picker. A skin here replaces a built-in one of the same name.",
  },
  {
    kind: "theme", name: "Theme folders", noun: "themes", where: "the theme picker",
    ask: "In the orb's Settings page, explain the theme folders section to me aloud in a few sentences: what an added folder is, how it differs from the default folders, and which folders I have added right now. Plain language, no file paths read out.",
    purpose: "Themes in these folders appear in the theme picker. A theme here replaces a built-in one of the same name.",
  },
  {
    kind: "widget", name: "Widget folders", noun: "widgets", where: "the widgets list",
    ask: "In the orb's Settings page, explain the widget folders section to me aloud in a few sentences: what an added folder is, how it differs from the default folders, and which folders I have added right now. Plain language, no file paths read out.",
    purpose: "Widgets in these folders appear in the bar's widgets list.",
  },
];

const DEFAULT_LABELS = { mine: "Just this repo", user: "Every repo", "built-in": "Built in" };

function defaultLabel(scope) {
  return DEFAULT_LABELS[scope] || scope;
}

function folderSummary(state) {
  const dirs = (state && state.dirs) || [];
  const missing = dirs.filter((d) => !d.exists).length;
  const extraText = dirs.length ? `${dirs.length} added ${dirs.length === 1 ? "folder" : "folders"}` : "no added folders";
  const missingText = missing ? `${missing} not found` : "";
  return { extraText, missingText, problem: missing > 0 };
}

function pathPieces(path) {
  return String(path).split(/(?<=[\\/])/).filter((piece) => piece !== "");
}

function extraRow(dir) {
  const chips = [];
  const notes = [];
  if (!dir.exists) {
    chips.push({ text: "Folder not found", level: "warn", title: "Nothing loads from this folder until it exists again." });
    notes.push("This folder isn't on disk right now, perhaps a drive that isn't connected. Nothing loads from it until it's back.");
  }
  if (!dir.editable) {
    const file = dir.source || "another config file";
    chips.push({ text: `Set in ${file}`, level: "dim", title: "Remove it in the file that sets it." });
    notes.push(`To stop searching it, remove it from ${file}.`);
  }
  return { path: dir.path, resolved: dir.resolved, chips, notes, removable: dir.editable, openable: dir.exists };
}

function defaultsSummary(defaults) {
  const labels = (defaults || []).map((d) => defaultLabel(d.scope));
  if (!labels.length) return "";
  return `Also searched: ${labels.length > 1 ? `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}` : labels[0]} (see Default folders above).`;
}

function initiallyOpen(stored, states) {
  const open = new Set(Array.isArray(stored) ? stored.filter((k) => typeof k === "string") : []);
  for (const section of FOLDER_SECTIONS) if (folderSummary(states && states[section.kind]).problem) open.add(section.kind);
  return open;
}

function typedFolder(text) {
  const folder = String(text).trim();
  return folder ? { ok: true, folder } : { ok: false, error: "Give a folder to add." };
}

if (typeof module !== "undefined") module.exports = { FOLDER_SECTIONS, defaultLabel, folderSummary, pathPieces, extraRow, defaultsSummary, initiallyOpen, typedFolder };
