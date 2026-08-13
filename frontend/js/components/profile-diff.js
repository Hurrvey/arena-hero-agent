export function diffProfile(before = {}, after = {}) {
  return [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .sort()
    .filter((field) => before[field] !== after[field])
    .map((field) => ({ field, before: before[field], after: after[field] }));
}

export function threeWayMerge(base = {}, draft = {}, server = {}) {
  const profile = {};
  const conflicts = [];
  for (const field of [...new Set([...Object.keys(base), ...Object.keys(draft), ...Object.keys(server)])].sort()) {
    const userChanged = draft[field] !== base[field];
    const serverChanged = server[field] !== base[field];
    profile[field] = userChanged ? draft[field] : server[field];
    if (userChanged && serverChanged && draft[field] !== server[field]) conflicts.push(field);
  }
  return { profile, conflicts };
}

export function renderProfileDiff(changes) {
  if (!changes.length) return `<p class="muted">当前草稿与基准版本一致。</p>`;
  return `<ul class="diff-list">${changes.map(({ field, before, after }) => `<li><code>${field}</code><span>${before}</span><span aria-hidden="true">→</span><strong>${after}</strong></li>`).join("")}</ul>`;
}
