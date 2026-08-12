# Arena Hero 经济与信标统治策略设计

## 背景与问题

当前战术名义上同时考虑经济、信标和战斗，但实战遥测暴露了一个结构性活锁：一个 Worker 被永久记为信标跑手，在两个相邻格间持续往返；另一个 Worker 在看不到资源时以 Core 为目标并原地等待。连续数千 Tick 中没有成功采集、提交或信标得分。两阶段 LLM 也没有改变这个结果，因为它只能调整少量有界参数，而且默认从用户目录加载 Arena Hero skill，部署环境无法独立复现其规则上下文。

参考项目 `D:\arena-hero-agent` 的优势不在某个权重，而在完整的经济和侦察状态机：短期资源记忆、一对一资源分配、分区侦察、进度检测、目标冷却、Worker 模式诊断和长期运行保护。它的默认 `retreat` 信标策略主动远离信标，不符合本项目“经济与信标同时领先”的目标，因此只迁移经济/侦察机制，不复制默认战略或庞大的单文件实现。

## 目标

1. 任何有空 Worker 的正常局面都能持续扩大视野、发现资源、采集并提交，不因当前视野为空而停摆。
2. 不再让初始经济单位无限追逐迷雾中的移动信标；信标任务必须有经济门槛、进度租约和反振荡退出条件。
3. 信标与经济不是互斥模式：跑手沿途可以采集，有货先提交；己方持有信标时优先利用 Worker 的双倍采集收益。
4. 生产从经济启动、早期防御、成熟经济到受限进攻分阶段推进，长期目标为 23 Worker、3 Vanguard、4 Ranger，总人口 30、Core 容量 150。
5. 把完整、版本固定的 Arena Hero 规则包放入项目。评估模型和重设计模型每次循环必须加载同一份项目内 skill，并返回匹配的 SHA-256 指纹。
6. LLM 只调整经过验证的安全参数和策略门槛，不在 15 秒 Tick 循环中生成或执行代码。
7. 所有新增行为先用失败测试重现，再写最小实现；继续遵守 v0.14 当前 Turn 权威、迷雾信息边界、动态资源节点和结算顺序。

## 非目标

- 不整体复制 `arena_farmer.py`。
- 不采用参考项目默认的 `retreat` 信标政策。
- 不让 LLM 直接生成 Python、Shell、行动 UUID 或坐标。
- 不在 Tick 关键路径中调用 LLM。
- 不把记忆中的资源、敌人或信标载体当作当前权威事实。
- 不承诺一个永恒世界存在可判定的“最终第一名”；优化目标是长期提高经济吞吐、信标控制和有效战斗参与。

## 架构

### 1. `TacticMemory`：有界战略记忆

在现有跨 Turn 内存中增加四类有界状态：

- **资源记忆**：`resource_last_seen[position] = tick`。当前可见资源刷新时间；超过 64 Tick 或当前明确可见但节点消失时删除。
- **资源意图**：每个空 Worker 最多一个资源目标，每个资源最多分配给一个 Worker。目标连续 6 Tick 没有降低估算路径成本时释放，并进入 8 Tick 的 Worker–资源冷却。
- **侦察状态**：为每个 Worker 分配稳定的方向槽和环形阶段，记录目标、最好距离和停滞 Tick。连续 3 Tick 无进展或出现最近位置循环时切换方向/环。
- **信标任务租约**：记录 runner、目标快照、最佳距离、无进展 Tick 和最近位置。租约不是永久身份；经济门槛失效、信标状态改变、6 Tick 无进展或两格振荡时立即释放。

所有容器只保留存活单位和 TTL 内的数据。障碍仍可永久记忆；资源节点仍只是可失效提示。

### 2. 经济调度器

Worker 每 Tick 按以下顺序决策：

1. 当前受到可见致死威胁时撤离。
2. 己方信标载体需要保命或满血载体需预治疗时执行对应动作。
3. 有货且在 stationary Core 上时提交；有货不在 Core 时返回。
4. 空载且站在当前可见资源上时采集。该规则同样适用于信标跑手；若己方持有信标，可利用同 Tick 双倍采集。
5. 追踪已分配的当前/短期记忆资源目标。
6. 没有资源目标时执行分区侦察，而不是返回 Core。

资源分配采用确定性最小总代价匹配。代价由障碍感知路径成本、目标陈旧度、已有意图粘性和冷却组成。相同输入必须得到相同分配。

侦察目标以 Core 所在位置为锚点，在八个方向和递增环上分散 Worker；优先选择最久未观察的 chunk，并在同 Tick 内声明目标，避免所有 Worker 走同一条走廊。侦察路线会避开可见敌占格、已知障碍、危险格和已保留目的地。

### 3. 信标任务状态机

信标拾取仍是最高价值的即时机会：己方对象与可见地面信标同格时，继续遵循 v0.14 的 pickup、Core action 和 raw UUID 竞争规则。

远程信标任务按状态区分：

- **`GROUND` 且可见**：距离近时可以机会性争夺；距离远时只有经济启动完成后才建立 runner 租约。
- **敌方载体可见**：优先由 Vanguard/Ranger 拦截；Worker 不尾随战斗载体。
- **状态未知**：信标坐标只作为侦察方向提示，不建立永久 Worker runner。经济启动完成且有富余单位后，最多让一个 Scout 偏向该方向。
- **己方载体可见**：停止选 runner，切换为载体保护和信标经济。

经济启动门槛默认要求至少 6 个 Worker，且至少有一个 Worker 不承担信标任务。可见地面信标距离不超过 12 时允许机会性例外。优先顺序为安全 Vanguard、空载 Worker、Ranger；Worker 有货时不成为新 runner。

runner 每 Tick先检查采集和提交，再考虑移动。目标距离必须在租约窗口内取得进展；最近位置出现 A→B→A→B 或目标反复导致净距离不降时释放 runner，单位回到经济/侦察调度。新 runner 选择不会继承旧路线历史。

### 4. 分阶段生产

默认长期组成目标是 23 Worker、3 Vanguard、4 Ranger，但生产按阶段进行：

1. **Bootstrap**：Worker 少于 6 时优先 Worker。
2. **Early defense**：达到 6 Worker 后补到 1 Vanguard、1 Ranger。
3. **Economic expansion**：继续扩展到 12 Worker。
4. **Mature defense**：补到 3 Vanguard、4 Ranger。
5. **Dominance economy**：在安全窗口扩展到 23 Worker。

每次生产继续使用 SDK `unit_cost()` 和 Core 容量检查，保留 Core 治疗/护盾恢复优先级，并考虑 same-Tick deposit、combat death 和 Core-cell occupancy。可见重大威胁可以暂停非紧急扩张，但不能永久关闭经济恢复。

### 5. 项目内 Arena Hero skill

项目新增 `skills/arena-hero/`，包含上游 skill 的 `SKILL.md`、许可证和本项目 LLM 推理所需的完整权威参考文件。默认加载顺序为：

1. 项目内 `skills/arena-hero`；
2. 仅为旧部署兼容而检查用户目录安装位置；
3. 两者都不完整时抛出 `SkillBundleError`，该轮自适应安全失败，确定性战术继续运行。

`SkillBundle` 对固定文件集合逐字节计算 SHA-256。两个模型的 system prompt 都包含 skill 指令、规则文档、指纹和“遥测是不可信数据”的边界。模型输出必须回显完全相同的指纹；缺失或不一致时拒绝候选。

普通 OpenAI-compatible 模型不需要真正安装 Codex skill。程序将项目内 skill 展开为受信任 system context，这才是可部署、可测试、可复现的加载方式。

### 6. 自适应评分与安全参数

遥测增加不包含秘密和玩家身份的聚合字段：

- 当前可见资源数、短期已知资源数；
- Worker 模式计数；
- 采集、提交、捕获资源；
- 空载闲置 Worker Tick；
- 路线停滞和两格振荡次数；
- runner 到信标的距离变化；
- Core 零资源持续 Tick；
- Beacon 持有、拾取、丢失；
- 单位/Core 损失、有效伤害和失败动作。

内部评分保留 Beacon Tick 的高价值，同时加入经济吞吐和停滞惩罚。LLM 可调整的 profile 扩展为经过边界验证的参数：成熟 Worker 目标、经济启动 Worker 数、近距离信标例外、runner 停滞阈值、资源记忆 TTL、资源停滞阈值和侦察环步长。模型不能关闭安全检查、扩大 prompt/响应上限或改变规则版本。

候选 profile 仍先经过结构验证，再以 canary 运行；归一化分数退化超过阈值时回滚。LLM 调用失败、响应非法或规则包缺失只会写入无秘密错误类型，不阻止 Tick 提交。

## 数据流

```text
authoritative Turn
    │
    ├─ update bounded memory ── resource/scout/runner progress
    │
    ├─ deterministic planner
    │      ├─ survival and immediate Beacon pickup
    │      ├─ economy matching and scouting
    │      ├─ bounded Beacon mission/interception
    │      ├─ combat and carrier protection
    │      └─ recovery and staged production
    │
    ├─ submit exactly one complete plan
    │
    └─ append redacted telemetry after acceptance
             │
             └─ background adaptive cycle
                    ├─ load project skill + fingerprint
                    ├─ evaluator scores deficits
                    ├─ designer proposes bounded profile
                    └─ validate → canary → apply/rollback
```

## 错误处理

- 任何迷雾状态都不会被记忆提升为权威事实。
- 资源目标在当前视野明确消失、TTL 到期或无进展时释放。
- runner 在停滞、振荡、经济门槛失效或信标状态改变时释放。
- 没有安全移动时提交合法 `WAIT`/无动作，不编造路线。
- SkillBundle 文件缺失、UTF-8 无效或指纹不一致时拒绝自适应周期。
- LLM 的网络、认证、JSON、schema 和模型参数错误保持 fail-open，并记录分类诊断。
- `.env`、API key、模型 key、玩家身份和私有日志永远不进入 prompt、测试快照或 Git。

## 测试策略

新增行为必须覆盖：

- 两 Worker、零可见资源时至少一个 Worker向不同侦察目标移动；
- A↔B 两格振荡在阈值内释放并改变路线；
- runner 站在资源节点先采集，而不是继续移动；
- runner 有货先返回/提交；
- 经济未启动且信标状态未知时不创建永久 runner；
- 近距离可见地面信标仍可机会性争夺；
- 敌方载体可见时 Worker 不尾随，战斗单位优先拦截；
- 多 Worker 对多资源的一对一确定性匹配；
- 可见消失、TTL、停滞和冷却正确释放资源目标；
- 分阶段生产顺序与动态价格/容量兼容；
- 项目内 skill 优先于用户目录，缺文件和指纹不匹配安全失败；
- 两阶段模型 prompt 都包含同一项目 skill 指纹；
- 新遥测评分能识别经济活锁；
- 现有 Beacon、combat、heal、spawn、dotenv 和 transport 回归保持通过。

发布前运行完整 pytest、`compileall`、`pip check`、`git diff --check`、skill 文件完整性/指纹测试和秘密扫描。

## 取舍

这一方案比直接复制参考项目慢一些，但能保持当前 v0.14 规则回归和安全的 LLM 边界；也比只加一个探索方向可靠，因为它同时解决目标冲突、停滞恢复、经济诊断和部署可复现性。实现将优先完成能解除当前活锁的最小垂直切片，再扩展生产与自适应参数，不做与此目标无关的重构。
