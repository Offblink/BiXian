# vlm-probe — 用本地 VLM 从 athand 的候选里挑一个编号

athand 的 `targets` 给出候选表（`n/name/cls/rect/source`），这里把「意图 + 列表（+ 截图）」变成
**一个 athand 编号**，或明确说 `UNDECIDED`。设计、实测数字、坑都在上一层的
`VLM_DECISION_PROBE.md`；两级升级（小眼睛 bixian → 大眼睛）见 `eyes.py`。

## 布局

```
vlm-probe\
  vlm_pick.py     ★ 现役 tier1：listing + intent (+frame) -> 编号 / UNDECIDED（只打印，不动作）
  eyes.py         ★ 现役两级接缝：小眼睛答，不自信就把 som 图交上来（exit 0 / 2 / 3）
  decider.py      ★ 决策服务：**不认识任何调用方**（枚举/图 -> 选项 + 置信度），给别的程序按需调用
                  （契约、实测、三个命令见 `DECIDER.md`；athand 的 `--intent` 走这条）
  policies\       策略覆盖（demo-big-eyes.json = 演示"调用点抬门槛"）
  cases\          素材与产物：裸图/编号图(.som.png)/athand 形状的 listing(.json)/问题集(.cases)
  tools\          实验与评测脚本（一次性；一次性就是它们该待的地方）
  evidence\       真机取证截图（desktop→notepad→typed→dropped→sent 一条链）
  README.md       这份地图
```

## 跑法

解释器用 `..\vlm\.venv\Scripts\python.exe`（torch 2.8+cu128 / transformers 5.17 / bnb 0.50.2；
**别用 harness 的 python，它的 torch 是坏的**）。命令在 **vlm-probe 根**下执行：

```powershell
$py = "...\bixian\vlm\.venv\Scripts\python.exe"
# 单次：--expect 是 athand 号（不是 our #i）
& $py vlm_pick.py --listing cases\fixture-som.json --intent "点击发送按钮" --expect 12
# 热循环：权重只加载一次（加载 ~13 s 比一次前向贵），stdin 每行一个 json 请求
'{"listing":"cases\\fixture-som.json","intent":"点击发送按钮","frame":"cases\\bare.png"}' | & $py vlm_pick.py --loop
# 两级
& $py eyes.py --listing cases\fixture-som.json --frame cases\bare.png --intent "点击发送按钮"
# 决策服务（要问两次以上就走这条；常驻一次，之后一次 /decide 约 1-2 s）
& $py decider.py check                                  # 权重在不在这台机器上（0.2 s，不 import torch）
& $py decider.py serve --port 8111 --idle 1800          # GET /health、POST /decide、POST /shutdown
```

`tools\` 里的脚本（评测/一次性实验）**从同一个根跑**，路径都锚在脚本自身位置，所以换 cwd 也不炸：

```powershell
& $py tools\make_desktops.py                 # 重生成 cases\d_*.{png,json,som.png,cases}（重跑字节一致）
& $py tools\vlm_battery.py --model ..\models\qwen3vl-4b --edge 1024 --4bit --suites "som.png:som.cases"
& $py tools\matrix.py --model ..\models\qwen3vl-4b --config both --tag 4b   # 模型×分辨率矩阵
& $py tools\run_multi_1024_4bit.py           # 四个 launcher：把中文问题集写进源码，绕开 cmd.exe 拆词
```

## 两条约定（改代码前先读）

- **suite 里的路径相对 `cases\`**（`vlm_battery.py: resolve()`）：`--suites "som.png:som.cases"` 指
  `cases\som.png` 与 `cases\som.cases`。绝对路径原样透传。
- **`cases\` 是唯一的数据目录**：素材（裸图、listing、`.cases` 问题集）与产物（`.som.png` 编号图）
  都在这儿 —— 生成器 `make_desktops.py`/`make_som.py` 知道往这儿写，脚本里不再出现数据文件名。

## 数字在哪

- 分辨率 × 4-bit/bf16 的矩阵、峰值显存、失败模式（高置信错）→ `VLM_DECISION_PROBE.md`
  （§五 4-bit、§六 接缝、§七 分辨率、§八 与 kev 文本档的对照）。
- 4B 4-bit @1536 的日常口径：前向 1.0–1.3 s、峰值 5.82 GiB、37 问 36 对。
- `tools\rank.py` 汇总 `C:\tmp\scratch\athand-bixian\*.json`（matrix 的落盘）成跨模型排行。
