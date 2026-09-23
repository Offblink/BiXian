# decider —— 决策服务的契约

**一个枚举（可选地配一张图）进去，一个选项 + 置信度出来，别的什么都没有。**

`vlm-probe\decider.py`。它是这条接缝的 **provider 那一半**：它不知道谁在调它，也不认识任何调用方的
listing 格式、窗口、坐标系或目录布局。调用方是谁都行 —— 桌面驱动、表单填写、测试台 —— 都用同样的
三个命令。

对比一下同目录另外两件（别搞混）：

| | 认识调用方吗 | 什么时候用 |
|---|---|---|
| `vlm_pick.py` | **认识 athand 的 listing**（`source`、窗口原点、`plausible` 预过滤） | 两级眼睛的 tier1 本体；agent 手动挑号 |
| `eyes.py` | 是 `vlm_pick` 的上层：UNDECIDED 就 exit 3 把 som 图交上去 | 大模型自己看图的流程 |
| **`decider.py`（本文）** | **什么都不认识**，只要一个枚举 +（可选）一张图 | 别的程序按需调用（例如 athand 的 `--intent`） |

三者共用同一份模型代码和同一个阈值口径：`decider.py` 直接 import `vlm_pick` 的
`Reader` / `annotate` / `decide`，而 policy 的唯一来源是 `client\src\bixian\policy.py`。

## 契约 v1

请求（一个 JSON 对象）：

```json
{"intent": "点击发送按钮",
 "options": [{"id": 12, "label": "发送", "box": [300, 60, 380, 96]}],
 "image": "C:\\...\\frame.png"}
```

| 字段 | 说明 |
|---|---|
| `intent` | 要干什么，用**选项自己的语言**写（中文界面写中文："点击发送按钮"准，"把内容发出去"歧义） |
| `options[].id` | 调用方自己的编号。**原样回来**，两边不需要商量编号怎么排 |
| `options[].label` | 选项的文字，可以是空（图画上的纯形状按钮就是这样） |
| `options[].box` | **图片坐标**里的框；给了 `image` 就必须每个选项都带 |
| `image` | PNG 路径。**可以不给**：那就只用 label 文字问 |

上限 **9 个选项**（读数是最后位置的受限 softmax，槽位是 `"1".."9"` 九个单 token）。超过 9 个是
**400 报错**，不是这边替你挑 —— 哪些候选值得问是调用方对自己世界的判断。

响应：

```json
{"contract": 1, "decision": "YES", "id": 12, "index": 4,
 "p": 1.0, "confidence": 1.0, "threshold": 0.5, "policy": "default",
 "options": [{"index": 4, "id": 12, "label": "发送", "p": 1.0}, ...],
 "marked": "...\\frame.marked.png", "ms": 10441.0, "diag": {"ms": 9847.0, "vis_tok": 1440, ...}}
```

- `decision` 只可能是 `YES` / `UNDECIDED`。**`UNDECIDED` 是交接不是答案**：peak 没过 policy 阈值时，
  这边不吐一个"最像的"给调用方 —— 调用方要么自己看 `marked`（**模型真正看的那张图**，编号就是它给的
  1..K），要么换更大的模型。"不确定安不安全"和"不安全"是两句话（见 `client\src\bixian\policy.py`）。
- `marked` 是**每张图都会写的**，路径 = `<image>.marked.png`：给两级流程用 —— 大眼睛必须看到**同一张、
  同一套编号**的图，答案才映射得回去。
- 画在图上的号码是**它自己的 1..K**，不是调用方的 `id`（真实界面的编号能到几十，`27` 是两个 token）。
- `options` 是完整排名（第一行是它挑的），只给"要让别人复核"的场景用；落地只需要 `id`/`p`/`confidence`。
- 请求本身有问题（缺 intent、选项超 9、给了图却没给框、图不存在）：**400 + `{"error": "..."}`**，
  不是 500，也不是静默返回一个数。

## 三个命令（同一份契约）

| 命令 | 干什么 | 成本（本机实测） |
|---|---|---|
| `decider.py check` | 权重在不在这台机器上 —— **调用方自动检测的开关** | **0.21 s**，不 import torch |
| `decider.py ask --request req.json`（`-` = stdin） | 一次性请求，调试/脚本/对方不能常驻时用 | 一次全量加载（实测全程 **56.5 s**） |
| `decider.py serve --port 8111` | 常驻：权重加载一次，HTTP 在 **127.0.0.1** | `GET /health`、`POST /decide`、`POST /shutdown` |

`serve` 是"要问两次以上"唯一合理的形状：加载 43 s、一次前向 1–20 s（看输入），一次性进程会把 98% 的
命花在装权重上。

```powershell
$py = "vlm\.venv\Scripts\python.exe"
& $py vlm-probe\decider.py serve --port 8111 --idle 1800
curl.exe -s http://127.0.0.1:8111/health
```

- `--idle`（默认 1800 s）：这么久没人问就自己退出，把 5.8 GiB 显存还回去（12 GB 的卡上这是礼貌）；
  `--idle 0` 永不退。`POST /shutdown` 是给调用方收显存用的（loopback-only，没有鉴权 —— 它是本机工具，
  不是服务）。
- `/health` 在模型**正在加载**时也会立刻回答（`loading: true`），所以"在装权重"和"没起来"分得开；
  权重缺失时它照常启动并报 `model_present: false` —— 那正是调用方要读的那一格。
- `serve` 会让 `/decide` 阻塞到加载完成；调用方该给一个覆盖加载的 timeout（athand 那边默认等 300 s）。

## 本机实测（2026-09-23）

输入：`cases\fixture-som.json` 的 9 个候选 + `cases\bare.png`（**2240×1400** 的真机窗口图，不随仓分发
→ 1440 视觉 token、1578 输入 token、峰值 **5.81 GiB**），意图 `点击发送按钮`，答案两侧都是 **`#12 发送`**。

| 项 | 数字 |
|---|---|
| `check` | **0.21 s**（不 import torch） |
| `serve` 冷启动到 `loaded` | **43.0 s**（首次）/ **28.1 s**（盘缓存热）；此前还有 Python+torch import |
| `/decide` 稳态 | **1.06 / 1.07 / 1.56 s** 墙钟（payload 1.02/1.02/1.50；**模型前向 0.97/0.97/1.39 s**） |
| 同一实例最开头三次 | 10.4 / 18.5 / 20.7 s —— **别用它判断这条路的成本**，冷启动的头几次包含首次前向的摸索 |
| `ask` 一次性全过程 | **56.5 s**（加载 43.8 s + 前向 9.8 s + 解释器/torch import） |
| 与 `vlm_pick` 同题同素材对照 | 两边都 `#12 发送 p=1.0`（`--expect 12` HIT） |
| 只给枚举不给图的纯文字问 | 有答案（`#27`，p=0.998）**但和带图的答案不同** → 精度未测，接线可用 |
| 非法请求 | 400 + `{"error": ...}` |

合成评测台（`tools\make_desktops.py` 出的图）在 4-bit @1536 是 **1068 ms**（`VLM_DECISION_PROBE.md` §七，
34 问），和这里的稳态一致；所以评估门槛可以照用，但**上机第一分钟别用来量成本**。

## 谁在用

athand（`skills/athand/athand.py`）的 `--intent` 走这条：它自己把候选筛成 ≤9 个、按调用方的编号
发过来，拿回 `id` 再落地成一次点击。athand 那边不 import 任何模型代码，配置里只有 url + 权重路径；
这边不认识 athand —— 两边唯一的共同语言就是本文的契约。见 athand 仓库
`skills/athand/NOTES.md` 的「The decider seam」。
