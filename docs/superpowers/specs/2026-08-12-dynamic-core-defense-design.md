# Arena Hero 动态 Core 防御圈设计

## 目标

在不牺牲 Beacon 连续得分和长期经济扩张的前提下，为现有策略加入一套实时、确定性、可测试的 Core 防御机制。策略不能靠静态龟缩生存，也不能为了采集和抢 Beacon 把 Core 完全暴露；它必须根据当前可见状态在扩张与防守之间切换。

实时 Tick 不调用 LLM。LLM 只读取有界遥测，在自适应周期中评估防守质量并调整有限的策略参数。

## 核心原则

1. **Core 存活是硬约束。** Core 被摧毁会清除整支舰队，因此可见的致命攻击优先于普通经济、敌方 Core 和一般战斗目标。
2. **Beacon 仍是最高价值任务之一。** Beacon 载体的即时生存优先级不降低；只有会导致 Core 本 Tick 灭亡的攻击者排在敌方 Beacon 载体之前。
3. **不长期龟缩。** 无威胁时只保留小型常备守军，其余战斗单位继续护送、抢 Beacon 和压制经济。
4. **只相信当前 Turn。** 防御判断不使用雾中敌人记忆，也不把未结算的 LLM 推断当作事实。
5. **不假装战后治疗能救致死伤害。** HEAL、REPAIR_SHIELD、SPAWN 都在 combat 后解析；本 Tick 可见致死攻击必须通过移动、击杀优先级和保存资产来处理。

## 威胁等级

防御评估器每 Tick 基于当前可见敌方 Vanguard/Ranger、障碍、Core HP 和 shield 计算一个等级：

| 等级 | 判定 | 策略响应 |
| --- | --- | --- |
| `CLEAR` | 没有进入观察圈的可见战斗单位 | 保留最小守军，其余单位执行 Beacon/经济/进攻任务 |
| `WATCH` | 可见战斗单位进入 Core 观察半径，但下一步还不能形成合法攻击位 | 守军不远征，持续观察；不停止经济生产 |
| `APPROACH` | 敌军移动一步后可以形成对 Core 的合法攻击 | 非载体战斗单位回防，暂停 Worker 生产，切换战时战斗单位生产 |
| `ATTACK` | 敌军在当前位置本 Tick 可以合法攻击 Core | Core 攻击者进入高优先目标，受威胁的近 Core Worker 向安全侧翼疏散 |
| `LETHAL` | 当前可见合法攻击数大于等于 Core 的 HP + shield | 所有可打到的 Core 攻击者成为最高优先目标；不把战后恢复当作生存方案 |

`APPROACH` 是保守的一步几何判断：只判断一个未被障碍阻挡的 cardinal destination 是否能形成合法射击/横扫位置，不预测敌人一定会这样移动。

## 常备守军与回防

默认守军配置：

- 1 个 Vanguard，目标驻防距离为 Core 的 Manhattan 1–2 格；
- 2 个 Ranger，目标驻防距离为 Core 的 Manhattan 2–3 格；
- Beacon 载体不计入常备守军；
- 选择守军时按离 Core 的距离、再按 raw UUID 确定，保证结果稳定。

在 `CLEAR`/`WATCH` 时，仅选中的守军在超出防区后回到 Core 周围。在 `APPROACH` 及以上时，所有未承担 Beacon 载体职责的战斗单位都会优先回到防区，但仍可在途中攻击更高价值、当前合法的目标。

## 战斗目标优先级

直接攻击和 Vanguard 相邻扫击统一使用以下顺序：

1. 在 `LETHAL` 状态下，当前能够攻击 Core 的敌军；
2. 可见敌方 Beacon 载体；
3. 能威胁我方 Beacon 载体的敌军；
4. 当前能够攻击 Core 的其他敌军；
5. 一步后能够形成 Core 攻击位的敌军；
6. 可见敌方 Core；
7. 普通敌方对象。

同一层级继续按有效生命值和 raw UUID 排序。移动中的敌方 Core 仍遵守现有的结算位置预测规则。

## Worker 疏散

当等级至少为 `ATTACK`，且 Worker 位于 Core 周边、当前格也受到可见攻击时：

- Beacon 载体生存逻辑仍优先；
- 普通 Worker 不再默认向 Core 聚集；
- 从合法 cardinal destinations 中选择可见攻击数最低、不进入 Core、离 Core 与最近敌军更远的一格；
- 携带资源的 Worker 若无安全出口，保留现有 DEPOSIT/WAIT 降级行为，不提交非法移动。

疏散只使用当前可见攻击范围，不声称对雾中攻击安全。

## 战时生产

等级达到 `APPROACH` 后进入战时生产：

1. Vanguard 守军未达目标时优先 Vanguard；
2. Ranger 守军未达目标时优先 Ranger；
3. 两项已达标时按受约束的 Ranger/Vanguard 比例补充战斗单位；
4. 暂停新 Worker，直到威胁降到 `WATCH` 或 `CLEAR`；
5. 仍服从动态价格、资源容量、Core action slot 和格子容量规则。

这不会让同 Tick 的新兵阻止 combat 阶段伤害；它的用途是为后续 Tick 重建或加强防线。

## 有界策略参数

自适应 profile 新增以下参数，所有值都经过严格边界校验：

- `defense_priority`：防御倾向权重，范围 0.75–1.50；
- `defender_vanguard_target`：常备 Vanguard 数，范围 1–3；
- `defender_ranger_target`：常备 Ranger 数，范围 1–4；
- `defense_watch_radius`：观察半径，范围 4–8；
- `worker_evacuation_radius`：Worker 疏散半径，范围 2–5。

LLM 只能提交这些有界参数，不能输出任意 Python、动作或实时指令。

## 防御遥测与评分

每 Tick 输出：

- `defense_level`；
- `core_threat_ticks`；
- `projected_lethal_ticks`；
- `incoming_core_damage`；
- `defender_coverage`；
- `worker_evacuations`；
- `core_damage_taken`（由本 Tick 的 `CORE_DAMAGED` 事件统计）。

周期 Scorecard 聚合这些值。内部评分对 Core damage、致命暴露和 Core loss 施加惩罚，对实际守军覆盖与有效 Worker 疏散给予小额正向信号。正向权重保持较小，避免策略为了“刷防御分”长期龟缩。Beacon ticks 和资源收入仍是主要增长指标。

## 失败与降级

- 没有合法回防/疏散路径：不提交非法动作，保持现有合法降级。
- LLM、自适应状态或 skill bundle 失败：沿用上一个已验证 profile；实时防御不受影响。
- 只有不可见敌人：不虚构威胁，保持经济/Beacon 策略。
- 预测致死但攻击者无法在本 Tick 被阻止：优先保存可保存的 Beacon/cargo 位置，并避免宣称 HEAL 能救回 Core。

## 验收标准

1. 威胁等级、守军选择和目标排序具有独立单元测试。
2. 合法 Turn 回归覆盖 `WATCH`、`APPROACH`、`ATTACK`、`LETHAL`。
3. Core 遭攻击时目标优先级、战斗单位回防、Worker 疏散和战时生产都通过集成测试。
4. Beacon 载体生存相关旧测试保持通过。
5. 防御遥测进入两阶段 LLM 输入，profile 边界和畸形响应继续 fail closed。
6. 全量测试、compileall、pip check、diff check 全部通过。
