import { ApiClient } from "./api-client.js";
import { AppStore } from "./app-store.js";
import { LiveConnection } from "./live-connection.js";
import { currentRoute, installRouter } from "./router.js";
import { renderRuntimeHeader } from "./components/runtime-header.js";
import { renderEntityDetail } from "./components/entity-detail.js";
import { renderUnitTable } from "./components/unit-table.js";
import { TacticalMap } from "./map/tactical-map.js";
import { renderOverview } from "./views/overview.js";
import { installStrategy, renderStrategy } from "./views/strategy.js";
import { installAdaptive, renderAdaptive } from "./views/adaptive.js";
import { installHistory, renderHistory } from "./views/history.js";
import { renderSettings } from "./views/settings.js";
import { decorateStateWithPlan } from "./map/plan-routes.js";

const store = new AppStore(); const api = new ApiClient(); let tacticalMap = null; let liveConnection = null; let activeRoute = currentRoute(); let routeNonce = 0;
const main = document.querySelector("#route-view"); const header = document.querySelector("#runtime-strip"); const banner = document.querySelector("#connection-banner");

async function bootstrap() {
  const [runtime, metrics, strategy, adaptive, state, plan, events] = await Promise.all([
    api.status(), api.metrics(), api.strategy(), api.adaptive(), api.state().catch(() => null), api.plan().catch(() => null), api.eventsTail(),
  ]);
  store.snapshot = { ...store.snapshot, runtime, metrics, strategy, adaptive };
  store.replaceFromRest(state, plan, events.lastSeq, events.events || []);
  render(activeRoute);
  liveConnection = new LiveConnection({ store, api, onEvent: refreshAuthoritative });
  liveConnection.start();
}

async function refreshAuthoritative() {
  const [runtime, state, plan, metrics] = await Promise.all([api.status(), api.state().catch(() => null), api.plan().catch(() => null), api.metrics()]);
  store.snapshot = { ...store.snapshot, runtime, state, plan, metrics }; store.emit();
}

function render(route = activeRoute) {
  tacticalMap?.dispose();
  tacticalMap = null;
  activeRoute = route; document.querySelectorAll(".nav-link").forEach((link) => link.classList.toggle("active", link.getAttribute("href") === route));
  if (route === "/") renderOverview(main, overviewSnapshot());
  else renderSecondary(route);
  renderChrome();
  if (route === "/") installOverview();
}

function renderChrome() {
  header.innerHTML = renderRuntimeHeader(store.snapshot.runtime, store.snapshot.connection);
  banner.classList.toggle("visible", store.snapshot.connection.stale); banner.textContent = store.snapshot.connection.stale ? "实时连接不可用；当前界面显示最后一次权威快照，敌军位置可能已过期。" : "";
  document.querySelector("#map-stale")?.classList.toggle("visible", store.snapshot.connection.stale);
  updateControls();
}

async function renderSecondary(route) {
  const nonce = ++routeNonce;
  main.innerHTML = `<section class="panel"><div class="empty-state"><div><strong>正在读取本地数据</strong>从 SQLite 与安全配置接口加载。</div></div></section>`;
  try {
    if (route === "/strategy") {
      const [strategy, schema] = await Promise.all([api.strategy(), api.strategySchema()]);
      if (nonce !== routeNonce || activeRoute !== route) return;
      store.snapshot = { ...store.snapshot, strategy }; renderStrategy(main, strategy, schema); installStrategy(main, strategy, api, store);
    } else if (route === "/adaptive") {
      const [status, reports] = await Promise.all([api.adaptive(), api.adaptiveReports()]);
      if (nonce !== routeNonce || activeRoute !== route) return; renderAdaptive(main, status, reports); installAdaptive(main, api, store.snapshot.strategy);
    } else if (route === "/history") {
      const series = await api.metricSeries(); if (nonce !== routeNonce || activeRoute !== route) return; renderHistory(main, series); installHistory(main, api);
    } else if (route === "/settings") {
      const settings = await api.settings(); if (nonce !== routeNonce || activeRoute !== route) return; renderSettings(main, settings);
    }
  } catch (error) { if (nonce === routeNonce) main.innerHTML = `<section class="panel"><div class="empty-state"><div><strong>页面数据加载失败</strong>${error.message}</div></div></section>`; }
}

function installOverview() {
  const presented = overviewSnapshot();
  const canvas = document.querySelector("#tactical-map");
  tacticalMap = new TacticalMap(canvas, document.querySelector("#map-description"), (entity) => { const detail = document.querySelector("#entity-detail"); detail.innerHTML = renderEntityDetail(entity); detail.hidden = false; });
  tacticalMap.update(presented.state);
  document.querySelectorAll("[data-map]").forEach((button) => button.addEventListener("click", () => { const action = button.dataset.map; if (action === "home") { tacticalMap.hasAnchor = false; tacticalMap.update(presented.state); } else { tacticalMap.camera.zoomBy(action === "in" ? .2 : -.2); tacticalMap.render(); } }));
  document.querySelectorAll("[data-filter]").forEach((tab) => tab.addEventListener("click", () => { document.querySelectorAll("[data-filter]").forEach((item) => item.classList.toggle("active", item === tab)); document.querySelector("#unit-table").innerHTML = renderUnitTable(store.snapshot.state, tab.dataset.filter); }));
}

function overviewSnapshot() {
  return {
    ...store.snapshot,
    state: decorateStateWithPlan(store.snapshot.state, store.snapshot.plan),
  };
}

function updateControls() {
  const status = store.snapshot.runtime?.status || "STOPPED";
  document.querySelector("#start-agent").hidden = !["STOPPED", "ERROR"].includes(status);
  document.querySelector("#pause-agent").hidden = status !== "RUNNING";
  document.querySelector("#resume-agent").hidden = status !== "PAUSED";
  document.querySelector("#stop-agent").disabled = ["STOPPED", "STOPPING"].includes(status);
}

async function control(action, button) {
  const before = button.textContent; button.disabled = true; button.textContent = "处理中…";
  try { store.patch("runtime", await api.control(action)); } catch (error) { showToast(`${error.code || "操作失败"}：${error.message}`); }
  finally { button.disabled = false; button.textContent = before; render(activeRoute); }
}

function showToast(message) { const toast = document.querySelector("#toast"); toast.textContent = message; toast.hidden = false; setTimeout(() => { toast.hidden = true; }, 5000); }

document.querySelectorAll("[data-control]").forEach((button) => button.addEventListener("click", () => control(button.dataset.control, button)));
window.addEventListener("offline", () => {
  store.setConnection({ status: "RECONNECTING", stale: true });
  liveConnection?.socket?.close();
});
window.addEventListener("beforeunload", () => liveConnection?.stop());
store.subscribe(() => activeRoute === "/" ? render(activeRoute) : renderChrome()); installRouter(render); bootstrap().catch((error) => { showToast(`初始化失败：${error.message}`); store.setConnection({ status:"ERROR", stale:true }); });
