function releaseName(release) {
  return release.date ? `${release.version}, released ${release.date}` : `${release.version}, which is not released yet`;
}

function explainChange(release, section, entry) {
  const where = section.heading ? `, under ${section.heading}` : "";
  return `Explain this agent-orb change to me in plain spoken language, as if I had not seen it. It is in release ${releaseName(release)}${where}: ${entry} Say what it was, why it matters to someone using the orb, and what I would notice, in a few sentences.`;
}

function explainRelease(release) {
  const changes = release.sections.flatMap((s) => s.entries.map((entry) => (s.heading ? `${s.heading}: ${entry}` : entry))).join(" ");
  return `Explain agent-orb release ${releaseName(release)} to me in plain spoken language. Its changes: ${changes} Give the gist in a few sentences, then say what I would notice most.`;
}

function releaseRows(releases) {
  return releases.flatMap((release) => [
    { kind: "release", text: release.date ? `${release.version} — ${release.date}` : release.version, request: explainRelease(release) },
    ...release.sections.flatMap((section) => [
      ...(section.heading ? [{ kind: "section", text: section.heading }] : []),
      ...section.entries.map((entry, i) => ({ kind: "entry", text: entry, request: explainChange(release, section, entry), stripe: i % 2 === 1 })),
    ]),
  ]);
}

const TEXT_SIZES = { min: 12, max: 28, step: 2, start: 16 };

function clampTextSize(size) {
  return Number.isFinite(size) && size > 0 ? Math.min(TEXT_SIZES.max, Math.max(TEXT_SIZES.min, size)) : TEXT_SIZES.start;
}

function nextTextSize(size, direction) {
  return clampTextSize(size + direction * TEXT_SIZES.step);
}

if (typeof module !== "undefined") module.exports = { explainChange, explainRelease, releaseRows, TEXT_SIZES, clampTextSize, nextTextSize };
