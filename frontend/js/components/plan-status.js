import { escapeHtml } from "../formatters.js";

export function renderPlan(plan) {
  if (!plan) return `<div class="empty-state"><div><strong>尚无当前计划</strong>启动 Agent 后，每个 Tick 的动作和原因会显示在这里。</div></div>`;
  const status = plan.status || "DRAFT";
  const explanations = plan.explanation?.actions || plan.explanation || [];
  const effective = plan.plan || {};
  const unitActions = effective.unitActions || effective.unit_actions || {};
  const manualPlan = plan.receipts?.MANUAL?.plan || {};
  const agentPlan = plan.receipts?.AGENT?.plan || {};
  const manualActions = manualPlan.unitActions || manualPlan.unit_actions || {};
  const actions = Object.entries(unitActions).map(([entityId, action]) => {
    const source = Object.hasOwn(manualActions, entityId) ? "MANUAL" : "AGENT";
    const actionType = action?.type || "WAIT";
    const explanation = source === "AGENT"
      ? explanations.find((item) => item.entityId === entityId && item.actionType === actionType)
      : null;
    return {
      entityId,
      actionType,
      source,
      reasonCode: explanation?.reasonCode || (source === "MANUAL" ? "MANUAL_OVERRIDE" : "RECEIVED_PLAN"),
      riskBefore: explanation?.riskBefore,
      riskAfter: explanation?.riskAfter,
    };
  });
  const coreAction = effective.coreAction || effective.core_action;
  if (coreAction) {
    const manualCore = manualPlan.coreAction || manualPlan.core_action;
    const source = manualCore ? "MANUAL" : "AGENT";
    const actionType = coreAction.type || "WAIT";
    const explanation = source === "AGENT"
      ? explanations.find((item) => (item.entityId === "Core" || !item.entityId) && item.actionType === actionType)
      : null;
    actions.unshift({ entityId:"Core", actionType, source, reasonCode:explanation?.reasonCode || (source === "MANUAL" ? "MANUAL_OVERRIDE" : "RECEIVED_PLAN"), riskBefore:explanation?.riskBefore, riskAfter:explanation?.riskAfter });
  }
  if (!actions.length) actions.push(...explanations.map((item) => ({ ...item, source:"AGENT" })));
  return `<div class="plan-body">
    <div class="plan-progress"><span class="plan-stage ${status === "DRAFT" ? "current" : ""}">DRAFT</span><span class="plan-arrow">→</span><span class="plan-stage ${status === "ACCEPTED" ? "current" : ""}">ACCEPTED</span><span class="plan-arrow">→</span><span class="plan-stage ${status.includes("RESOLVED") ? "current" : ""}">RESOLVED</span></div>
    <div class="threat-callout"><span aria-hidden="true">△</span><span>${actions.length ? "策略已根据当前可见事实生成完整计划。" : "当前 Tick 无需排队动作；保持观察。"}</span></div>
    <ul class="action-list">${actions.slice(0, 8).map((action) => `<li class="action-row"><strong class="accent mono">${escapeHtml(action.entityId || "Core")}</strong><span>${escapeHtml(action.actionType || "WAIT")}</span><span class="muted">${escapeHtml(action.source || "AGENT")} · ${escapeHtml(action.reasonCode || "DETERMINISTIC_FALLBACK")}</span><span>${escapeHtml(action.riskBefore ?? "—")} → ${escapeHtml(action.riskAfter ?? "—")}</span></li>`).join("")}</ul>
  </div>`;
}
