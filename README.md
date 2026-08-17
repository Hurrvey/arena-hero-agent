# Arena Hero Agent

一个面向 Arena Hero 的本地自动化 Agent：确定性战术负责每个 Turn 的完整计划，FastAPI + Canvas 控制台负责启动、暂停、观察、策略版本、自适应候选与历史回放，SQLite 保存权威快照和审计记录。

> Arena Hero 没有单一总分，而是三个独立的 lifetime 排行榜：Beacon 持有 Tick、造成伤害、Core 摧毁参与。策略优先保护 Beacon 持有时间，再把剩余行动用于可见敌人伤害和 Core 参与；这能提高积分效率，但不能承诺固定名次或保证第一名。

## 项目简介

战术入口位于 **balanced_tactic.py**。脚本不猜测迷雾中的敌人或 Beacon 归属；每一回合都以服务器提供的当前状态为权威，只把短期资源线索作为会过期、会被当前视野推翻的探索提示。

它使用同步的 ArenaHeroClient 连接 Arena Hero，并且每个 Turn 只提交一次当前计划。API key 只在本地运行时读取，不写入代码或日志。

## 主要特性

- **本地战术控制台**：`http://127.0.0.1:8000` 提供实时地图、计划理由、事件、单位、策略编辑、自适应评分、历史和脱敏设置；Arena Hero/LLM key 不会发送到浏览器。
- **可靠 Runtime**：CLI 与 Web 共享同一账户锁；每个新 Tick 最多提交一次，10,000 Tick 长跑使用有界去重窗口。
- **SQLite 权威历史**：快照、计划、事件、指标、策略 revision、固定自适应窗口和候选统一写入 `data/arena_hero_agent.db`。

- **有边界的 Beacon 任务**：Beacon 坐标始终公开，但未知状态不会立刻抽走经济 Worker。经济达到 6 名 Worker 后才允许远程 runner；当前明确为 `GROUND` 且距离较近时可以机会性抢取。runner 必须持续缩短距离，停滞或 A-B-A-B 往返会被释放并进入冷却。
- **载体保命与护卫**：只把当前 Turn 明确可见的己方 carrier 当作事实；状态进入迷雾时按未知处理，避免把过期 carrier 当成 10 点 shield cap；可见威胁会触发回 Core、护卫或在 Core 同格预排战后 HEAL。
- **机会型高分战斗**：Ranger 优先敌方 Beacon carrier、威胁己方 carrier 的单位和敌方 Core，直接目标不足时向由可见敌人一步移动推导的合法空格射击；Vanguard 同理使用预测 Sweep。
- **四级动态 Core 防御圈**：可见战斗单位会被分为 `WATCH`、`APPROACH`、`ATTACK`、`LETHAL`。平时只保留 1 Vanguard + 2 Ranger 的小型常备守军；敌军一步后可形成攻击位时，非载体战斗单位回防并切换战时生产；当前 Core 攻击者会被提权，致命攻击者优先于敌方 Core。
- **持久探索与真实边界侦察**：当前可见区域保持明亮；曾经探索但当前不可见的区域保留为深色历史地形；从未探索区域保持近乎不透明。探索进度按 Arena 账号隔离并跨重启保存，只保存探索/永久障碍位，不保存敌人、资源或 Beacon 归属。
- **抗振荡 Worker 路由**：成熟目标为 23 名 Worker。可见/短期记忆资源通过确定性的最小成本一对一分配；空载 Worker 领取真正连接未知区域的 frontier 租约；两格往返、短周期重复和失败边触发 tabu/cooldown，无法推进时明确 WAIT，而不是制造假进度。载货 Worker 仍优先返航存入。
- **接敌与 Core 防御分层**：Core `APPROACH+` 永远优先回防；Core 安全时，受威胁 Worker 先撤离，Ranger 优先拦截，至少一名 Vanguard 留守，敌人消失后只移动调查 3 Tick，绝不按旧 UUID 盲射。
- **分阶段扩军**：生产价格使用官方 `unit_cost()`，依次建设 6 Worker → 1 Vanguard → 1 Ranger → 12 Worker → 3 Vanguard → 4 Ranger → 23 Worker，随后按 Ranger 偏重比例继续扩大战斗力；容量不足时选择当前能够容纳的类型。
- **受约束的确定性记忆**：永久障碍可以长期记忆；资源提示有 64 Tick TTL，路线有停滞阈值和冷却。carrier 仅使用当前可见状态或同 Tick 拾取计划，不把迷雾里的敌人、资源或 Beacon 归属当作当前事实。
- **确定性决策**：相同状态下使用固定方向顺序和 UUID 排序，减少同局行为漂移。

## 环境要求

- Windows PowerShell 或其他可以运行 Python 的终端。
- Python 3.11 或更高版本。
- 一个 Arena Hero 账号和对应的 API key。
- 能访问 Arena Hero API，默认端点为 https://api.arenahero.io。

完整依赖与开发工具锁定在 `pyproject.toml` / `uv.lock`；`requirements.txt` 保留 CLI 最小兼容安装：

~~~text
arena-hero>=0.2.9,<0.3
~~~

## 安装

推荐使用 uv 和仓库固定的 Python 3.11：

~~~powershell
cd D:\arena-hero
uv sync --python 3.11 --group dev
~~~

没有 uv 时，CLI 仍可用 `python -m pip install -r requirements.txt`；Web 控制台需要安装 `pyproject.toml` 中的完整依赖。

## 配置 API key

### 推荐：本地 `.env`（一次配置）

仓库根目录已经放好一个被 Git 忽略的 `.env` 模板。打开它，填入自己的
`ARENA_HERO_API_KEY`；如果要启用双 LLM 自适应评估，再填入
`ARENA_HERO_LLM_API_KEY`、两个模型名，并保持 `ARENA_HERO_ADAPTIVE=1`：

~~~powershell
cd D:\arena-hero
notepad .env
python .\balanced_tactic.py
~~~

脚本启动时会自动读取这个文件，因此不需要每次重新声明一长串
`$env:ARENA_HERO_...`。如果同名变量已经由当前进程、CI secret 或密钥管理器
注入，进程变量优先，`.env` 不会覆盖它。克隆仓库后不会自动得到 `.env`（这是
有意的安全设计），只需在本机重新创建或复制自己的配置即可。默认读取的是
仓库（脚本）目录下的 `.env`；空值按未设置处理。

如果你刚编辑了 `.env`，但仍收到 `AuthenticationError`，先清除当前 PowerShell
会话里可能残留的旧值，再启动一次：

~~~powershell
Remove-Item Env:ARENA_HERO_API_KEY -ErrorAction SilentlyContinue
python .\balanced_tactic.py
~~~

这是一次性清理；之后程序会继续从 `.env` 读取，不需要重新声明 key。

### 推荐：运行时隐藏输入

直接启动脚本：

~~~powershell
python .\balanced_tactic.py
~~~

如果当前进程没有设置 ARENA_HERO_API_KEY，脚本会显示 Arena Hero API key: 提示，并使用隐藏输入读取密钥。输入时字符不会回显到终端。

### 临时使用环境变量（可选）

脚本会优先读取当前进程中的 ARENA_HERO_API_KEY。在自动化环境中，可以让本机密钥管理器或 CI 的 secret 注入这个变量，然后照常运行：

~~~powershell
python .\balanced_tactic.py
~~~

如果只想清除当前 PowerShell 会话中的变量，可以执行：

~~~powershell
Remove-Item Env:ARENA_HERO_API_KEY
~~~

请遵守以下安全规则：

- 不要把 API key 发到聊天、Issue、Pull Request 或截图中。
- 不要把 API key 写进 balanced_tactic.py、README.md 或其他源文件。
- 不要提交包含密钥的 .env 文件；仓库的 .gitignore 已经忽略 .env，但提交前仍应检查 git status。
- GitHub 登录凭据和 Arena Hero API key 是两套不同的凭据。

## 启动 Web 控制台（推荐）

~~~powershell
cd D:\arena-hero
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

浏览器访问 `http://127.0.0.1:8000`。Web 服务本身不需要 API key 即可启动并查看空状态、策略和设置；只有点击“启动 Agent”时才读取本地 `.env` 并连接 Arena Hero。服务仅允许 loopback，MVP 不应绑定 `0.0.0.0`。

页面“暂停”会继续接收并保存权威 Turn，但不提交 AGENT 计划；恢复后只从下一次新 Tick 继续，不补交暂停期间错过的旧 Tick。“停止”会关闭 SDK 并释放账户锁。

地图使用三种明确状态：当前可见区域为明亮网格，曾经探索但当前不可见的区域为深色历史地形，从未探索区域近乎不透明。当前可见资源消失会立即失效；历史区域只保留永久障碍，不保留敌人、资源或 Beacon 归属。Beacon 坐标按官方规则始终公开，但状态和 carrier 在不可见时显示为未知。策略内部的短期资源记忆只参与受规则约束的寻路，不会被前端伪装成当前可见事实。计划的 `ACCEPTED` 表示服务已接收；下一 Turn 到达后，上一 Tick 的私有结果会独立保存为 `RESOLVED`，可在历史页查看，二者不会混为同一状态。

探索历史使用 `SHA-256(API Key)` 派生的本地账号作用域隔离并存入 SQLite，原始 API key 不进入数据库、REST、WebSocket、DOM 或 LLM 提示。`GET /api/v1/exploration?minX=&minY=&maxX=&maxY=` 返回至多 96×96 窗口内的 `exploredCells`、`knownObstacleCells` 坐标数组和单调 `revision`；本 Tick 当前可见格由 `/api/v1/state/current` 的 `visibility.currentCells` 提供。账号作用域由服务端当前运行配置决定，调用方不能指定。

总览页的威胁卡同时显示两条互不混淆的状态：`Core` 防御等级表示本体安全，`接敌`等级表示前沿当前可见敌情和响应单位数。地图与诊断不会把历史敌情当作当前事实。

### CLI 兼容入口

安装依赖并准备好 API key 后运行：

~~~powershell
cd D:\arena-hero
.\.venv\Scripts\python.exe .\balanced_tactic.py
~~~

正常运行时，终端会持续输出类似内容：

~~~text
tick=123 accepted=True
tick=124 accepted=True
~~~

- tick 是当前游戏 Tick。
- accepted=True 表示本回合的计划已被服务接受，不等同于每个行动都命中或本局已经获胜。
- 脚本进程必须保持运行，关闭 PowerShell 窗口会停止后续计划提交。
- 按 Ctrl+C 可以停止脚本。
- CLI 与 Web 使用同一套账户锁。已经运行其中一个时，另一个会拒绝启动；先停止旧实例，不要并行控制同一账号。
- 更新 `balanced_tactic.py`、`economic_strategy.py` 或 `skills/arena-hero` 后，正在运行的旧 Python 进程不会热加载修改；请按 Ctrl+C 停止旧进程，再执行同一启动命令。新进程会继续读取本地 `.env`，无需重新声明变量。

## 可选：双 LLM 自适应评估与重设计

默认情况下脚本只运行确定性的 `balanced_tactic.py`，不会连接任何 LLM。打开自适应模式后，主战术仍然是唯一的行动权威：每个 Turn 先根据当前可见状态生成并提交一份完整计划，提交完成后才把脱敏遥测写入本地队列。到达 Tick/时间间隔后，后台线程才启动两阶段循环：

1. **评估模型**读取仓库内置的 `skills/arena-hero` v0.14 完整规则/SDK 包、聚合事件和 Beacon/经济/防御/战斗 scorecard，输出缺陷、规则风险和改进建议。它会看到零资源 Tick、空闲 Worker、frontier 推进、振荡检测/拦截、runner 推进、接敌等级/响应计数、Core 威胁/致命暴露、实际 Core 伤害、守军覆盖和 Worker 疏散等指标。
2. **重设计模型**读取完全相同且带 SHA-256 指纹的项目内规则包、当前 `StrategyProfile` 和上一步评估，只能输出有限 JSON 参数（Worker 目标、经济启动规模、Beacon 任务半径/租约、资源 TTL/停滞阈值、战斗倾向、载体安全余量、常备守军规模、观察圈和 Worker 疏散半径等）。它不能提交行动、写 Python、执行 Shell 或读取迷雾信息；系统不会执行 LLM 生成的 Python。

候选参数会写入 SQLite，并做 schema、范围、最低样本数、规则指纹、当前策略 revision 和 `LETHAL` 防御状态校验。默认 `ARENA_HERO_ADAPTIVE_AUTO_APPLY=0`，由控制台人工应用；通过的候选只创建 `PENDING` revision，并在下一 Turn 规划前原子激活。LLM 只接收有界聚合对象/行动计数、探索计数、接敌等级/计数和事件数值，不接收玩家名、对象 ID、精确坐标、路线、frontier 租约、chunk mask、账号作用域、原始计划或提示词。LLM 超时、网络错误、skill 文件缺失、指纹不匹配或 JSON 不合法都会保留旧策略，并且不会中断主战术。它只是调参信号，不是官方总榜，也不能保证固定第一名。

### 本地 `.env` 配置（推荐）

LLM 凭据必须与 Arena Hero 的 `ARENA_HERO_API_KEY` 分开。上面的本地
`.env` 模板已经列出自适应模式所需的全部变量；程序会在启动时自动读取它。
请只在本机填写真实值，不要提交 `.env`。空值按“未设置”处理，内置默认值仍会生效：

~~~text
ARENA_HERO_ADAPTIVE=1
ARENA_HERO_LLM_API_KEY=独立的_LLM_API_KEY
ARENA_HERO_LLM_BASE_URL=https://api.openai.com/v1
ARENA_HERO_LLM_MODEL_VERBOSITY=high
ARENA_HERO_LLM_MODEL_REASONING_EFFORT=high
ARENA_HERO_EVALUATOR_MODEL=评估模型名
ARENA_HERO_DESIGNER_MODEL=重设计模型名
ARENA_HERO_ADAPTIVE_INTERVAL_TICKS=60
ARENA_HERO_ADAPTIVE_MINIMUM_SAMPLES=30
ARENA_HERO_ADAPTIVE_MIN_SECONDS=900
ARENA_HERO_ADAPTIVE_AUTO_APPLY=0
ARENA_HERO_ADAPTIVE_ROLLBACK_RATIO=0.15
ARENA_HERO_ADAPTIVE_STATE_DIR=adaptive
~~~

### 临时 PowerShell 覆盖（可选）

如果只想为当前 PowerShell 会话临时覆盖 `.env`，可以使用下面的命令；关闭终端后这些设置就会消失：

~~~powershell
$env:ARENA_HERO_ADAPTIVE="1"
$env:ARENA_HERO_LLM_API_KEY="独立的_LLM_API_KEY"
$env:ARENA_HERO_LLM_BASE_URL="https://api.openai.com/v1"
$env:ARENA_HERO_LLM_MODEL_VERBOSITY="high"
$env:ARENA_HERO_LLM_MODEL_REASONING_EFFORT="high"
$env:ARENA_HERO_EVALUATOR_MODEL="评估模型名"
$env:ARENA_HERO_DESIGNER_MODEL="重设计模型名"
$env:ARENA_HERO_ADAPTIVE_INTERVAL_TICKS="60"
$env:ARENA_HERO_ADAPTIVE_MINIMUM_SAMPLES="30"
$env:ARENA_HERO_ADAPTIVE_MIN_SECONDS="900"
$env:ARENA_HERO_ADAPTIVE_AUTO_APPLY="1"
$env:ARENA_HERO_ADAPTIVE_ROLLBACK_RATIO="0.15"
$env:ARENA_HERO_ADAPTIVE_STATE_DIR="adaptive"
python .\balanced_tactic.py
~~~

`ARENA_HERO_LLM_BASE_URL` 需要指向 OpenAI-compatible 的版本根路径（例如 `/v1`）；程序会请求其 `/chat/completions`。`ARENA_HERO_LLM_MODEL_VERBOSITY` 支持 `low`、`medium`、`high`；`ARENA_HERO_LLM_MODEL_REASONING_EFFORT` 支持 `none`、`minimal`、`low`、`medium`、`high`、`xhigh`，但具体模型/供应商可能支持更窄的集合。两项都留空时不会把对应字段发送给供应商并保留旧的 `temperature=0`；设置任一项后会省略 `temperature`，避免新模型拒绝不兼容参数。评估模型和重设计模型可以是同一个模型，也可以分别指定。缺少 `ARENA_HERO_ADAPTIVE=1`、独立 LLM key 或任一模型名时，自适应功能安全关闭，原有战术行为不变。将 `ARENA_HERO_ADAPTIVE_AUTO_APPLY` 设为 `0` 可先观察报告而不自动采用候选 profile。

Web Runtime 的新状态统一位于 `data/arena_hero_agent.db`；旧版根目录 `adaptive/state.json` 只会在启动时只读、按内容哈希幂等导入 Profile，绝不会删除或改名，`telemetry.jsonl` 与旧报告也不会重新发送给 LLM。确认数据库中的版本正确后可以自行归档旧目录。规则包优先从项目内 [skills/arena-hero](skills/arena-hero) 加载，每个周期重新读取并计算指纹；另一台机器无需单独安装 skill。更新规则包后，旧规则候选会因指纹不符而失效。

浏览器、REST/WebSocket payload、SQLite 公共快照、日志和 LLM prompt 都不会返回 Arena Hero/LLM key。对象 UUID 会转换为会话内短 ID；原始权威快照只留在本机 SQLite。默认数据保留策略由本地 `RetentionService` 提供（原始快照 7 天、服务事件 30 天），执行清理前应先备份 `data/arena_hero_agent.db`。

## 观察、停止与手动操作

脚本运行后，可以打开 [Arena Hero Arena](https://app.arenahero.io/arena)，并登录与 API key 对应的同一个账号查看对局。

网页和终端可以同时观察，但同一个 Tick 上的手动行动可能覆盖 Agent 已提交的对应行动。想让战术完全自动运行时，请避免在同一 Tick 手动操作同一对象。

## 战术逻辑摘要

| 对象 | 决策重点 |
| --- | --- |
| Core | 静止时才接受存入、治疗、修盾或生产；无 upkeep，价格通过 `unit_cost(unit_type, population)` 计算，不主动发起迁移。 |
| Ranger | 致命 Core 攻击者 > 敌方 Beacon carrier > 威胁己方 carrier 的敌人 > 其他 Core 攻击/逼近者 > 敌方 Core；对 carrier/Core 使用精确目标射击。被选为守军时保持 Core 2–3 格防区。 |
| Vanguard | 使用相同目标层级 Sweep 相邻格；被选为守军时保持 Core 1–2 格防区。无威胁时只保留小型守军，其余单位继续护送、抢 Beacon 和进攻。 |
| Worker | carrier 生存 > 严格更安全的接敌规避 > 载货返航 > 当前格采集 > 一对一资源路线 > frontier 租约探索；失败边和短周期往返进入 cooldown，找不到安全前沿时明确 WAIT。Core 正在受击且 Worker 也处于近 Core 火线时，Core 防御优先。 |
| Beacon | 只有当前状态为 `GROUND` 且同格才拾取；远程任务通常要等 6 Worker 经济启动，近距离可见地面 Beacon 可机会性抢取；状态未知时不猜测 carrier 或 ground。 |

每个 Turn 的高层优先级是：

1. 处理同格 Beacon 拾取和 carrier 生存动作。
2. 计算 Core `CLEAR/WATCH/APPROACH/ATTACK/LETHAL` 状态；致命攻击者优先清除，逼近时召回战斗单位。
3. 处理敌方 Beacon carrier、威胁己方 carrier 的敌人、其他 Core 攻击者和敌方 Core；Core 为 `CLEAR/WATCH` 时才允许 Ranger 前沿拦截，并保留至少一名 Vanguard 守军。
4. 维持 Worker 存入、采集、独占资源分配与 frontier 探索；接敌 Worker 只向当前攻击数严格下降的格子规避，无安全响应时明确 WAIT；敌人消失后最多调查 3 Tick 且只移动不盲射。
5. 为 Core HP、可预见的非致命伤害和 Beacon carrier 恢复预留资源；普通 Unit heal 只使用剩余预算。
6. 无威胁时按 6W → 1V1R → 12W → 3V4R → 23W 扩军；`APPROACH+` 暂停 Worker，优先补足 1 Vanguard + 2 Ranger 守军并继续补战斗单位。

### 动态防御等级

| 等级 | 当前可见判定 | 响应 |
| --- | --- | --- |
| `CLEAR` | Core 观察圈内无战斗敌军 | 只保留最小守军，其余单位继续 Beacon、经济和进攻任务 |
| `WATCH` | 敌军进入观察圈，但下一步尚不能形成攻击位 | 守军不远征，不停止正常经济 |
| `APPROACH` | 敌军移动一步后可合法攻击 Core | 非载体战斗单位回防，暂停 Worker 生产，补 Vanguard/Ranger |
| `ATTACK` | 敌军当前可合法攻击 Core | 提升攻击者目标优先级，近 Core 受击 Worker 撤到可见安全侧翼 |
| `LETHAL` | 可见合法攻击数 ≥ Core HP + shield | 当前 Core 攻击者成为最高优先目标；不假设战后 HEAL/REPAIR 能救致死伤害 |

### 生产倾向

v0.14 已移除每 Tick upkeep。前 20 个 Unit 使用基础价格，Core 在不需要恢复且格子有空间时直接使用当前 `unit_cost()` 扩军；第 21 个 Unit 起价格按官方动态公式增加。默认方案先把 Worker 从初始规模扩到 6，建立可持续采集/侦察面；再补 1 Vanguard 与 1 Ranger，继续扩到 12 Worker、3 Vanguard、4 Ranger，最终形成 23 Worker 的经济底盘。达到成熟规模后再按默认约 2:1 的 Ranger/Vanguard 倾向扩大火力。

| 单位 | 代码中的基础成本 |
| --- | ---: |
| Worker | 5 |
| Vanguard | 10 |
| Ranger | 12 |

如果 Core 正在移动、出生格容量不足或资源不足，脚本会放弃该动作，不凭空编造行动；若可见 Unit 将在本 Tick 战死，脚本也会预览战后人口，允许动态价格下降后再尝试一次无成本失败的 SPAWN。战斗单位不读取已删除的 upkeep 字段；人口很高导致首选战斗单位价格超过容量时，只要仍有可容纳的单位类型，就会回退到该类型，减少常见的生产死锁。若所有单位价格都已超过当前容量，脚本会等待人口或资源状态变化，不会提交注定失败的生产动作。

## 本地测试

不需要 API key，也不会启动真实对局。运行完整测试集：

~~~powershell
cd D:\arena-hero
.\.venv\Scripts\python.exe -m pytest -q
~~~

首次运行浏览器测试需要安装仓库锁定的 Chromium：

~~~powershell
.\.venv\Scripts\python.exe -m playwright install chromium
~~~

如果终端提示找不到 pytest，只为本地测试安装它：

~~~powershell
python -m pip install pytest
~~~

当前测试集覆盖确定性战术与自适应闭环（运行 `python -m pytest -q` 可查看精确数量），包括：

- Ranger 射击范围、对齐和障碍判断。
- Vanguard 相邻目标选择。
- Core 五级威胁分类、Ranger 障碍射线、守军稳定选择、致命攻击者优先、回防、战时生产和 Worker 安全侧翼疏散。
- Worker 采集、存入、最小成本一对一分配、持久 frontier 租约、TTL、tabu/cooldown、两格/短周期振荡拦截、接敌规避和受控 WAIT。
- 单位/Core 恢复（含战后预防性 HEAL）、有租期 Beacon runner/carrier、分阶段 23/3/4 扩军、预测射击/扫击和动态生产价格。
- Core 移动时禁止存入和生产。
- API key 不被打印，以及每个 Turn 只提交一次计划。
- 项目内置 Arena Hero skill 的完整性/优先级、两阶段 evaluator/designer 规则指纹、聚合经济/防御/探索/接敌评分上下文、LLM 提示脱敏、JSON/范围校验、提示长度上限、金丝雀回滚和自适应故障 fail-open。
- FastAPI/SQLite/REST/WebSocket 契约、`ACCEPTED` 与 `RESOLVED` 生命周期、10,000 Tick 有界长跑、静态素材 MIME、安全头和 Chromium 真实浏览器响应式布局。

也可以运行依赖和差异检查：

~~~powershell
python -m pip check
git diff --check
~~~

## 上传到 GitHub

本项目的远程仓库是 [Hurrvey/arena-hero-agent](https://github.com/Hurrvey/arena-hero-agent)。

如果命令行尚未登录 GitHub，可以先使用 GitHub CLI 完成登录和 Git 凭据配置：

~~~powershell
gh auth login
gh auth setup-git
~~~

如果本地已经绑定 origin，修改文档或代码后执行：

~~~powershell
cd D:\arena-hero
git add .
git commit -m "update Arena Hero agent"
git push
~~~

如果是第一次绑定这个远程仓库：

~~~powershell
git remote add origin https://github.com/Hurrvey/arena-hero-agent.git
git branch -M main
git push -u origin main
~~~

如果 GitHub 仓库在创建时已经生成了自己的 README 或 LICENSE，首次推送可能出现 non-fast-forward。此时先安全合并远程提交：

~~~powershell
git pull --no-rebase --no-edit --allow-unrelated-histories origin main
git push -u origin main
~~~

不要在没有确认远程内容可以丢弃的情况下使用 git push --force。

## 常见问题

### ModuleNotFoundError: No module named 'arena_hero'

使用同一个 Python 解释器重新安装依赖：

~~~powershell
python -m pip install -r requirements.txt
python -c "import arena_hero; print(arena_hero.__version__)"
~~~

### 脚本提示 API key 或认证失败

确认使用的是 Arena Hero 账号对应的 key，并重新运行脚本。不要把 key 粘贴到 Issue、聊天或错误报告中；报告问题时只提供错误类型和不含凭据的终端输出。

### 终端没有持续输出

确认 PowerShell 窗口仍在运行，且脚本没有因认证、网络或依赖错误退出。Ctrl+C 会正常结束脚本；再次运行即可重新连接。

### git push 报 non-fast-forward

先执行：

~~~powershell
git pull --no-rebase --no-edit --allow-unrelated-histories origin main
git push -u origin main
~~~

如果出现冲突，运行 git status 查看文件，解决冲突标记后 git add、git commit，再重新 git push。

## 安全说明

本项目只根据当前 Turn 可见的状态做决定，不尝试绕过迷雾或读取隐藏信息。运行日志不会主动打印 API key；测试中的密钥字符串仅为本地假数据。

竞技结果还会受到地图、对手、随机事件、服务器状态和网络延迟影响。该 tactic 的目标是提供稳定、可解释、可测试的平衡策略，而不是承诺固定名次。

## 项目文件

| 文件 | 用途 |
| --- | --- |
| [balanced_tactic.py](balanced_tactic.py) | 战术决策、API key 读取和持续运行入口。 |
| [defense_strategy.py](defense_strategy.py) | 当前可见 Core 威胁分级、攻击几何和确定性守军选择。 |
| [economic_strategy.py](economic_strategy.py) | 有界资源记忆、一对一分配、路线进度与 runner 租约。 |
| [app/strategy/frontier.py](app/strategy/frontier.py) | frontier 提取、确定性租约、有界 A*、tabu/cooldown 与振荡拦截。 |
| [app/strategy/contact.py](app/strategy/contact.py) | 当前可见接敌分级、Worker 规避、单响应者拦截与三 Tick 调查。 |
| [strategy_policy.py](strategy_policy.py) | 有范围约束的 `StrategyProfile` 与 Beacon/经济 score 计算。 |
| [adaptive_strategy.py](adaptive_strategy.py) | 脱敏遥测、规则指纹、双 LLM 协调器、金丝雀回滚与禁用模式。 |
| [app](app) | FastAPI、Runtime、SQLite、策略模块、自适应安全层与 REST/WebSocket API。 |
| [frontend](frontend) | 本地战术控制台、Canvas 地图及总览/策略/自适应/历史/设置页面。 |
| [skills/arena-hero](skills/arena-hero) | 两个 LLM 每轮共同加载的项目内 v0.14 规则与 SDK 文档包。 |
| [requirements.txt](requirements.txt) | Arena Hero Python SDK 版本约束。 |
| [test_balanced_tactic.py](test_balanced_tactic.py) | 无需真实连接的行为测试。 |
| [test_economic_strategy.py](test_economic_strategy.py) | 经济分配、探索、停滞、振荡与 runner 租约测试。 |
| [test_defense_strategy.py](test_defense_strategy.py) | 威胁等级、障碍射线与守军选择纯逻辑测试。 |
| [test_adaptive_strategy.py](test_adaptive_strategy.py) | 自适应遥测、评分、传输和回滚测试。 |
| [LICENSE](LICENSE) | GNU GPL v3 许可证全文。 |

## 许可证

本项目按 [GNU General Public License v3](LICENSE) 发布。
