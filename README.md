# bixian — 本机本地决策模型工作区

这套东西的用途：给 agent 一个**本地跑的单次窄决策**能力（yes/no、N 选 1、打分），
以及把屏幕截图里的候选编号挑出来。权威笔记在 `docs\`，工具在 `vlm-probe\`。

## 目录

| 目录 | 是什么 | 状态 |
|---|---|---|
| `vlm-probe\` | **工具**：`decider.py`（**决策服务**：一个枚举/一张图进去，一个选项 + 置信度出来；契约见 `docs\DECIDER.md`）、`eyes.py`（两级眼睛接缝）、`vlm_pick.py`（tier1 挑号）、`tools\`（实验/评测）、`cases\`（素材+产物）、`evidence\`（真机取证图）——用法地图见 `docs\VLM_PROBE.md` | 活 |
| `vlm\` | **环境**：`.venv`（跑上面所有东西的 Python）+ `recipe\`（pyproject + uv.lock，重建用） | 活 |
| `models\qwen3vl-4b\` | **权重**：Qwen3-VL-4B 官方件（视觉档，零微调） | 活 |
| `scripts\` | 本机脚本：`fetch_ms_model.py`（从 ModelScope 拉一个模型仓，逐文件 sha256；**大分片外呼同机的 Flower**，见「重建环境」） | 活 |
| `docs\` | **本项目所有文档都在这一个目录**：`VLM_PROBE.md`（工具地图）、`DECIDER.md`（决策服务契约）、`VLM_DECISION_PROBE.md`（决策档全量实测）、
`BIXIAN_KEV_NOTES.md`（kev 文本后端）、`ARCHIVE_KEV_TEXT.md`（死档为什么在这/怎么复活）、`HANDOFF_VLM_ATHAND.md` | 活 |
| `repos\kev\` | **第三方** kev 快照（jaredpalmer，只剩 `docs\` + `skills\`，源码按用户指示已删） | 只读参考 |
| `client\` | **你自己的 Bi-Xian 客户端**（CLI + 策略 + 审计 + 评测，`bin\decide.py`）；`vlm_pick` 从它的 `src\` import `policy`（阈值口径的来源）—— 说明见 `docs\CLIENT.md` | 你的代码 |
| `archive\` | 死档：`kev_text.py`（kev 文本档起停/自检三合一：`start` / `stop` / `selftest`，纯 Python 无 shell）+ `make_run_dir.py`（生成运行副本）—— 后端已废，复活步骤见 `docs\ARCHIVE_KEV_TEXT.md` | 死档 |

**本项目自己的文档只有两处**：本 `README.md`（入口）+ `docs\`（含客户端的说明 `docs\CLIENT.md`）。
客户端不再自带 `README`/`LICENSE`/`.gitignore` —— 那些是**外层这一层**的：`README.md`、`LICENSE`、`.gitignore`。
全树唯一的第三方 `README` 在 `repos\kev\`（clone 自带的原件，别删）。

**按「要问几次」选形状。** `vlm_pick` / `eyes`（tier1）是「调用时加载权重」的一次性进程：一次问完
全程实测 **56.5 s**（加载 43.8 s + 前向 9.8 s + import），适合单次调试与评测。`decider.py serve` 是
**常驻**形状：权重加载一次（43.0 s 冷 / 28.1 s 热盘缓存），之后一次 `/decide` **约 1.0–1.6 s**
（前向 0.97–1.39 s；**同一个实例最开头几次会慢得多，10–20 s**）—— 给别的程序按需调用，athand 的
`--intent` 走这条；它闲置 30 分钟会自己退出，把 5.8 GiB 显存还回去（`--idle 0` 可关）。
kev 那套 8009 常驻后端连同它的权重已经删了，别和它混。

## 怎么跑

```powershell
$py = "vlm\.venv\Scripts\python.exe"                       # 从本目录（bixian\）出发
# tier1 一次挑号：listing + 意图 -> athand 编号 或 UNDECIDED（exit 0=YES / 2=UNDECIDED）
& $py vlm-probe\vlm_pick.py --listing vlm-probe\cases\fixture-som.json --intent "点击发送按钮" --k 9
# 两级接缝：小眼睛答不出（exit 2）就把 som 图交给我（大眼睛）
& $py vlm-probe\eyes.py --listing vlm-probe\cases\fixture-som.json --intent "点击发送按钮"
# 决策服务（给别的程序用，不认识调用方）：权重在不在这台机器上 / 常驻一次 / 起停
& $py vlm-probe\decider.py check
& $py vlm-probe\decider.py serve --port 8111        # 常驻：GET /health、POST /decide、POST /shutdown
& $py vlm-probe\decider.py ask --request req.json   # 一次性（每次都付全量加载）
```

`--listing` 里的 `frame` 是相对名时，先在 listing 同目录找（所以带 `cases\` 前缀的 listing 直接能用）。

## 重建环境

环境配方在 `vlm\recipe\`（pyproject.toml + uv.lock）→ `uv sync --extra serve`。
权重用 `scripts\fetch_ms_model.py`（只用标准库，系统 Python 3.13 就能跑）：

```powershell
python scripts\fetch_ms_model.py Qwen/Qwen3-VL-4B-Instruct -d models\qwen3vl-4b
```

**零外部依赖**：只用标准库。大分片（≥10 MiB）由脚本自己开若干 ranged 连接（`--streams`，默认 **8**），
进度落在 `<文件>.part` + `<文件>.part.json` 旁边 —— 断网 / Ctrl-C / 崩了，重跑**接着下**，不从头来。

实测基线（2026-09-23，ModelScope 直连）：每连接约 **0.1 MiB/s**，所以聚合速度差不多就是连接数 ×0.1；
**16 条连接会被服务端打断**（942 MiB 分片跑到 333 MiB 时多个 range 零推进），8 条稳。
嫌慢也可以直接拿本机另一个项目 Flower 下同一个链接（`~\Desktop\myProject\Flower`，
它就是为这件事写的）—— 本项目不依赖它，脚本里也没有它。

装 torch 必须 cu128（PyPI 的 Windows 轮子是 CPU 版），细节在 `docs\BIXIAN_KEV_NOTES.md`。

## 许可与忽略

MIT，见 `LICENSE`（原客户端自带的那份，现在归外层这一层）。第三方：`repos\kev` 是 Apache-2.0；
kev 后端权重（Apache-2.0 发布件）**不随仓分发**，它们被 `.gitignore` 排除，按 `docs\ARCHIVE_KEV_TEXT.md` 重下。

`.gitignore` 也归外层：忽略 `vlm/.venv/`、`models/`、`repos/`、下载中的 `*.part` / `*.part.json`、
`vlm-probe/cases/*.som.png` 这类每次跑都会重新生成的图，以及 **`vlm-probe/evidence/`**。

**真机取证件不随仓分发**：`evidence/` 里是真实窗口的截图与 listing（聊天内容、联系人、桌面布局），
`cases\bare.png` / `som.png` / `som_annotated.png` 也是真机窗口图。它们留在这台机器上，
文档里说到"取证链"时指的是本机这份。夹具与合成素材（`fixture-*.json`、`d_*.png`、`.cases`、
`tools\make_desktops.py` 生成的一切）都随仓分发。

## 数字从哪来

`docs\VLM_DECISION_PROBE.md`：§五 4-bit / §六 接缝 / §七 分辨率 / §九 与 kev 文本档对照 / §十 真机一条龙。
历史 handoff 在 `%USERPROFILE%\Tools\handoffs\`（最新：`handoff-bixian-reorg-20260923.md`）。
