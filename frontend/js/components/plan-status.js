import { escapeHtml } from "../formatters.js";

export function renderPlan(plan) {
  if (!plan) return `<div class="empty-state"><div><strong>尚无当前计划</strong>启动 Agent 后，每个 Tick 的动作和原因会显示在这里。</div></div>`;
  const status = plan.status || "DRAFT";
  const actions = plan.explanation?.actions || plan.explanation || [];
  return `<div class="plan-body">
    <div class="plan-progress"><span class="plan-stage ${status === "DRAFT" ? "current" : ""}">DRAFT</span><span class="plan-arrow">→</span><span class="plan-stage ${status === "ACCEPTED" ? "current" : ""}">ACCEPTED</span><span class="plan-arrow">→</span><span class="plan-stage ${status.includes("RESOLVED") ? "current" : ""}">RESOLVED</span></div>
    <div class="threat-callout"><span aria-hidden="true">△</span><span>${actions.length ? "策略已根据当前可见事实生成完整计划。" : "当前 Tick 无需排队动作；保持观察。"}</span></div>
    <ul class="action-list">${actions.slice(0, 8).map((action) => `<li class="action-row"><strong class="accent mono">${escapeHtml(action.entityId || "Core")}</strong><span>${escapeHtml(action.actionType || "WAIT")}</span><span class="muted">${escapeHtml(action.reasonCode || "DETERMINISTIC_FALLBACK")}</span><span>${escapeHtml(action.riskBefore ?? 0)} → ${escapeHtml(action.riskAfter ?? 0)}</span></li>`).join("")}</ul>
  </div>`;
}
