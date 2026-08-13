import { diffProfile, renderProfileDiff, threeWayMerge } from "../components/profile-diff.js";
import { escapeHtml } from "../formatters.js";

const GROUPS = [
  ["经济与生产", ["worker_target", "bootstrap_worker_target", "economy_priority", "spawn_aggression", "ranger_ratio"]],
  ["Beacon 与侦察", ["beacon_priority", "near_beacon_radius", "runner_stall_ticks", "scout_ring_step"]],
  ["防御", ["defense_priority", "defender_vanguard_target", "defender_ranger_target", "defense_watch_radius", "worker_evacuation_radius", "carrier_safety_margin"]],
  ["战斗与资源记忆", ["combat_priority", "resource_memory_ttl", "resource_stall_ticks"]],
];

const LABELS = {
  worker_target: "成熟 Worker 目标", bootstrap_worker_target: "开局 Worker 目标",
  economy_priority: "经济优先级", spawn_aggression: "扩军积极度", ranger_ratio: "Ranger / Vanguard 比例",
  beacon_priority: "Beacon 优先级", near_beacon_radius: "Beacon 近域半径", runner_stall_ticks: "Runner 停滞阈值",
  scout_ring_step: "侦察环步长", defense_priority: "防御优先级", defender_vanguard_target: "守备 Vanguard",
  defender_ranger_target: "守备 Ranger", defense_watch_radius: "防御警戒半径", worker_evacuation_radius: "Worker 疏散半径",
  carrier_safety_margin: "载体安全余量", combat_priority: "战斗优先级", resource_memory_ttl: "资源记忆 TTL",
  resource_stall_ticks: "资源路线停滞阈值",
};

export function renderStrategy(root, strategy, schema) {
  if (!strategy?.profile) {
    root.innerHTML = `<section class="panel"><div class="empty-state"><div><strong>策略尚未就绪</strong>等待本地策略仓库初始化。</div></div></section>`;
    return;
  }
  const status = strategy.status || "ACTIVE";
  root.innerHTML = `<section class="page-heading"><div><span class="eyebrow">VERSIONED POLICY</span><h1>策略控制台</h1><p>完整 Profile 通过版本号比较并交换；保存后只在下一个 Tick 边界激活。</p></div><div class="revision-badge">REV ${escapeHtml(strategy.revision)} · ${escapeHtml(status)}</div></section>
  <div id="strategy-notice" class="inline-notice" hidden></div>
  <form id="strategy-form" class="strategy-layout">
    <div class="strategy-groups">${GROUPS.map(([name, fields]) => `<section class="panel form-section"><header class="panel-header"><h2 class="panel-title">${name}</h2></header><div class="field-grid">${fields.map((field) => fieldControl(field, strategy.profile[field], schema?.fields?.[field])).join("")}</div></section>`).join("")}</div>
    <aside class="panel sticky-review"><header class="panel-header"><h2 class="panel-title">版本差异</h2></header><div class="review-body"><div id="profile-diff">${renderProfileDiff([])}</div><label class="field-control"><span>变更原因</span><textarea id="strategy-reason" required maxlength="500">本地控制台调整</textarea></label><button class="button button-primary save-profile" type="submit">保存为待激活版本</button><p class="muted">不会中途修改正在生成或已经提交的计划。</p></div></aside>
  </form><section id="strategy-history" class="panel strategy-history"><div class="empty-state"><div><strong>正在读取版本历史</strong></div></div></section>`;
}

function fieldControl(field, value, bounds = {}) {
  const step = bounds.kind === "integer" ? "1" : "0.05";
  return `<label class="field-control"><span>${LABELS[field] || field}</span><input name="${field}" aria-label="${LABELS[field] || field}" type="number" value="${escapeHtml(value)}" min="${escapeHtml(bounds.minimum ?? "")}" max="${escapeHtml(bounds.maximum ?? "")}" step="${step}" required><small>${escapeHtml(bounds.minimum ?? "—")} – ${escapeHtml(bounds.maximum ?? "—")}</small></label>`;
}

export function installStrategy(root, strategy, api, store) {
  const form = root.querySelector("#strategy-form");
  if (!form) return;
  let baseProfile = structuredClone(strategy.profile);
  const values = () => Object.fromEntries(Object.entries(baseProfile).map(([field, original]) => {
    if (field === "schema_version") return [field, original];
    const input = form.elements.namedItem(field);
    return [field, Number(input?.value)];
  }));
  const showDiff = () => { root.querySelector("#profile-diff").innerHTML = renderProfileDiff(diffProfile(baseProfile, values())); };
  form.addEventListener("input", showDiff);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const draft = values(); const button = form.querySelector("button[type=submit]"); button.disabled = true;
    try {
      const saved = await api.saveStrategy({ expectedRevision: strategy.revision, profile: draft, reason: root.querySelector("#strategy-reason").value });
      store.snapshot = { ...store.snapshot, strategy: saved };
      notice(root, "等待下一个 Tick 边界激活", "good");
    } catch (error) {
      if (error.status === 409 && error.details?.current?.profile) {
        const merged = threeWayMerge(baseProfile, draft, error.details.current.profile);
        baseProfile = structuredClone(error.details.current.profile);
        for (const [field, value] of Object.entries(merged.profile)) if (form.elements.namedItem(field)) form.elements.namedItem(field).value = value;
        strategy.revision = error.details.current.revision;
        notice(root, `检测到版本冲突；已保留草稿并合并服务器变更${merged.conflicts.length ? `，冲突字段：${merged.conflicts.join(", ")}` : ""}`, "warning");
        showDiff();
      } else notice(root, `保存失败：${error.message}`, "danger");
    } finally { button.disabled = false; }
  });
  api.strategyHistory().then((history) => {
    const host = root.querySelector("#strategy-history");
    host.innerHTML = `<header class="panel-header"><h2 class="panel-title">版本历史</h2></header><div class="revision-list">${(history.items || []).map((item) => `<div><span class="mono">REV ${item.revision}</span><span>${item.source} · ${item.status}</span>${item.revision !== strategy.revision ? `<button class="button" data-rollback="${item.revision}" aria-label="回滚到 REV ${item.revision}">回滚</button>` : ""}</div>`).join("")}</div>`;
    host.querySelectorAll("[data-rollback]").forEach((button) => button.addEventListener("click", async () => {
      button.disabled = true;
      try { await api.rollbackStrategy({ expectedRevision:strategy.revision, targetRevision:Number(button.dataset.rollback), reason:"dashboard rollback" }); notice(root, "回滚版本等待下一个 Tick 边界激活", "good"); }
      catch (error) { notice(root, `回滚失败：${error.message}`, "danger"); button.disabled = false; }
    }));
  }).catch(() => {});
}

function notice(root, message, tone) {
  const element = root.querySelector("#strategy-notice"); element.hidden = false; element.className = `inline-notice ${tone}`; element.textContent = message;
}
