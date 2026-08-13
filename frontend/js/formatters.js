export const formatNumber = (value, fallback = "—") =>
  Number.isFinite(Number(value)) ? new Intl.NumberFormat("zh-CN").format(Number(value)) : fallback;

export const escapeHtml = (value) => String(value ?? "").replace(
  /[&<>'"]/g,
  (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character],
);

export const formatPosition = (position) =>
  Array.isArray(position) && position.length === 2
    ? `(${escapeHtml(position[0])}, ${escapeHtml(position[1])})`
    : "未知";

export const typeLabel = (value = "") =>
  ({ WORKER: "Worker", VANGUARD: "Vanguard", RANGER: "Ranger", CORE: "Core" })[String(value).toUpperCase()] || escapeHtml(value);

export const timeLabel = (value) => {
  const date = value ? new Date(value) : new Date();
  return Number.isNaN(date.valueOf()) ? "--:--:--" : date.toLocaleTimeString("zh-CN", { hour12: false });
};
