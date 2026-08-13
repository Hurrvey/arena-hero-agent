export function metricCard({ label, value, unit = "", icon, color = "var(--friendly)" }) {
  return `<article class="metric-card" style="--metric-color:${color}">
    <div class="metric-icon" aria-hidden="true"><svg><use href="/assets/arena-hero/icons/ui-symbols.svg#${icon}"></use></svg></div>
    <div><div class="metric-label">${label}</div><div class="metric-value">${value} <span class="metric-unit">${unit}</span></div></div>
  </article>`;
}
