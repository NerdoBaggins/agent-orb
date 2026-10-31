// The release notes panel's pure logic: nothing here touches the page, so node can test it.

function releaseName(release) {
  return release.date ? `${release.version}, released ${release.date}` : `${release.version}, which is not released yet`;
}

function explainChange(release, section, entry) {
  return `Explain this agent-orb change to me in plain spoken language, as if I had not seen it. It is in release ${releaseName(release)}, under ${section.heading}: ${entry} Say what it was, why it matters to someone using the orb, and what I would notice, in a few sentences.`;
}

function explainRelease(release) {
  const changes = release.sections.flatMap((s) => s.entries.map((entry) => `${s.heading}: ${entry}`)).join(" ");
  return `Explain agent-orb release ${releaseName(release)} to me in plain spoken language. Its changes: ${changes} Give the gist in a few sentences, then say what I would notice most.`;
}

if (typeof module !== "undefined") module.exports = { explainChange, explainRelease };
