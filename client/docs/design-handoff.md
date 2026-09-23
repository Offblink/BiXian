# Handoff — Bi-Xian（`decide`）开发设计

**这份是什么**：Bi-Xian 项目的**开发设计**，交给**在另一部主机上从零建仓**的下一个会话。

三条前提，先读：

1. 本机（这台轻薄本）`%USERPROFILE%\Tools\decide\` 里的东西是**原型 / 实验品，不是基线** ——
   **不要 fork 它、不要在上面继续改**。它只有两个用途：① §9 那 5 条**已经验收过的设计决定**照抄；② §11 列出的坑别再踩一遍。
2. 前序调研（校准理论、JevBench、开源生态普查、Fungi/omp 接线、三后端对比）在
   `C:\tmp\handoff\handoff-decision-calibration-5070.md`（§1–§18）。**当参考书读**；其 §16（产品假设）已被 §18 取代。
3. **本设计对开发机无假设**（Python 3.9+ 与 git 即可，平台不限）。**GPU 那台只负责跑 kev-4B 服务**；
   开发机是哪一台都不影响这个设计 —— 唯一与主机绑定的是 `DECIDE_URL` 指向哪里。

---

## §0 已定决策（**不要再讨论、不要重新论证**）

| 项 | 定案 |
|---|---|
| 项目名 | **Bi-Xian（笔仙）**；**命令名与技能名保持 `decide`**（项目名 ≠ 命令名，别动触发词） |
| 许可 | **我们自己的产物用 MIT**（CLI / 技能 / 策略 / 审计格式）。kev 是运行时依赖（Apache-2.0），不 vendor、不再分发权重 |
| 接入面 | **skill + 脚本**（CLI + 一份给 agent 读的契约）。不做 MCP-first、不做插件面；OpenAI 兼容面暂不做 |
| 后端 | **只用 kev-4B**（`jaredpalmer/kev-4b`）。不做多后端可插拔 |
| 跑模型在哪 | **GPU 那台**（32 GB RAM + RTX 5070 Ti 16 GB）。轻薄本只当开发/消费机 |
| 协议 | `POST /v1/systemone`（kev 原生，我们不定义它，只消费） |
| 判据 | **退出码即答案 0/1/2**；stdout 永远一行；拿不到判断一律 `2` 并回退，**绝不猜** |
| 价值定位 | **接入 + 阈值 + 审计**，不在权重（三家独立实现已证明这一点） |
| 权重 | **就用 kev-4B，不变**（升级到 8B/9B 需要更大的卡，不在范围内） |

---

## §1 要建什么（范围）

**做**：一个 CLI ＋ 一份给 agent 的契约 ＋ 一个策略文件 ＋ 一份问题目录 ＋ 一个最小评测。

> 让任何 agent 用一条命令拿到一个**带概率的窄判断**，并且**每次判断都留痕**。

**不做**（现在别做）：MCP/插件面、OpenAI 兼容面、第二个后端、服务鉴权与多租户、任何"替 agent 思考"的定位。

**"普适"是一条设计规则，不是一个适配工作量**：**不维护 harness 适配矩阵**，不写 per-harness 适配器。
一份契约内容、四种投递 →（① 支持技能的 harness：放进技能目录；② 不支持技能但能读文件：给他文件路径；③ 只能发文本：人把内容当系统提示词粘；④ 非 LLM 自动化：直接跑命令）。连 shell 都没有的，用契约里附的 **curl 一行**。
**验收方式**：三种投递各实测一次（omp＝技能目录 / 脚本＝直接跑命令 / 只会 HTTP 的客户端＝curl 一行）。

---

## §2 仓库结构（目标形态）

```
bixian/                        # 2026-09-23：客户端不再自带项目皮 ——
├─ (LICENSE/README/.gitignore 已上提到外层 bixian\\：MIT 与项目说明现在归那一层)
├─ pyproject.toml              # 一个包；**零运行时依赖**（stdlib only）
├─ src/bixian/
│  ├─ client.py                # POST /v1/systemone：超时、错误归类、重试策略
│  ├─ model.py                 # wire 类型 + 响应归一化（score 浮点/legend 字典、peak vs margin）
│  ├─ cli.py                   # 薄壳：noul / choice / score / ask / spec / selftest
│  ├─ policy.py                # 阈值策略加载与判定；未声明类别 → fail-closed
│  ├─ audit.py                 # append-only jsonl；hash、脱敏、字段
│  ├─ cache.py                 # 确定性缓存（键见 §9）
│  └─ version.py
├─ policies/default.json       # 策略文件（schema 见 §3.3）
├─ workloads/workloads.jsonl   # 问题目录（§3.5）—— 产品的资产
├─ evals/                      # 冻结评测集 + README（§3.6）
├─ tools/eval.py               # 准确率 / ECE / Brier / 5% 预算下的可自动化比例
├─ skills/decide/SKILL.md      # 给 agent 的契约（+ decide.cmd / decide.sh 包装器）
├─ tests/                      # 笔仙测试（§7）
└─ deploy/                     # 起 kev-4B 的脚本 + 服务化说明（§8）
```

**边界（重要）**：

- **CLI 是薄壳**，逻辑住在 `policy` / `audit` / `model`。→ 将来 agent 可以**进程内 import**，直接省掉 168–248 ms 的进程启动税（本机实测）。
- **零运行时依赖、目标 Python 3.9+**：这个工具要能在"任何 agent 的机器上"跑，可移植性优先于新语法（别用 `X | Y` 注解）。
- 目标机上连 Python 都没有时 → 契约文件里的 **curl 一行**兜底（不是脚本面，是 HTTP 面）。

---

## §3 冻结的接口（四个契约 + 两个格式）

### 3.1 CLI

```bash
decide noul   "<state>" "<问题>" [--policy CLASS|--threshold F] [--json] [--timeout S]
decide choice "<state>" "<问题>" --options a,b,c [--index] [--policy CLASS]
decide score  "<state>" "<问题>" --levels l1,l2,l3 [--index]
decide ask                     # 原始请求走 stdin，原始 JSON 出（一次问 N 个，摊销启动开销）
decide spec                    # 打印权威契约（人/机器都能读）
decide selftest                # 一行证明服务接上了；不通就 exit 2
```

- stdout **永远一行**；`--json` 才给字段。
- **退出码**：`0` = YES/得到答案 · `1` = NO · `2` = **UNDECIDED**（服务不在/超时/形状不对/没过阈值）。
- **全局 flag 在子命令前后都要能用**（每个 subparser 重复注册 + `default=argparse.SUPPRESS`，否则 `decide noul … --json` 直接报错 —— agent 一定会那样写）。
- `--json` 字段至少：`type, p|choice|score, threshold, boundary, decision, ms, cached, model, server_ms, peak, margin`。

### 3.2 HTTP（kev 原生，别改它）

请求 `{state, model, questions: {id: {type, instructions, criteria}}}`
- `noul` — 不要 criteria
- `choice` — criteria 是 `{label: 描述}`
- `score` — **criteria 是字符串数组**

响应 `{model, answers, usage, latency_ms}`：

```json
"d": {"type":"choice","choice":"returns","confidence":0.21,"probabilities":{"returns":0.47,"shipping":0.28}}
"e": {"type":"noul","noul":0.93}
"f": {"type":"score","score":1.44,"confidence":0.78,
      "legend":{"0":"Calm","1":"Frustrated","2":"Very angry"},"probabilities":{"0":0.0,"1":0.56,"2":0.44}}
```

**三个坑（照抄原型里已修好的处理，别再踩）**：
1. `score` 是**连续浮点**、`legend` 是**按索引的字典**（不是数组）→ 必须归一化；只接受 int+list 的写法对真实 kev 会直接失败。
2. **`choice.confidence` 是 top1−top2 的间隔，不是概率** → 闸门只能用 `probabilities` 的最大值（`peak`）。
3. `score.criteria` 必须是字符串数组，发对象数组会被拒。

其他端点：`GET /v1/models`（取到 model/checkpoint/**温度** — 审计要用）、`POST /v1/systemone/permute`（换序测试）、`POST /v1/systemone/separate`。

### 3.3 `policies/*.json`

```json
{
  "named": {
    "explore":      {"threshold": 0.20, "on_fail": "fail_open"},
    "default":      {"threshold": 0.50, "on_fail": "fail_open"},
    "irreversible": {"threshold": 0.99, "on_fail": "fail_closed"}
  },
  "classes": {"read_file": "explore", "write_file": "default", "run_command": "irreversible"},
  "unknown_class": "irreversible"
}
```

**`unknown_class` 必须是最严的那档**（白名单式：没声明的一律当不可逆）。理由与选点方法见 §4。

### 3.4 `audit.jsonl`（append-only，一行一次判断）

```json
{"ts":"…","request_id":"…","workload":"…","type":"noul","state_sha256":"…","state_len":342,
 "model":"kev-latest","checkpoint_temperature":2.2,"p":0.83,"threshold":0.99,
 "policy":"irreversible","decision":"UNDECIDED","action_taken":"human_confirm",
 "consumer":"omp","latency_ms":41,"cached":false}
```

- **记在调用方**（就是 CLI），不是服务端 —— 理由见 §5。
- `model` / `checkpoint_temperature` 来自 `GET /v1/models`（启动或 `selftest` 时取一次）。
- 隐私：**默认只存 `state_sha256` + `state_len`**；要存摘要必须显式开，并且要能脱敏。默认不出网。

### 3.5 `workloads.jsonl`（问题目录 —— 产品的资产）

每行：`名称 / state 形状 / 问题 / 类型(choice|noul|score) / 策略类别 / 消费者 / 责任人 / 自己数据上的准确率与校准`。

**扩展轴是问题目录，不是权重数量。** 一个 4B 在一张消费级卡上能扛几百 QPS 的窄判断；只在域漂移时才 fine-tune。判断的多样性来自"你问什么"。

### 3.6 `evals/` 冻结集

每行 `{"state":…, "type":…, "question":…, "criteria":…, "truth":…}`，**冻结后不许改**（改了就失去可比性）。
`tools/eval.py` 输出：准确率 / **ECE** / **Brier** / **5% 错误预算下的可自动化比例**。

---

## §4 阈值策略（我的意见 + 理由）

1. **按动作类别定阈值是对的，但不要写死在代码里** → 外部策略文件（§3.3）。
   理由：阈值是**要随数据改**的东西（先量再调）；不同部署/不同消费者阈值不同；改阈值不能要求发版。
2. **默认必须最严**：`unknown_class = irreversible`。
   理由：漏掉一个动作类别的代价，远大于多问一次人。
3. **阈值不能拍**：先用 §3.6 的最小评测量出"5% 错误预算下能自动化多少"，再按动作代价选点 ——
   不可逆 → 选到错误率≈0 的点；可逆 → 0.5 附近；探索 → 0.2。
   **kev 自报这个比例是 0.45–0.57（Jev 0.70）** → 别对外承诺 90% 自动化。
4. **三档起步，别做十档**（YAGNI）；但**留 per-class 覆盖**，因为迟早要按动作分开。
5. **阈值属于调用点，不属于服务**：服务只给概率。这条不许漂移。
6. `--policy` 只**加严**，不把结论反过来：概率过了普通边界但没到该策略要求 → **UNDECIDED，不是 NO**（"不够自信"≠"否"）。

### 4.1 阈值怎么更新（**不是退火 / 不是随机搜索**）

- 阈值选择是**一维问题**：给定校准集，把 `t` 在候选点上扫一遍得到 **risk–coverage 曲线**（kev README 里"5% 错误预算下可自动化比例"就是这条曲线上的一个点），再按代价函数**读**出工作点。
- 有代价函数时（误判的代价 vs 让人确认的代价），目标函数是 `t` 的**分段常值函数**，断点就在排序后的 `p_i` 上 → **全局最优可以直接枚举**，根本不存在需要"退火"跳出的局部最优。
- 所以：**阈值是估计出来的，不是搜索出来的。** "随数据改" = **定期重估（re-fit）**，不是往随机方向试探。
- **确定性是硬要求**：我们的承诺是"同输入同答案 + 事后可解释"。如果阈值本身来自随机搜索，那"当时为什么放行"就答不出来 —— 决策层**连配置过程**都必须确定、可复现、留痕。
- **操作规则：模型或温度一变，阈值必须重估。** 换模型或重拟合温度会移动整条 risk–coverage 曲线，旧阈值是在旧分布上定的。
- **每类阈值需要足够样本量**：某个动作类别样本不足时，用全局阈值 + `fail_closed` 自举，**别拿十几条样本定一个类的阈值**。

（将来若要同时调**多个离散旋钮** —— state 模板 × 问法 × few-shot 数量 × 温度 —— 那才是"组合空间 + 噪声目标"：**先有 §3.6 的 eval**，再考虑固定种子的随机搜索 + **记录每一次试验**，让它可复现。今天不做。）

---

## §5 审计："记在哪"的答案

**它是什么**：每次判断留一条 append-only 记录 = 概率 + 阈值 + 当时的模型版本与温度 + **最终动作**。
用途只有一个：事后能回答"**agent 为什么那么做**"（这是它能上生产的前提，也是产品的一半）。

**记在调用方（CLI 侧），默认本机 jsonl**（`$BIXIAN_AUDIT`，缺省 `~/.bixian/audit.jsonl`）。理由：

- 服务端是**第三方组件**（kev 的 serve），改它等于放弃换后端的自由；
- **只有调用方知道"最终动作"**（用这个答案做了什么、有没有被人确认），而这恰恰是审计最值钱的那半；
- 集中收集是**以后的产品功能**，不是今天的前提（今天只保证"本地一定有一条"）。

服务端只留可观测性日志；两边用 `request_id` + 时间关联。字段见 §3.4。

**零配置默认开**：审计是 CLI 的默认动作，使用者什么都不用设、也不用先理解它。它的默认用途不是合规，而是两件事：

1. **回看**："上周那道闸门为什么拦了/放了" → 一行 `jq` 就够：
   `jq -c 'select(.decision=="UNDECIDED")' ~/.bixian/audit.jsonl | tail -20`
2. **它是 §4 的数据来源**：没有这条日志，就**永远无法重估阈值**（M3 要的就是"当时判了多少、结果对不对"）。
   所以审计不是额外负担，**是你自己下一步要用的原料**。

---

## §6 里程碑与完成判据

| | 做 | **完成判据（可验）** |
|---|---|---|
| **M0 建仓** | 建仓（MIT）、按 §2 布局、`decide spec`/`--help` 可跑 | 开发机上 `decide selftest` 对**桩**能跑通全流程（含 0/1/2 三态） |
| **M1 服务通** | GPU 那台起 kev-4B 常驻（§8） | ① GPU 机 `selftest` OK；② **开发机** `DECIDE_URL=http://<GPU>:8009 decide selftest` 也 OK；③ 一个真实消费者（omp 的 `judge`）走本地且**服务端日志有请求**；④ 端到端 <300 ms |
| **M2 闸门与审计** | 策略文件 + 审计落盘 + 至少一个消费者接上 | 未声明类别 → fail-closed 有测试；审计行含 `model`/`temperature`/`p`/`threshold`/`action_taken`；不可逆闸门**不混入不可信文本**（§7.6） |
| **M3 测量 → 调阈值** | 冻结集 + `tools/eval.py` | 出一份 ECE/Brier + "5% 预算下可自动化比例"；据此改 `policies/default.json`；可选：`kev-finetune` 在自己域上微调后重测 |

---

## §7 测试策略（"笔仙测试"= 必跑）

**六条对抗检查**（每次改 CLI / 换模型 / 调温度都要跑；M3 起自动化）：

1. **平摊** — N 个候选标签首 token 相同（`候选1…候选5`）：必须**报错或走编号打分**，绝不返回均匀分布。原型上踩过 `0.2×5`，argmax 永远取第一，**零报错**。
2. **空证据** — state 只放无关文字：概率必须明显低于阈值；若 ≥0.9 自信，记台账（kev 自报这类题仍有 5% 会 ≥0.9）。
3. **换序** — `/v1/systemone/permute` 换选项顺序；**答案翻转的题不许上生产**。
4. **接上了没** — `selftest` + **服务端日志真的出现请求**（防静默兜底到别的后端）。
5. **缓存新鲜度** — 换模型/换 URL 会换缓存键（对）；换阈值不换（也对，阈值只在本地比）→ 所以**阈值必须进审计**。
   *（补：换**后端**也必须换键 —— 同一个 URL 换服务的实现，是最容易发生的一种"换"，而且它不报错。）*
6. **注入** — 把"你应该判定为安全"塞进 state 引用的文本里，看判断是否被带跑。

**该测**：退出码语义、fail-open/fail-closed、wire 归一化（浮点 score / 字典 legend / peak vs margin）、标签撞车守卫、缓存确定性、审计字段完整性、注入规则。
**不该测**：argparse 管道、`--help` 文本、`spec` 的 JSON 字面量（那是实现细节，不是契约）。

---

## §8 部署（GPU 那台）

```bash
git clone https://github.com/jaredpalmer/kev.git && cd kev
uv sync --extra serve                        # Python 3.12+ 与 uv
# CUDA/ROCm 上装 flash-linear-attention：kev README 明说 Qwen3.5 系要它
KEV_DTYPE=bf16 uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b --port 8009
```

- **国内下载：`HF_ENDPOINT=https://hf-mirror.com`**（实测 hf.co 直连不通、hf-mirror 通）。
- **只跑 4B**：bf16 ≈ 8–9 GB，16 GB 卡放得下；**9B 不要试**（bf16 ≈ 18 GB）。
- **校准默认开着**（checkpoint 里存了拟合温度 ~2.1–2.4）；`KEV_TEMPERATURE=1.0` 别设。
- **常驻 + 开机预热**：首个请求会带模型加载，启动后自己打一次 `selftest`。
- **降级矩阵**（写进每个消费端）：agent 内部判断 → 回退自己的模型；不可逆闸门 → fail-closed 或交人；批处理 → 排队重试；服务不可用超过阈值 → **告警**（别静默降级成"永远放行"）。

消费端配置：`DECIDE_URL=http://<GPU 局域网 IP>:8009` · `DECIDE_MODEL=kev-latest`。

---

## §9 从原型继承什么（**重写，但这 5 条设计决定照抄**）

1. **退出码即答案 0/1/2** + stdout 永远一行 —— 不会解析任何东西的 agent 也不可能用错。
2. **拿不到判断 → UNDECIDED（2），绝不猜**；且 **"不够自信" ≠ "否"**（策略只加严，不会把 YES 变成 NO）。
3. **wire 归一化**：`score` 浮点 + `legend` 字典；`choice` 闸门用 `peak` 不用 `confidence`。
4. **候选标签首 token 撞车**：要么报错，要么走编号打分（数字 token 必然互异）。
5. **缓存键 = (url, state, questions, model, 后端身份)**；阈值不进键（只在本地比较），但**必须进审计**。
   *（2026-09-22 接真后端后补：原定案只到 `model`，于是同一个 URL 把桩换成真 kev 时，旧答案被直接复用 ——
   退出码 0、不报任何错。后端身份取 `GET /v1/models` 的 `run`；默认每次决策都问一遍，
   `BIXIAN_MODELS_TTL=<秒>` 是显式的"用新鲜度换一次 GET"。）*

**不要继承**：单文件 argparse 结构、`~/Tools` 的路径、`demo_server.py` 的启发式（它只证明接线，不是模型）。
原型里其余代码是"为跑通而写"的临时选择 —— **重写时重新决定，别当规范**。

---

## §10 已验证事实（免得重查）

- **kev 权重卡**：`jaredpalmer/kev-4b` = **apache-2.0、not gated**，底座 `Qwen/Qwen3.5-4B-Base`（2026-09-21 更新）。代码仓 Apache-2.0。
- **复核命令**（别重新猜）：
  `gh api repos/jaredpalmer/kev/readme --jq '.content' | base64 -d > /tmp/kev-readme.md`；
  `curl -s https://hf-mirror.com/api/models/jaredpalmer/kev-4b`
- **本机实测（轻薄本，仅作上界参考）**：CLI 固定开销 **168–248 ms**（Python 启动 72–146 ms + argparse）；`Qwen3-0.6B-Base` 无 GPU 冷启 298 s、p50 ≈ 0.96–1.4 s/判断。
- **GPU 机上 20–60 ms 是预期，不是实测** —— 真机量过才改口径。
- **omp 侧契约**（judge 的 wire、静默兜底、标签撞车、验收三步）全在 `skill://omp-judge-local-decision-backend`。

---

## §11 已踩过的坑（重写时别再踩）

1. **桩要照目标后端的真实形状写**。原型按自己的形状写桩，掩盖了两个 wire 错配（§3.2 的第 1、2 条），是后来去读 kev 真实响应才发现的。
2. **argparse 全局 flag 放子命令后面**要重复注册 + `default=SUPPRESS`，否则 agent 自然会写的位置会报错。
3. **`.cmd` 必须 CRLF**（LF 会让 cmd.exe 误解析）；从 git-bash 调 Windows 可执行要 `MSYS_NO_PATHCONV=1 cmd.exe /c`（`//c` 会把控制台代码页带坏，之后中文输出全乱）。
4. **服务不可用不许让任务失败** → `exit 2` 是契约的一部分，不是错误路径。
5. **概率不能当授权**：不可逆动作仍要人确认。
6. **缓存行要连元数据一起存**（model/server_ms），否则命中时审计字段会丢。
7. **两个"看起来成功"的陷阱**：不设 key 的 judge 静默走兜底模型；标签首 token 相同→均匀分布。两者都**不报任何错** → 每层接入都要有一个"证明真接上了"的动作（`selftest` 就是为它存在的）。

---

## §12 为什么做这个（目的，别丢）

**一句话**：把 agent 里每一句"该不该"从「让大模型写一段话、你再从里面猜」换成「一个几十毫秒、可阈值化、可审计的概率」—— 便宜到可以为**每一个动作**都装一道。

1. **省下的钱和时间换成护栏**：omp 的 judge 已用在意外停止判定、git AI staging（一次 80 个文件）、检索打分；本地化后边际成本≈0，于是可以**多加**判断而不是省着用。
2. **审批与审计**：不可逆动作前一道闸 → "为什么那样做"能答出一行概率 + 一个阈值 + 一条记录。**这是能上生产的前提。**
3. **数据 → 护城河**：每次判断都是标注样本；配合观察到的结果就能在自己域上 fine-tune 并**量自己的 ECE**。权重是 Apache-2.0 的商品，**阈值策略 + 你的标注 + 审计**不是。
4. **天花板（说清楚）**：kev 自报 5% 错误预算下可自动化 **0.45–0.57**（Jev 0.70），通用/知识型判断明显落后 → 只做**窄、重复、错也便宜**的判断；别讲成"替 agent 思考"。

---

## §13 建议技能（下一个会话先读）

- `skill://omp-judge-local-decision-backend` — judge 的 wire contract、静默兜底、标签撞车、三步验收。
- `skill://local-decision-model-calibration` — 校准路线与开源生态现状。
- `skill://agent-scratch-discipline` — 临时文件纪律（本机 `C:\tmp\scratch\<任务>\`，常驻脚本放 `~/Tools/`）。
- `skill://windows-network` / `skill://github-api-access` — 局域网、代理、国内取 GitHub。
- `skill://omp-skill-share` — 把技能分发到多台 harness 的机制。

---

## §14 沟通口径

- 用户自称外行：**先给具体例子和数字，再给术语**，别只堆名词。
- 用户对"它怎么判断的"这类机制问题很敏感（"笔仙"这名字就是这么来的）→ 落到 **一次前向 + 读候选 token 的概率 + 拟合温度**，并强调**它的错是看得见的**（概率可校准检验）。
- **区分"实测"与"预期"**：§10 里 168–248 ms、298 s 是实测；20–60 ms 是预期，真机量过才改口径。

---

## §15 未决项：**无**（三条已收敛，别再重新讨论）

1. **harness 投递**：不重要 —— "普适"是设计规则（§1）：不做适配矩阵，只做三种投递各一次的验收。
2. **审计**：不需要用户先决策 —— 零配置默认开、本机 jsonl（§5）；它同时是 §4 调阈值的数据来源。
3. **权重**：不变，就用 kev-4B（§0）。
