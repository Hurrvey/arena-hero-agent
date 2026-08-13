import { escapeHtml } from "../formatters.js";

export function renderAdaptive(root, status, reports) {
  const items = reports?.items || [];
  root.innerHTML = `<section class="page-heading"><div><span class="eyebrow">SKILL-GROUNDED REVIEW</span><h1>自适应评分与候选</h1><p>Evaluator 与 Designer 每次都绑定项目内 Skill 指纹；候选默认要求人工审核。</p></div><div class="revision-badge">${escapeHtml(status?.status || "DISABLED")}</div></section>
  <section class="metrics-grid adaptive-summary"><article class="metric-card"><div><div class="metric-label">自适应</div><div class="metric-value">${status?.enabled ? "已启用" : "未启用"}</div></div></article><article class="metric-card"><div><div class="metric-label">自动应用</div><div class="metric-value">${status?.autoApply ? "ON" : "OFF"}</div></div></article><article class="metric-card"><div><div class="metric-label">当前 Skill 指纹</div><div class="fingerprint">${escapeHtml(status?.skillFingerprint || "等待首个窗口")}</div></div></article></section>
  <section class="candidate-grid">${items.length ? items.map(candidateCard).join("") : `<article class="panel"><div class="empty-state"><div><strong>尚无评分窗口</strong>达到配置的 Tick 窗口后，评估报告会保存到 SQLite。</div></div></article>`}</section>`;
}

function candidateCard(item) {
  const applyDisabled = !["READY", "REVIEW_REQUIRED"].includes(item.status) || item.disabledReason;
  const rejectDisabled = !item.candidateId || !["READY", "REVIEW_REQUIRED", "STALE"].includes(item.status);
  const reason = item.disabledReason || (applyDisabled ? `候选状态 ${item.status} 不允许应用` : "通过样本、版本和 Skill 指纹校验");
  return `<article class="panel candidate-card" data-candidate="${escapeHtml(item.candidateId || "")}"><header class="panel-header"><h2 class="panel-title">窗口 ${escapeHtml(item.startTick)} → ${escapeHtml(item.endTick)}</h2><span class="candidate-status">${escapeHtml(item.status)}</span></header><div class="candidate-body"><div class="score-grid"><div><span>样本</span><strong>${escapeHtml(item.sampleCount)} 个样本</strong></div><div><span>总分</span><strong>${escapeHtml(item.rawScore)}</strong></div><div><span>归一化</span><strong>${Number(item.scorePerTick || 0).toFixed(2)} / Tick</strong></div></div><div class="fingerprint-row"><span>Skill</span><code>${escapeHtml(item.skillFingerprint)}</code></div><ul class="diff-list">${(item.changes || []).map((change) => `<li><code>${escapeHtml(change.field)}</code><span>${escapeHtml(change.before)}</span><span>→</span><strong>${escapeHtml(change.after)}</strong></li>`).join("") || "<li>该窗口没有候选 Profile 差异。</li>"}</ul><div class="candidate-actions"><button class="button button-primary" data-candidate-action="APPLY" ${applyDisabled ? "disabled" : ""}>应用候选</button><button class="button" data-candidate-action="REJECT" ${rejectDisabled ? "disabled" : ""}>拒绝</button></div><p class="validation-reason">${escapeHtml(reason)}</p></div></article>`;
}

export function installAdaptive(root, api, strategy) {
  root.querySelectorAll("[data-candidate-action]").forEach((button) => button.addEventListener("click", async () => {
    const card = button.closest("[data-candidate]"); button.disabled = true;
    try {
      const result = await api.decideCandidate(card.dataset.candidate, { action:button.dataset.candidateAction, expectedRevision:strategy?.revision || 1 });
      card.querySelector(".validation-reason").textContent = result.status === "PENDING_ACTIVATION" ? "候选已进入待激活版本" : "候选已拒绝";
      card.querySelector(".candidate-status").textContent = result.status;
      card.querySelectorAll("button").forEach((item) => { item.disabled = true; });
    } catch (error) { card.querySelector(".validation-reason").textContent = `操作被服务器拒绝：${error.details?.reason || error.message}`; button.disabled = false; }
  }));
}
