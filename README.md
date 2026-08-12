# Arena Hero Agent

一个面向 Arena Hero v0.14 的确定性 Beacon-first 战术脚本。它读取每个 Turn 的当前可见状态，为 Core、Worker、Ranger 和 Vanguard 选择合法行动，再通过官方 Python SDK 提交完整计划。

> Arena Hero 没有单一总分，而是三个独立的 lifetime 排行榜：Beacon 持有 Tick、造成伤害、Core 摧毁参与。策略优先保护 Beacon 持有时间，再把剩余行动用于可见敌人伤害和 Core 参与；这能提高积分效率，但不能承诺固定名次或保证第一名。

## 项目简介

战术入口位于 **balanced_tactic.py**。脚本不猜测迷雾中的敌人或 Beacon 归属；每一回合都以服务器提供的当前状态为权威，只把短期资源线索作为会过期、会被当前视野推翻的探索提示。

它使用同步的 ArenaHeroClient 连接 Arena Hero，并且每个 Turn 只提交一次当前计划。API key 只在本地运行时读取，不写入代码或日志。

## 主要特性

- **有边界的 Beacon 任务**：Beacon 坐标始终公开，但未知状态不会立刻抽走经济 Worker。经济达到 6 名 Worker 后才允许远程 runner；当前明确为 `GROUND` 且距离较近时可以机会性抢取。runner 必须持续缩短距离，停滞或 A-B-A-B 往返会被释放并进入冷却。
- **载体保命与护卫**：只把当前 Turn 明确可见的己方 carrier 当作事实；状态进入迷雾时按未知处理，避免把过期 carrier 当成 10 点 shield cap；可见威胁会触发回 Core、护卫或在 Core 同格预排战后 HEAL。
- **机会型高分战斗**：Ranger 优先敌方 Beacon carrier、威胁己方 carrier 的单位和敌方 Core，直接目标不足时向由可见敌人一步移动推导的合法空格射击；Vanguard 同理使用预测 Sweep。
- **Worker 经济与探索**：成熟目标为 23 名 Worker。可见/短期记忆资源通过确定性的最小成本一对一分配；没有资源时，空载 Worker 按八方向递增环分散侦察，不再返回 Core 原地或在两格之间永久往返。载货 Worker 仍优先返航存入，受威胁时撤退。
- **分阶段扩军**：生产价格使用官方 `unit_cost()`，依次建设 6 Worker → 1 Vanguard → 1 Ranger → 12 Worker → 3 Vanguard → 4 Ranger → 23 Worker，随后按 Ranger 偏重比例继续扩大战斗力；容量不足时选择当前能够容纳的类型。
- **受约束的确定性记忆**：永久障碍可以长期记忆；资源提示有 64 Tick TTL，路线有停滞阈值和冷却。carrier 仅使用当前可见状态或同 Tick 拾取计划，不把迷雾里的敌人、资源或 Beacon 归属当作当前事实。
- **确定性决策**：相同状态下使用固定方向顺序和 UUID 排序，减少同局行为漂移。

## 环境要求

- Windows PowerShell 或其他可以运行 Python 的终端。
- Python 3.11 或更高版本。
- 一个 Arena Hero 账号和对应的 API key。
- 能访问 Arena Hero API，默认端点为 https://api.arenahero.io。

依赖版本记录在 **requirements.txt** 中：

~~~text
arena-hero>=0.2.9,<0.3
~~~

## 安装

在 PowerShell 中进入项目目录并安装依赖：

~~~powershell
cd D:\arena-hero
python --version
python -m pip install -r requirements.txt
~~~

如果系统中有多个 Python，请确保安装依赖和运行脚本使用的是同一个 python 命令。

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

## 启动战术

安装依赖并准备好 API key 后运行：

~~~powershell
cd D:\arena-hero
python .\balanced_tactic.py
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
- 更新 `balanced_tactic.py`、`economic_strategy.py` 或 `skills/arena-hero` 后，正在运行的旧 Python 进程不会热加载修改；请按 Ctrl+C 停止旧进程，再执行同一启动命令。新进程会继续读取本地 `.env`，无需重新声明变量。

## 可选：双 LLM 自适应评估与重设计

默认情况下脚本只运行确定性的 `balanced_tactic.py`，不会连接任何 LLM。打开自适应模式后，主战术仍然是唯一的行动权威：每个 Turn 先根据当前可见状态生成并提交一份完整计划，提交完成后才把脱敏遥测写入本地队列。到达 Tick/时间间隔后，后台线程才启动两阶段循环：

1. **评估模型**读取仓库内置的 `skills/arena-hero` v0.14 完整规则/SDK 包、聚合事件和 Beacon/经济/战斗 scorecard，输出缺陷、规则风险和改进建议。它会看到零资源 Tick、空闲 Worker、路线停滞、两格振荡和 runner 推进等指标。
2. **重设计模型**读取完全相同且带 SHA-256 指纹的项目内规则包、当前 `StrategyProfile` 和上一步评估，只能输出有限 JSON 参数（Worker 目标、经济启动规模、Beacon 任务半径/租约、资源 TTL/停滞阈值、战斗倾向、载体安全余量等）。它不能提交行动、写 Python、执行 Shell 或读取迷雾信息；系统不会执行 LLM 生成的 Python。

候选参数会在本地做 schema、范围、规则指纹和 Beacon/经济下限校验，再以 Turn 边界替换配置。后续周期用同一 scorecard 做金丝雀比较；分数按配置比例下降时自动**回滚**到上一份 profile。LLM 只接收聚合对象/行动计数和事件数值，不接收玩家名、对象 ID、精确坐标或路线目标。LLM 超时、网络错误、skill 文件缺失、指纹不匹配或 JSON 不合法都会保留旧策略，并且不会中断主战术。内部 score 会奖励 Beacon、采集、存入、战斗和 runner 推进，并惩罚零资源停滞、空闲 Worker、卡路与振荡；它只是调参信号，不是官方总榜，也不能保证固定第一名。

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
ARENA_HERO_ADAPTIVE_MIN_SECONDS=900
ARENA_HERO_ADAPTIVE_AUTO_APPLY=1
ARENA_HERO_ADAPTIVE_ROLLBACK_RATIO=0.15
ARENA_HERO_ADAPTIVE_STATE_DIR=.codex_tmp/adaptive
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
$env:ARENA_HERO_ADAPTIVE_MIN_SECONDS="900"
$env:ARENA_HERO_ADAPTIVE_AUTO_APPLY="1"
$env:ARENA_HERO_ADAPTIVE_ROLLBACK_RATIO="0.15"
$env:ARENA_HERO_ADAPTIVE_STATE_DIR=".codex_tmp/adaptive"
python .\balanced_tactic.py
~~~

`ARENA_HERO_LLM_BASE_URL` 需要指向 OpenAI-compatible 的版本根路径（例如 `/v1`）；程序会请求其 `/chat/completions`。`ARENA_HERO_LLM_MODEL_VERBOSITY` 支持 `low`、`medium`、`high`；`ARENA_HERO_LLM_MODEL_REASONING_EFFORT` 支持 `none`、`minimal`、`low`、`medium`、`high`、`xhigh`，但具体模型/供应商可能支持更窄的集合。两项都留空时不会把对应字段发送给供应商并保留旧的 `temperature=0`；设置任一项后会省略 `temperature`，避免新模型拒绝不兼容参数。评估模型和重设计模型可以是同一个模型，也可以分别指定。缺少 `ARENA_HERO_ADAPTIVE=1`、独立 LLM key 或任一模型名时，自适应功能安全关闭，原有战术行为不变。将 `ARENA_HERO_ADAPTIVE_AUTO_APPLY` 设为 `0` 可先观察报告而不自动采用候选 profile。

运行态 `telemetry.jsonl`、周期报告和 `state.json` 默认位于 `.codex_tmp/adaptive/`，已被 `.gitignore` 排除。规则包优先从项目内 [skills/arena-hero](skills/arena-hero) 加载，每个周期重新读取并计算指纹；因此 clone 到另一台机器时不需要给 LLM 单独安装 skill。更新这个目录后，下一轮评估会自动使用新规则，旧规则的模型输出不会通过指纹校验。只有项目包不存在时才兼容用户目录里的旧安装；项目包存在但缺文件时本轮安全失败，不会跨目录拼接规则。

## 观察、停止与手动操作

脚本运行后，可以打开 [Arena Hero Arena](https://app.arenahero.io/arena)，并登录与 API key 对应的同一个账号查看对局。

网页和终端可以同时观察，但同一个 Tick 上的手动行动可能覆盖 Agent 已提交的对应行动。想让战术完全自动运行时，请避免在同一 Tick 手动操作同一对象。

## 战术逻辑摘要

| 对象 | 决策重点 |
| --- | --- |
| Core | 静止时才接受存入、治疗、修盾或生产；无 upkeep，价格通过 `unit_cost(unit_type, population)` 计算，不主动发起迁移。 |
| Ranger | 敌方 Beacon carrier > 威胁己方 Beacon carrier 的敌人 > 敌方 Core > 其他敌人；对 carrier/Core 使用精确目标射击，预测位置使用合法 target-free cell fire。 |
| Vanguard | 先 Sweep 相邻敌方 carrier，再处理威胁己方 carrier 的格、敌方 Core 和其他目标；无真实目标时才 Sweep 可见敌人可能进入的相邻预测格。 |
| Worker | 生存/载货返航 > 当前格采集 > 一对一资源路线 > 八方向环形侦察；己方 carrier 优先保命/回 Core。资源线索过期、停滞或振荡时自动换目标。 |
| Beacon | 只有当前状态为 `GROUND` 且同格才拾取；远程任务通常要等 6 Worker 经济启动，近距离可见地面 Beacon 可机会性抢取；状态未知时不猜测 carrier 或 ground。 |

每个 Turn 的高层优先级是：

1. 处理同格 Beacon 拾取和 carrier 生存动作。
2. 处理敌方 Beacon carrier/Core 的直接攻击与预测格攻击。
3. 先维持 Worker 的存入、采集、独占资源分配与分散探索；只有经济就绪或近距离明确机会时才租用 Beacon runner，并持续检查其进度。
4. 为 Core HP、可预见的非致命伤害和 Beacon carrier 恢复预留资源；普通 Unit heal 只使用剩余预算，必要时允许同格 Beacon carrier 预排一次满血/战后 HEAL，再执行 Core HEAL/REPAIR。
5. 使用 v0.14 动态价格按 6W → 1V1R → 12W → 3V4R → 23W 的阶段扩军，成熟后保持 Ranger 偏重并补 Vanguard 护卫。

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
python -m pytest -q
~~~

如果终端提示找不到 pytest，只为本地测试安装它：

~~~powershell
python -m pip install pytest
~~~

当前测试集覆盖确定性战术与自适应闭环（运行 `python -m pytest -q` 可查看精确数量），包括：

- Ranger 射击范围、对齐和障碍判断。
- Vanguard 相邻目标选择。
- Worker 采集、存入、最小成本一对一分配、八方向探索、TTL、卡路冷却、两格振荡恢复和受威胁撤退。
- 单位/Core 恢复（含战后预防性 HEAL）、有租期 Beacon runner/carrier、分阶段 23/3/4 扩军、预测射击/扫击和动态生产价格。
- Core 移动时禁止存入和生产。
- API key 不被打印，以及每个 Turn 只提交一次计划。
- 项目内置 Arena Hero skill 的完整性/优先级、两阶段 evaluator/designer 规则指纹、聚合经济评分、LLM 提示脱敏、JSON/范围校验、提示长度上限、金丝雀回滚和自适应故障 fail-open。

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
| [economic_strategy.py](economic_strategy.py) | 有界资源记忆、一对一分配、环形侦察、路线进度与 runner 租约。 |
| [strategy_policy.py](strategy_policy.py) | 有范围约束的 `StrategyProfile` 与 Beacon/经济 score 计算。 |
| [adaptive_strategy.py](adaptive_strategy.py) | 脱敏遥测、规则指纹、双 LLM 协调器、金丝雀回滚与禁用模式。 |
| [skills/arena-hero](skills/arena-hero) | 两个 LLM 每轮共同加载的项目内 v0.14 规则与 SDK 文档包。 |
| [requirements.txt](requirements.txt) | Arena Hero Python SDK 版本约束。 |
| [test_balanced_tactic.py](test_balanced_tactic.py) | 无需真实连接的行为测试。 |
| [test_economic_strategy.py](test_economic_strategy.py) | 经济分配、探索、停滞、振荡与 runner 租约测试。 |
| [test_adaptive_strategy.py](test_adaptive_strategy.py) | 自适应遥测、评分、传输和回滚测试。 |
| [LICENSE](LICENSE) | GNU GPL v3 许可证全文。 |

## 许可证

本项目按 [GNU General Public License v3](LICENSE) 发布。
