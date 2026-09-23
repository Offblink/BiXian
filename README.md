# BiXian（笔仙）

本地跑一个小模型，只回答一个问题：**这一堆候选里，该选哪一个？**

给它一份带编号的清单（也可以再给一张截图）和一句"我要干什么"，它回你一个编号，外加它有多少把握。
把握不够时它说 `UNDECIDED`，而不是硬猜。

## 它适合什么

- **给屏幕上的控件挑号。** 一个窗口里往往有几十个能点的东西，程序先把它们编号、并画出编号图，
  剩下的问题就是"该点哪个"。配合 [athand](https://github.com/Offblink/Athand)（Windows 桌面操作脚本）
  就是一条完整链路：截图 → 编号 → 在这里挑号 → athand 去点。模型不需要知道坐标，只需要挑编号。
- **其它窄决策**：yes/no、N 选 1、打分。客户端把阈值、审计和评测都做在里面，用法见 `docs/CLIENT.md`。

它不做通用对话，也不做任务规划：一次一个问题，答不上来就把判断交回给你（或者交给更大的模型）。
原因很实在 —— 在"点错一行就是数据事故"的场景里，**它肯说不确定**比命中率高更重要。
所以阈值没过时它不会给你一个"最像的"，而是带着它看过的图退回来。

## 两种跑法：问一次，或者常驻

| | 什么时候用 | 代价（RTX 5070 Ti Laptop 12 GB，4-bit，1536 长边） |
|---|---|---|
| 一次性：`vlm_pick.py`、`eyes.py`、`decider.py ask` | 调试、评测、只问一次 | 首次约 56 秒（装权重约 44 秒 + 一次前向约 10 秒）；权重进了系统缓存之后约 15 秒 |
| 常驻：`decider.py serve` | 会被反复问（例如每个动作都问一次） | 权重只装一次（冷启 43 秒 / 硬盘缓存热 28 秒），之后每次约 1.0~1.6 秒 |

选择标准很简单：**要问两次以上，就用常驻**。常驻的服务闲置 30 分钟会自己退出，把显存还回去
（`--idle 0` 可以关掉这个行为）。跑起来后显存占用约 5.8 GiB。

两种跑法背后是同一个模型、同一份阈值配置；阈值只有一处来源：`client/src/bixian/policy.py`。

## 装

1. Python 3.13（系统自带的就行）与 [uv](https://docs.astral.sh/uv/)。
2. 环境（`pyproject.toml` + `uv.lock` 在 `vlm/recipe/`）：

   ```powershell
   uv sync --extra serve          # 在 vlm/recipe/ 里执行
   ```

3. 权重（8 GB 级，不随仓库分发，用脚本自己拉）：

   ```powershell
   python scripts/fetch_ms_model.py Qwen/Qwen3-VL-4B-Instruct -d models/qwen3vl-4b
   ```

   脚本只用标准库：大分片自己开 8 条连接，进度写在 `<文件>.part` 旁边，断了重跑接着下，
   并且逐个文件核对 sha256。

4. **torch 要用 cu128 的轮子**。PyPI 上 Windows 的 `torch` 是 CPU 版，装上会跑成"模型很弱"的样子；
   换法见 `docs/BIXIAN_KEV_NOTES.md`。

## 用

解释器用 `.venv` 里那个，命令在仓库根目录执行。仓库自带三张**合成桌面**（`vlm-probe/cases/d_*.png`
与同名 `.json` 清单），下面的例子可以直接跑：

```powershell
$py = "vlm\.venv\Scripts\python.exe"

# 挑一个编号：给一份候选清单 + 一句"要干什么"
& $py vlm-probe\vlm_pick.py --listing vlm-probe\cases\d_notepad.json --intent "save this document to disk"
#   picked athand #36 Save  p=1.0000  confidence=1.000  threshold=0.50 (default) -> YES

# 两级接缝：小模型答不出时，把它看过的那张编号图交上来，由更大的模型决定
& $py vlm-probe\eyes.py --listing vlm-probe\cases\d_notepad.json --intent "save this document to disk"

# 决策服务：先看权重在不在，再决定常驻还是一次性
& $py vlm-probe\decider.py check
& $py vlm-probe\decider.py serve --port 8111
& $py vlm-probe\decider.py ask --request req.json
```

两句实战经验：

- **意图要用候选名字的同一种语言写**（清单是英文，意图就写英文）。实测跨语言会明显掉把握：
  同一道题，同语言能到 0.9 以上，跨语言常常掉到 0.2 附近，然后被阈值拦成 `UNDECIDED`。
- **它读的是图**，不是文字列表：清单里的 `frame` 指向一张截图，模型看的就是它，编号也是画在它上面的。
  `docs/` 里有一部分例子引用了未随仓库分发的截图，把 `--frame` 换成你自己的截图即可（清单格式见
  `docs/DECIDER.md`）。

`vlm-probe/cases/d_*.cases` 是这三张合成桌面的问句集（一行一句，`问句=期望的编号`），
可以照着它写自己的评测问题。

结果怎么读：

| 退出码 | 含义 |
|---|---|
| `0` | 有答案：它挑了一个编号，并给出把握（`p` 与 `confidence`） |
| `2` | `UNDECIDED`：把握不够，它不猜。屏幕/文件上会留下它看过的那张编号图，你可以自己看，或换更大的模型 |
| `3` | 两级模式下的"交上去"：`eyes.py` 把小模型看过的图和选项表一起交给你 |

给别的程序用（不限于 athand）走常驻服务的 HTTP 接口：`GET /health`、`POST /decide`、`POST /shutdown`。
请求与响应的完整定义 —— 一份枚举（和可选的一张图）进去，一个选项加置信度出来 —— 在 `docs/DECIDER.md`。

## 仓库结构

| 目录 | 是什么 |
|---|---|
| `vlm-probe/` | 工具本体：`decider.py`（决策服务，接口见 `docs/DECIDER.md`）、`eyes.py`（两级接缝）、`vlm_pick.py`（从候选清单挑号）、`policies/`（阈值覆盖）、`tools/`（实验与评测）、`cases/`（素材与产物） |
| `client/` | 客户端：命令行、策略、审计与评测，入口 `client/bin/decide.py`；阈值口径在这里（`client/src/bixian/policy.py`） |
| `vlm/recipe/` | 环境配方（`pyproject.toml` + `uv.lock`） |
| `scripts/` | `fetch_ms_model.py`：从 ModelScope 拉权重并逐文件校验 |
| `models/` | 权重放这里（不随仓库分发） |
| `docs/` | 全部文档，见下 |
| `archive/` | 已停用的旧文本后端（kev），保留是给需要复活它的人 |
| `repos/` | 第三方只读快照（kev，Apache-2.0），需要时自己 clone |

## 文档

- `docs/VLM_PROBE.md` —— 工具地图，从哪开始看
- `docs/DECIDER.md` —— 决策服务的接口契约
- `docs/VLM_DECISION_PROBE.md` —— 全部实测数据：量化、分辨率、准确率、与旧后端的对照
- `docs/CLIENT.md` —— 客户端用法（窄决策的调用方式与阈值策略）
- `docs/BIXIAN_KEV_NOTES.md`、`docs/ARCHIVE_KEV_TEXT.md` —— 旧文本后端（含复活步骤），除非要动它，可以不看

## 许可

MIT，见 `LICENSE`。第三方只有 `repos/` 里的 kev 快照（Apache-2.0）；模型权重是官方发布件，
同样的许可，但体积太大，不随仓库分发。
