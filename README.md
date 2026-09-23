# bixian（笔仙）

**一句话**：本机跑的一个小模型服务 —— 你给它**一份带编号的候选清单**（或者一张截图）和**一句"我要干什么"**，
它回答**该选第几个**，并且告诉你**它有多大把握**。把握不够时它说"我不确定"，而不是硬猜。

它只干"从一堆候选里挑一个"这一件事，但这件事到处都用得上。典型场景：屏幕上某个窗口有 40 个控件，
[athand](https://github.com/Offblink/Athand) 把它们编成 1~40 号并打印出编号图，问题是**该点哪个**。
以前要么靠写死的规则，要么把整屏截图丢给云端大模型；这里是本机一个 4B 的小模型，一次回答约 1 秒，
答不上来就直说 —— 对"点错一行就是数据事故"的场景来说，**它肯说不确定**比命中率更重要。

除了挑编号，客户端还给几种窄决策：yes/no、N 选 1、打分（见 `docs\CLIENT.md`）。

**这个仓里没有的东西**，先说清楚：权重（8 GB）、跑它用的 Python 环境（7.7 GB）、第三方快照、
以及真机取证件 —— 都不随仓分发，各自有重建或保留的办法，见下面「重建环境」和「许可与忽略」。

## 两种跑法：问一次，或者常驻

| | 什么时候用 | 代价（本机实测） |
|---|---|---|
| 一次性：`vlm_pick.py`、`eyes.py`、`decider.py ask` | 调试、评测、只问一次 | 全程约 56 秒（装权重约 44 秒 + 一次前向约 10 秒） |
| 常驻：`decider.py serve` | 会被反复问（例如 athand 的每个动作都可能问一次） | 权重只装一次（冷启 43 秒 / 盘缓存热 28 秒），之后每次约 1.0~1.6 秒 |

一句话的选择标准：**要问两次以上，就用常驻**。常驻的服务闲置 30 分钟会自己退出，把 5.8 GiB 显存还回去
（`--idle 0` 可关掉这个行为）。

两种跑法背后是同一个模型、同一份阈值设置（阈值只有一处来源：`client\src\bixian\policy.py`）。

> 别和旧的 kev 文本后端混：那一套跑在 **8009** 端口，源码与权重都已经删了（只留 `archive\kev_text.py`
> 和 `docs\ARCHIVE_KEV_TEXT.md` 讲怎么复活）。现在这个决策服务默认在 **8111**。

## 目录

| 目录 | 是什么 | 状态 |
|---|---|---|
| `vlm-probe\` | **工具**都在这里：`decider.py`（决策服务，最通用，契约见 `docs\DECIDER.md`）、`eyes.py`（两级"眼睛"接缝）、`vlm_pick.py`（从 athand 的候选里挑号）、`policies\`（阈值覆盖）、`tools\`（实验与评测脚本）、`cases\`（素材与产物）、`evidence\`（真机取证图，只在本机） | 活 |
| `vlm\` | **环境**：`.venv`（跑上面这些的 Python）+ `recipe\`（pyproject 与 uv.lock，用来重建） | 活 |
| `models\qwen3vl-4b\` | **权重**：Qwen3-VL-4B-Instruct 官方件，一行没改（零微调） | 活 |
| `scripts\` | 本机脚本：`fetch_ms_model.py`，从 ModelScope 拉一个模型仓并逐文件核对 sha256；大分片自己开 8 条连接、断了能接着下，只用标准库 | 活 |
| `docs\` | **所有文档都在这一处**（7 份）：`VLM_PROBE.md` 工具地图 · `DECIDER.md` 决策服务契约 · `VLM_DECISION_PROBE.md` 全部实测数据 · `CLIENT.md` 客户端说明 · `BIXIAN_KEV_NOTES.md` 旧的 kev 文本后端 · `ARCHIVE_KEV_TEXT.md` 死档为什么留着、怎么复活 · `HANDOFF_VLM_ATHAND.md` 上一轮交接 | 活 |
| `client\` | **你自己的 Bi-Xian 客户端**（命令行 + 策略 + 审计 + 评测，入口 `bin\decide.py`）；`vlm_pick` 与 `decider` 从它的 `src\` 里 import `policy`，阈值口径只有这一处 | 你的代码 |
| `repos\kev\` | **第三方** kev 快照（jaredpalmer，只剩 `docs\` 与 `skills\`，源码按你的要求删了） | 只读参考 |
| `archive\` | **死档**：`kev_text.py`（旧文本后端的 start / stop / selftest 三合一，纯 Python）+ `make_run_dir.py`；后端已废，复活步骤见 `docs\ARCHIVE_KEV_TEXT.md` | 死档 |

文档只有两处：本 `README.md`（给人看的入口）和 `docs\`（给人和 agent 看的笔记）。
客户端不再自带 `README`/`LICENSE`/`.gitignore` —— 那些现在归**外面这一层**。
本机全树唯一的第三方 `README` 在 `repos\kev\`（clone 自带的原件，别删）；`repos\` 不随仓分发，公开仓里没有它。

## 怎么跑

解释器一律用仓里的那个 `.venv`，命令在仓根执行（路径都相对仓根写好了）：

```powershell
$py = "vlm\.venv\Scripts\python.exe"

# 1) 只挑一个编号：给一份候选清单 + 一句意图
#    答得出来 -> 打印 athand 编号（exit 0）；不敢答 -> UNDECIDED（exit 2）
& $py vlm-probe\vlm_pick.py --listing vlm-probe\cases\fixture-som.json --intent "点击发送按钮" --k 9

# 2) 两级"眼睛"：小眼睛答不出时，把它看过的那张编号图交给你（exit 3）
& $py vlm-probe\eyes.py --listing vlm-probe\cases\fixture-som.json --intent "点击发送按钮"

# 3) 决策服务（给别的程序用）：先看权重在不在，再决定是常驻还是一次性
& $py vlm-probe\decider.py check
& $py vlm-probe\decider.py serve --port 8111        # 常驻：GET /health、POST /decide、POST /shutdown
& $py vlm-probe\decider.py ask --request req.json   # 一次性：每次都付一次完整加载
```

清单里 `frame` 写的是相对名时，会先在清单同目录找，所以像上面这样直接传 `cases\...` 就能用。

## 重建环境

环境配方在 `vlm\recipe\`（pyproject.toml + uv.lock）：

```powershell
uv sync --extra serve          # 在 vlm\recipe\ 里执行
```

权重用 `scripts\fetch_ms_model.py` 拉（只用标准库，系统自带 Python 3.13 就能跑）：

```powershell
python scripts\fetch_ms_model.py Qwen/Qwen3-VL-4B-Instruct -d models\qwen3vl-4b
```

关于下载脚本，两件值得知道的事：

- **不依赖任何外部下载器**。大分片（≥10 MiB）由脚本自己开若干条 ranged 连接（`--streams`，默认 8），
  进度写在 `<文件>.part` 与 `<文件>.part.json` 旁边；断网、Ctrl-C、进程崩了，重跑都是**接着下**，不从头来。
- 实测基线（2026-09-23，ModelScope 直连）：单条约 0.1 MiB/s，所以聚合速度大概就是"连接数 × 0.1"；
  **16 条会被服务端打断**（942 MiB 的分片跑到 333 MiB 时多条连接零推进），8 条稳。
  嫌慢也可以拿同机的另一个项目 Flower 下同一个链接（它就是为这件事写的）——本项目不依赖它，脚本里也没有它。

装 torch 必须用 cu128 的轮子（PyPI 上 Windows 那个是 CPU 版），细节在 `docs\BIXIAN_KEV_NOTES.md`。

## 许可与忽略

MIT，见 `LICENSE`。第三方只有 `repos\kev`（Apache-2.0）；kev 后端的权重是 Apache-2.0 的发布件，
但**不随仓分发**，需要就按 `docs\ARCHIVE_KEV_TEXT.md` 重下。

`.gitignore` 忽略：`vlm/.venv/`（解释器）、`models/`（权重）、`repos/`（第三方快照）、
下载中的 `*.part` 与 `*.part.json`、每次跑都会重新生成的编号图（`cases/*.som.png`、`cases/*.marked.png`），
以及 **`vlm-probe/evidence/`**。

**真机取证件只留本机**：`vlm-probe\evidence\` 与 `vlm-probe\cases\` 里的三张真机窗口图（`bare.png`、
`som.png`、`som_annotated.png`）—— 它们是真实窗口的截图和候选清单，会带出聊天内容、联系人和桌面布局。
这些不上传；文档里说"取证链"时，指的是本机这一份。而**夹具与合成素材**（`fixture-*.json`、`d_*.png`、
`*.cases`，以及 `vlm-probe\tools\make_desktops.py` 生成的一切）都随仓分发，照着文档能复现同样的评测。

## 数字从哪来

`docs\VLM_DECISION_PROBE.md`：§五 4-bit 量化 · §六 与 athand 的接缝 · §七 分辨率取舍 ·
§九 与旧文本档对照 · §十 真机一条龙。历史交接记录在 `%USERPROFILE%\Tools\handoffs\`
（最新一份：`handoff-bixian-reorg-20260923.md`）。
