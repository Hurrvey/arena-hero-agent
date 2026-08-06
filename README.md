# Arena Hero Agent

一个面向 Arena Hero 的确定性平衡战术脚本。它读取每个 Turn 的当前可见状态，为 Core、Worker、Ranger 和 Vanguard 选择合法行动，再通过官方 Python SDK 提交完整计划。

> 这是一套可运行、可测试的 starter tactic，目标是平衡经济、战斗和生存，不承诺每局固定排名或保证第一名。

## 项目简介

战术入口位于 **balanced_tactic.py**。脚本不维护跨 Turn 的隐藏目标，也不猜测迷雾中的敌人或资源；每一回合都根据服务器提供的当前状态重新决策。

它使用同步的 ArenaHeroClient 连接 Arena Hero，并且每个 Turn 只提交一次当前计划。API key 只在本地运行时读取，不写入代码或日志。

## 主要特性

- **Core 防守优先**：先处理可见战斗威胁和恢复，再考虑生产。
- **合法远程攻击**：Ranger 只攻击当前可见、处于射程内、方向对齐且没有可见障碍阻挡的目标。
- **近战清场**：Vanguard 选择相邻可见敌人，优先处理敌方 Core 所在格。
- **Worker 经济循环**：Worker 采集当前可见资源、把货物运回静止的 Core，并避开可见敌人和障碍。
- **威胁撤退**：携带货物或受到附近敌人威胁时，Worker 优先向 Core 方向撤退。
- **恢复与保守生产**：受伤单位在静止 Core 旁按预算恢复；Core 会先治疗或修复，再在保留 upkeep 和安全储备后生产。
- **机会型 Beacon**：只有在 Beacon 当前可见且位于受控对象所在格时才尝试拾取，不追逐不可见目标。
- **确定性决策**：相同状态下使用固定方向顺序和 UUID 排序，减少同局行为漂移。

## 环境要求

- Windows PowerShell 或其他可以运行 Python 的终端。
- Python 3.11 或更高版本。
- 一个 Arena Hero 账号和对应的 API key。
- 能访问 Arena Hero API，默认端点为 https://api.arenahero.io。

依赖版本记录在 **requirements.txt** 中：

~~~text
arena-hero>=0.2.8,<0.3
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

### 推荐：运行时隐藏输入

直接启动脚本：

~~~powershell
python .\balanced_tactic.py
~~~

如果当前进程没有设置 ARENA_HERO_API_KEY，脚本会显示 Arena Hero API key: 提示，并使用隐藏输入读取密钥。输入时字符不会回显到终端。

### 使用环境变量

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

## 观察、停止与手动操作

脚本运行后，可以打开 [Arena Hero Arena](https://app.arenahero.io/arena)，并登录与 API key 对应的同一个账号查看对局。

网页和终端可以同时观察，但同一个 Tick 上的手动行动可能覆盖 Agent 已提交的对应行动。想让战术完全自动运行时，请避免在同一 Tick 手动操作同一对象。

## 战术逻辑摘要

| 对象 | 决策重点 |
| --- | --- |
| Core | 静止时才接受存入、治疗、修盾或生产；生产前保留当前 upkeep 和 5 点 Core 安全储备，不主动发起迁移。 |
| Ranger | 从当前可见敌人中筛选射程 1–3、直线/对角对齐且无遮挡的目标；优先敌方 Core，再按生命值和确定性 ID 排序。 |
| Vanguard | 只对相邻可见格执行 Sweep；优先含敌方 Core 的格，其次选择敌人数量更多的格。 |
| Worker | 空载时前往当前可见资源；站在资源格时采集；携货回到静止 Core 时存入；附近有可见敌人时优先撤退。 |
| Beacon | 只在可见状态为 GROUND 且受控对象已经位于 Beacon 格时拾取；不会根据迷雾信息追踪 Beacon。 |

每个 Turn 的高层优先级是：

1. 处理可见且合法的 Ranger/Vanguard 战斗行动。
2. 在静止 Core 旁按当前预算安排受伤单位恢复。
3. 让 Worker 完成存入、采集、返航、撤退或避障移动。
4. 在不替换更高优先级行动的情况下，处理同格 Beacon 拾取。
5. Core 先治疗或修复，再检查容量、单位成本、预计 upkeep 和安全储备后生产。

### 生产倾向

生产不是“资源够成本就立即生产”。starter policy 会先补足 Worker 到 3 个，再补齐 Ranger 和 Vanguard，并保留资源以支付预计 upkeep 和 Core 安全储备：

| 单位 | 代码中的基础成本 |
| --- | ---: |
| Worker | 5 |
| Vanguard | 10 |
| Ranger | 12 |

如果 Core 正在移动、出生格容量不足、预算不足或没有合法目标，脚本会放弃该动作，不凭空编造行动。

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

当前测试覆盖 22 个行为场景，包括：

- Ranger 射击范围、对齐和障碍判断。
- Vanguard 相邻目标选择。
- Worker 采集、存入、分配、避障和受威胁撤退。
- 单位/Core 恢复、Beacon 拾取和生产预算。
- Core 移动时禁止存入和生产。
- API key 不被打印，以及每个 Turn 只提交一次计划。

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
| [requirements.txt](requirements.txt) | Arena Hero Python SDK 版本约束。 |
| [test_balanced_tactic.py](test_balanced_tactic.py) | 无需真实连接的行为测试。 |
| [LICENSE](LICENSE) | GNU GPL v3 许可证全文。 |

## 许可证

本项目按 [GNU General Public License v3](LICENSE) 发布。
