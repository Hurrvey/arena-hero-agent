# Arena Hero 持久探索、前沿侦察与敌情响应设计

## 背景与已验证问题

当前地图只绘制网格与当前 Turn 的可见对象，没有表达“当前可见”、“曾经探索但当前不可见”和“从未探索”三种区域。策略虽已使用 v0.14 视野半径与障碍遮挡计算当前可见格，但侦察目标仍是围绕 Core 的固定放射点，没有使用真正的探索边界。

2026-08-13 的实时历史检查证明这不是 API 停止或移动失败：最近的 Worker 移动持续被服务器判定成功，但 E2 在 16 个 Tick 内发生 11 次两格往返，E3 发生 9 次，E9 发生 5 次。这是成功动作组成的行为活锁。

同一段历史中，敌方 Ranger E12 从 Tick 100684 到 100702 多次可见，且已靠近我方 Worker。我方 Ranger 和 Vanguard 不在合法攻击距离内，因此当 Tick 不能射击或横扫；但现有目标选择也没有将战斗单位调向这个普通敌情，Core 防御仍显示 `CLEAR`。这是独立于侦察活锁的响应缺口。

## 目标

1. 地图清晰显示当前可见、已探索和从未探索三种区域。
2. 探索记忆按 Arena Hero 账号隔离，跨浏览器刷新和服务重启保留。
3. Worker 优先前往能增加视野的真实探索前沿，而不是在固定放射点之间往返。
4. 策略识别成功移动造成的两格和局部循环，能终止、换目标或合法等待。
5. 将 Core 安全等级与前线敌情分开；远处敌人威胁 Worker 时，即使 Core 仍为 `CLEAR`，也会产生撤离与拦截计划。
6. 所有实时决策仍为确定性 Python 逻辑；LLM 不进入 Tick 命令窗口。

## 非目标

- 不保存或绘制“推测的当前敌人”。敌人离开视野后不再是当前事实。
- 不将历史资源格当成仍然存在的资源。资源是动态状态，只有当前可见 Turn 能证明它仍然存在。
- 不构建世界边界、全图敌方热力图或雾中精确射击。
- 不用新的探索存储取代官方 Turn；Turn 始终是当前状态的唯一权威来源。
- 不为追求“每个单位都有动作”而强制移动；没有安全且有价值的动作时，`WAIT` 是正确降级。

## 选定方案

采用服务端按账号持久化的探索记忆，并让地图、Worker 前沿侦察和敌情响应共用相同的视野几何与领域模型。

未选用的方案：

- **前端 `localStorage`**：实现快，但换浏览器、清缓存或在另一个设备上打开即丢失，且实时策略无法使用。
- **每次从 Turn 历史重建**：无需新表，但启动成本随历史增长，而且会因保留策略清理而丢失探索记忆。

## 领域边界

将功能拆成四个边界，避免将 SQLite、画布和战术动作混在同一个模块内。

### `ExplorationMap`

纯领域对象，管理已探索格、已确认永久障碍和最后可见 Tick。它接收 `compute_visible_cells` 的结果，输出探索增量、指定区域查询和有界前沿候选。它不知道 SQLite、HTTP 或 Arena Hero 动作。

### `ExplorationRepository`

只负责按 `account_scope` 加载和合并区块位图。它不计算视野、不选侦察目标、不暴露 API Key。

### `FrontierScoutPlanner`

给空载且没有资源、Beacon、撤离或腾位任务的 Worker 分配稳定前沿租约。它使用探索图、当前障碍、当前占位和可见威胁，但不持久化帐号数据。

### `ContactResponsePlanner`

只基于当前可见敌人评估前线敌情，并为 Worker 撤离与战斗单位拦截提供目标。最后发现位置只是短期调查提示，不是隐藏敌人的当前位置。

## 账号范围与安全

运行时已使用 `SHA-256(API Key)` 生成跨进程单写者锁标识。探索功能复用同样的不可逆摘要作为 `account_scope`，并将新建 `runtime_sessions.account_hash` 从当前的常量 `configured` 改为这个摘要。

- API Key 不进入 SQLite、公开 API、日志或事件。
- `account_scope` 不返回浏览器。前端只能查询当前运行时所属账号的探索窗口。
- 不同 API Key 的区块行不共享，即使它们在同一个本地数据库中。
- 旧的 `configured` session 保持可读，但不用它推断新账号的探索记忆。

## 持久化模型

世界坐标无预知边界，因此不按单格建表，也不存一张无界位图。按 `32 × 32` 世界格分块，每个 mask 固定为 128 bytes：

```sql
CREATE TABLE exploration_accounts (
    account_scope TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE exploration_chunks (
    account_scope TEXT NOT NULL,
    chunk_x INTEGER NOT NULL,
    chunk_y INTEGER NOT NULL,
    explored_mask BLOB NOT NULL,
    obstacle_mask BLOB NOT NULL,
    last_seen_tick INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_scope, chunk_x, chunk_y),
    CHECK (length(explored_mask) = 128),
    CHECK (length(obstacle_mask) = 128)
);
```

坐标使用 floor division 归一化，因此负坐标也有唯一稳定的区块与局部 bit index。`exploration_accounts.revision` 是账号级单调版本；一次 Turn 合并真正改变任何 bit 时，同一 SQLite 事务内只递增一次，并将所有变更区块写入同一 revision。没有 bit 变化时不递增。`exploration_chunks.revision` 表示该区块最后一次改变时的账号 revision。

区块合并在 `BEGIN IMMEDIATE` 事务中对旧新 mask 执行按位 OR，再与原值比较。这使重试幂等，也保证即使将来放宽单写者约束，较旧观测也不会把已探索 bit 清零。

只有两种事实跨重启保留：

- `explored_mask`：该格历史上至少一次处于当前可见区域。
- `obstacle_mask`：在当前可见时确认的永久障碍。

不在该表保存敌人、Beacon carrier 或“仍然存在”的资源。现有资源短期记忆仍受 TTL 和当前视野反证约束，不因该功能变成永久资源图。

## Tick 数据流

```text
官方 Turn
   |
   +--> 从存活我方 Core/Units 计算 current visible cells
   |       `-- 障碍使用 v0.14 supercover 视线遮挡
   |
   +--> ExplorationMap.observe
   |       +-- 内存中立即合并当前可见格，供本 Tick 规划
   |       `-- 生成受影响区块增量
   |
   +--> ContactResponsePlanner 评估当前可见敌情
   |
   +--> 实时 planner
   |       +-- Beacon / 生存 / 资源行为
   |       +-- Worker 撤离与前沿侦察
   |       `-- 守军与机动响应单位
   |
   +--> submit complete plan
   |
   `--> 后提交持久化
           +-- 原始/公开 Turn 与 Plan
           +-- exploration chunk delta
           `-- 有界诊断与 service events
```

运行时不在启动时加载某账号的全部历史区块。收到第一个 Turn 后，它按当前受控对象周边与前沿搜索半径计算有界区块键，通过主键批量惰性加载工作集。后续 Tick 只在受控对象进入新区块时补充该工作集，并对长期远离的内存区块执行 LRU 淘汰。单 Tick 加载的区块数受候选前沿预算限制；超预算、SQLite 忙或读取失败时，本 Tick 只使用已加载区块加当前视野，不阻塞提交。

当前 Turn 始终先合并到内存工作集，因此即使持久化降级，当前视野也不会丢失。后提交持久化只保存本 Tick 变更的区块，不在每个 Tick 重写全部探索图。

## 当前视野计算

复用 `app.strategy.visibility.compute_visible_cells`，不在前端或侦察算法中重写另一套视野规则：

- Core 半径 5、Worker 半径 3、Vanguard 半径 4、Ranger 半径 5。
- 只有存活、受控对象提供视野。
- 使用 Manhattan 半径。
- 障碍按 supercover 线段遮挡目标后方，障碍格本身可被看见。
- 所有我方对象的可见格取并集。

已探索障碍可用于本地路由和地图暗区；但当前可见对象、资源和 Beacon 状态仍只从当前 Turn 读取。

## 探索 API 与有界窗口

当前状态投影增加：

```json
{
  "visibility": {
    "tick": 100713,
    "currentCells": [[-1139, -296]],
    "explorationRevision": 42
  },
  "contact": {
    "level": "THREATENING",
    "visibleEnemyCount": 1,
    "respondingUnitCount": 1
  }
}
```

`currentCells` 受我方单位数与视野半径自然约束，可以随当前状态发送。已探索世界可无界增长，不能每 Tick 全量嵌入 `/state/current`。

增加视口窗查询：

```text
GET /api/v1/exploration?minX=&minY=&maxX=&maxY=
```

- 账号范围由当前 runtime session 决定，请求无法指定 `account_scope`。
- 服务器验证整数坐标、顺序和最大窗口；超界返回受控 `422`。
- 运行时尚未建立任何 session 时返回受控 `404`。首次启动后，最后一个 session 的账号范围在暂停或停止期间仍可查询；下一个 runtime 启动会替换当前 session 与前端缓存世代。
- 一次最多返回 `96 × 96` 格，足以覆盖当前画布及预取边缘。
- 响应只返回窗口内的 `exploredCells`、`knownObstacleCells` 和当前账号级 `revision`。
- ETag 由账号 revision 和规范化窗口坐标共同生成。前端只对完全相同窗口发送 `If-None-Match`；未变更时服务器返回 `304`。不同窗口不会因 revision 相同而误用其他区域的缓存。

前端在摄像机平移、缩放或 `explorationRevision` 变更时防抖请求可见世界窗口。缓存键由 runtime/session 世代、规范化窗口和 ETag 组成；runtime 更换时清除旧内存缓存。未返回的格始终按“从未探索”渲染，不使用上一个账号或上一个窗口的数据。

## 地图三态渲染

绘制顺序调整为：背景→未探索底色→已探索地形→当前可见区域→风险/路线→当前对象。

| 状态 | 视觉 | 允许显示的内容 |
| --- | --- | --- |
| 当前可见 | 正常亮度、清晰网格、轻微青蓝视野边缘 | 我方对象、可见敌人、当前资源、当前 Beacon、障碍 |
| 已探索但当前不可见 | 深蓝灰遮罩，地形保留约 45% 亮度 | 已确认的永久障碍；不显示敌人，不把历史资源当成当前资源 |
| 从未探索 | 接近黑色的不透明迷雾，弱化网格 | 无世界对象或地形事实 |

已探索与未探索的边界绘制一条很细的青色前沿线。地图左下角图例增加“当前可见 / 已探索 / 未探索”，并在可访问性描述中说明已探索不等于当前安全。

画布在探索窗口请求失败时仍能工作：当前 `currentCells` 正常绘制，缓存中未经当前 revision 确认的格不升级为已探索。

## 前沿侦察分配

“前沿”定义为：已探索、当前可通行，并且至少有一个 cardinal 相邻格从未探索的格。前沿候选只在每个待分配 Worker 周围的有界半径内生成，并且总数截断，避免遍历无界历史地图。

只有满足以下条件的 Worker 参与侦察：

- 不是 Beacon carrier 或 runner；
- 没有 cargo；
- 不在可见资源格；
- 没有已分配资源路线；
- 不受到当前撤离、Core 腾位或战时命令约束。

前沿评分为：

```text
score =
  5 * 预计新增可见格
  + 长时间未访问奖励
  + 当前侦察租约粘性
  - 有界路径代价
  - 可见敌方威胁
  - 与其他 Worker 的视野重叠
  - 最近路线与反向边惩罚
```

平分时按 Worker raw UUID、前沿坐标排序，保证确定性。一个前沿目标在以下任一情况之前保持租约，不因每 Tick 重算而随意更换：

- Worker 到达目标并实际增加探索格；
- 目标已不再是前沿；
- 当前路径变成不合法或明显危险；
- 连续无进展达到阈值；
- 检测到局部振荡。

## 防振荡路由

现有的 `detect_two_cell_oscillation` 保留，但不再只是将固定放射目标旋转一步。每个侦察 Worker 保存：

- 最近 8 个位置；
- 最近有向移动边及执行 Tick；
- 当前租约目标、最佳路径距离和无进展 Tick；
- 带到期 Tick 的短期禁行反向边。

触发条件：

- `A -> B -> A`；
- `A -> B -> C -> B`；
- 成功移动但到目标的有界路径代价连续不下降；
- 移动后探索覆盖连续不增加。

触发后的处理是一个完整状态转换：

1. 当前前沿租约标记为失败并进入短期冷却。
2. 对立即返回的有向边设置短期 taboo；除非该步是唯一合法生存路径，否则不选。
3. 从不同方向的未探索前沿重新分配。
4. 如果没有能提高探索覆盖的安全合法路径，当 Tick 等待，而不制造假进度。

下一步移动使用有界 A* 或等价最短路算法，统一考虑已知障碍、当前占位、同 Tick 预留目的格、当前可见攻击范围和 taboo 边。搜索超过预算时返回“无已证明路径”，不伪造直线可达性。

## 敌情模型

Core 防御等级继续专门回答“Core 现在有多危险”。新的前线敌情独立回答“当前可见敌人是否威胁我方其他资产”：

| 等级 | 当前 Turn 判定 | 响应 |
| --- | --- | --- |
| `NONE` | 没有可见敌人 | 无新的前线命令 |
| `SPOTTED` | 有可见敌人，但本 Tick 不能攻击高价值我方对象，一步后也不能形成该威胁 | 避免新 Worker 侦察路线进入其攻击扇区；有闲置机动战斗单位时有界监视 |
| `THREATENING` | 敌人当前能攻击，或一步后能攻击 Beacon carrier、载货 Worker、正在采集的 Worker 或我方资源路线 | 受威胁 Worker 优先撤离，分配一个机动战斗单位拦截 |
| `ENGAGED` | 我方战斗单位已有当前合法攻击，或敌人已能对上述高价值对象造成当 Tick 伤害 | 合法直接攻击优先于追击，同时撤离非战斗资产 |

Core 攻击者仍由现有 `WATCH/APPROACH/ATTACK/LETHAL` 评估，两个等级可同时存在，例如 `Core=CLEAR, Contact=THREATENING`。

## Worker 敌情行为

Worker 任务优先级调整为：

1. Beacon carrier 本 Tick 生存与交付。
2. 对当前或一步后合法攻击的安全撤离。
3. 载货回 Core 或合法 DEPOSIT。
4. 当前格 HARVEST。
5. 已分配资源路线。
6. 已租约的探索前沿。
7. 无安全、有价值目标时 `WAIT`。

撤离目的格在所有合法 cardinal 目的格中按以下顺序选择：预计可见攻击数更少、不靠近威胁者、不进入另一个敌人攻击范围、不阻塞 Core，然后按方向固定顺序破平。安全撤离不得被普通侦察租约覆盖。

## 战斗响应与守军底线

直接合法攻击的顺序继续遵守现有高价值优先级：致命 Core 攻击者、敌方 Beacon carrier、威胁我方 carrier 的敌人、Core 攻击者、敌方 Core，再到普通敌人。新设计主要修复“不在攻击距离内时完全没有响应”。

### 机动响应单位

- Core 为 `APPROACH` 及以上时，现有全面回防规则优先，不为前线敌情拆守军。
- Core 为 `CLEAR/WATCH` 且 contact 至少为 `THREATENING` 时，从非 carrier 战斗单位选一个响应者。
- 有 Vanguard 时至少保留一个 Vanguard 在 Core 的 1–2 格守备环。
- Ranger 是远距离响应首选；若没有 Ranger，才在不打破最低守备的前提下选 Vanguard。
- 选择按抵达合法拦截位的有界路径代价、单位存活性、再按 raw UUID 排序。

若当前敌人不在攻击距离内：

- Ranger 向能对威胁者形成清晰射线的安全攻击位移动；
- Vanguard 向敌人与受威胁资产之间的拦截格移动；
- 目标格必须是当前规则下合法移动目的格，不预定未来攻击一定成功。

### 短期调查租约

敌人离开视野后，对应响应单位可在最多 3 个 Tick 内前往“最后发现位置”调查。该租约只允许移动，不允许：

- 将旧 enemy UUID 当成当前可见 target；
- 基于旧位置提交精确 `SHOOT(target_id)`；
- 在当前 Turn 没有规则支持的情况下宣称敌人仍在该格。

目标区域变成当前可见且没有对应敌人时，或租约到期时，调查结束，响应单位返回守备或其他权威任务。调查租约仅存在于运行时内存，不跨重启持久化。

## 计划解释与界面反馈

解释层增加稳定 reason code：

- `SCOUT_FRONTIER`：前往未探索前沿；
- `SCOUT_REASSIGNED`：旧租约无进展或振荡后换目标；
- `SCOUT_WAIT_NO_SAFE_FRONTIER`：没有可证明安全且有收益的前沿；
- `CONTACT_EVADE`：Worker 撤离当前或一步威胁；
- `CONTACT_INTERCEPT`：战斗单位向拦截位移动；
- `CONTACT_ATTACK`：对当前合法的前线威胁攻击；
- `CONTACT_INVESTIGATE`：在有界租约内调查最后发现区域；
- `DEFENSE_HOLD`：保留 Core 最低守备而不追击。

计划面板继续只显示同 Tick 权威计划。威胁卡将 Core 与前线拆开显示，例如：

```text
Core 威胁  CLEAR
前线敌情  THREATENING
```

当前可见敌人的短 ID 可出现在当前计划解释中；稳定原始 UUID、账号摘要和雾中敌人记忆不进入公开 API。

## 可观测性与自适应输入

每 Tick 输出不含 ID 和世界坐标的有界诊断：

- `newly_explored_cells`；
- `visible_cells`；
- `frontier_assignments`；
- `frontier_progress_ticks`；
- `oscillation_detections`；
- `oscillation_prevented_moves`；
- `scout_wait_ticks`；
- `contact_level`；
- `threatened_workers`；
- `evading_workers`；
- `responding_combat_units`；
- `contact_attack_actions`；
- `contact_investigation_ticks`。

自适应 LLM 可以在周期总结中读取这些聚合数据，但不能直接设置 Worker 目标、敌人坐标或 Tick 动作。现有 profile 只在已有严格范围内影响侦察步长、战斗倾向和守军数量，本设计不新增任意指令或代码通道。

## 失败处理

- **探索表加载失败**：运行时降级为当前进程内存探索图，地图仍显示当前可见格，策略不停止。
- **单个区块长度或 revision 损坏**：忽略该行并记录不含数据内容的受控警告，不将损坏 mask 解码成世界事实。
- **后提交保存失败**：保留当前内存图供本进程使用，发出有界 service warning；不重复提交本 Tick 动作。
- **探索 API 失败**：前端保持当前可见层，未确认窗口按未探索显示，不用过期账号数据填充。
- **前沿搜索超预算**：当 Tick `WAIT` 或保留更高优先级动作，不提交未验证移动。
- **敌人消失**：降级为最多 3 Tick 的调查租约；调查不转化为当前敌人事实或精确攻击。
- **无合法撤离/拦截路径**：使用已有的 DEPOSIT、HEAL、合法直接攻击或 `WAIT` 降级，不伪造安全性。

## 保留与空间边界

`exploration_chunks` 是账号长期记忆，不随 7/30 天 Turn/service event 保留策略删除。这是跨重启语义的必要条件。

为防止异常状态无界写入：

- 只从官方 Turn 中存活我方对象的有界视野生成区块。
- 一个 Tick 可变更的格数受我方对象数和最大视野半径限制。
- 仓储层拒绝错误长度的 mask 和非整数区块坐标。
- 设置页可在后续独立功能中增加“清除当前账号探索记忆”；本实现计划不将删除操作与基础显示和策略修复捆绑。

## 测试策略

实现按测试先行进行，分为以下层次。

### 纯领域测试

- 正负坐标在 `32 × 32` 区块中的编码和解码精确往返。
- 多个我方对象视野取并集，障碍格可见且遮挡后方。
- 观测只能把 bit 从未探索变为已探索，不能回退。
- 前沿定义、候选截断、评分和确定性破平正确。
- `A-B-A`、`A-B-C-B` 和无探索增益能触发重分配或等待。
- 可见敌情在 `NONE/SPOTTED/THREATENING/ENGAGED` 之间按合法几何切换。

### 仓储与 API 测试

- migration 幂等，mask 长度检查生效。
- 同一账号重启后能读到已探索格，不同账号不可互见。
- session 保存真实账号摘要，公开 API 和日志不含 API Key 或账号摘要。
- 视口窗查询有最大面积和坐标验证，只返回当前账号数据。
- 损坏区块可受控降级，不会让实时策略退出。

### 策略回归

- 构造 20 个无资源 Tick，Worker 有正向探索进展或合法等待，不能持续出现 `A <-> B` 循环。
- 两个 Worker 不持有同一前沿租约，而且视野重叠惩罚能促使它们分散。
- Worker 的侦察移动必须有前沿、资源、Beacon、撤离或 Core 腾位原因；无目标时不为刷动作而游走。
- 普通可见 Ranger 威胁 Worker 时，Worker 选择安全撤离，机动 Ranger 向合法拦截位移动，Vanguard 保留 Core 守备。
- Core 为 `APPROACH/ATTACK/LETHAL` 时，现有回防规则压过前线追击。
- 敌人离开视野后只能生成有界调查移动，不能生成针对旧 UUID 的精确攻击。
- Beacon carrier 生存、资源容量、动态价格、Core 防御和同 Tick 结算旧回归保持通过。

### 前端测试

- 当前可见、已探索和未探索格在画布中使用不同图层与颜色。
- 已探索暗区显示永久障碍，但不显示历史敌人或历史资源。
- 摄像机平移/缩放请求受上限窗口，缓存按 revision 失效。
- 探索 API 不可用时，当前可见层仍正常且未知区域不被错误提亮。
- 地图图例和可访问性文字解释“已探索不等于当前安全”。
- 威胁卡同时显示 Core 威胁和前线敌情，计划原因与同 Tick 权威动作一致。

## 验收标准

1. 服务重启后，同一 Arena Hero API Key 的已探索区域仍然存在；不同 Key 完全隔离。
2. 地图上能明显分辨当前可见、已探索和未探索，且障碍遮挡后的格不会被错误标成当前可见。
3. 敌人、Beacon carrier 和资源不因历史探索记忆而继续显示为当前事实。
4. 在无资源、无 Beacon 即时任务的 20 Tick 回归中，不存在持续两格往返；每个侦察移动产生探索进展或有明确租约原因。
5. 可见普通敌人威胁 Worker、carrier 或经济路线且存在合法响应动作时，策略至少产生撤离、直接攻击或机动拦截之一，不再仅显示 `CLEAR` 后继续普通侦察。所有响应都无合法格或动作时，必须显式记录受控 `WAIT` 原因。
6. Core 威胁升至 `APPROACH` 或更高时，回防优先于远程追击；Core 安全时仍保留最低 Vanguard 守备。
7. 所有新动作遵守 v0.14 action slot、射程、视线、占位、阶段顺序和 raw UUID 结算规则。
8. 公开 API、WebSocket、日志、界面和自适应遥测不泄露 API Key、`account_scope`、稳定原始 UUID 或用户名。
9. 新功能故障时降级为当前视野、内存探索和已有确定性策略，不阻断 Tick 提交。
10. 全量 Python/Node 测试、`compileall`、`pip check` 和 `git diff --check` 通过。

## 实施分段

这是一个联合功能，但可以按垂直切片实施，每一步都保持可运行：

1. 区块编码、`ExplorationMap` 与 SQLite 账号持久化。
2. 当前视野投影、有界探索 API 和地图三态渲染。
3. 前沿租约、有界路由与防振荡 Worker 侦察。
4. 独立 contact 评估、Worker 撤离和机动战斗单位响应。
5. 计划解释、前端敌情反馈、诊断、自适应聚合与端到端发布门禁。

后续实现计划必须将每个分段继续拆成可测试的小步提交，不在一次修改中同时重写全部地图、仓储和战术规则。
