export function decorateStateWithPlan(state, plan) {
  if (!state) return state;
  const accepted = new Set(["ACCEPTED", "RECEIVED"]);
  if (!plan || Number(plan.tick) !== Number(state.tick) || !accepted.has(String(plan.status).toUpperCase())) return state;
  const entities = [state.core, ...(state.units || [])].filter(Boolean);
  const byId = new Map(entities.map((entity) => [String(entity.id), entity]));
  const effective = plan?.plan || {};
  const unitActions = effective.unitActions || effective.unit_actions || {};
  const explanations = plan?.explanation?.actions || [];
  const routes = [];
  for (const [entityId, action] of Object.entries(unitActions)) {
    const origin = byId.get(String(entityId))?.position;
    if (!origin) continue;
    const explanation = explanations.find((item) => item.entityId === entityId && item.actionType === action?.type);
    const target = explanation?.target || action?.expectedCell || action?.expected_cell;
    if (!target) continue;
    routes.push({ entityId, actionType: action.type || "WAIT", points: [origin, target] });
  }
  const units = (state.units || []).map((unit) => {
    const action = unitActions[String(unit.id)];
    return action ? { ...unit, currentAction: actionLabel(action) } : unit;
  });
  const coreAction = effective.coreAction || effective.core_action;
  const core = state.core && coreAction
    ? { ...state.core, currentAction: actionLabel(coreAction) }
    : state.core;
  return { ...state, core, units, planRoutes: routes };
}

function actionLabel(action) {
  const type = String(action?.type || "WAIT");
  const direction = action?.direction ? ` ${action.direction}` : "";
  const unitType = action?.unitType || action?.unit_type;
  if (type === "SPAWN" && unitType) return `${type} ${unitType}`;
  if (["MOVE", "SWEEP", "START_MOVE"].includes(type)) return `${type}${direction}`;
  return type;
}
