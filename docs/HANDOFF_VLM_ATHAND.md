# HANDOFF — 把本地 VLM 接进 athand 的"挑编号"环节

> 2026-09-23 傍晚补记：这份是那一轮的交接。之后的重排（根目录 / docs / archive / client 合并）
> 见 `%USERPROFILE%\Tools\handoffs\handoff-bixian-reorg-20260923.md`。

日期 2026-09-22。给下一个会话（无上下文）。**先读这份，再读 `docs\VLM_DECISION_PROBE.md`（同目录，生态调研 + 全部实测数据）。**

> ## ⚠️ 用户最新决定（2026-09-22，覆盖本文其余部分的优先级）
>
> **1. 只要视觉档 —— 放弃 kev 文本档。** 不再把 kev（Qwen3.5-4B + adapter）当生产路径。
> **2. 要试量化版本。** 量化从"备选"升为**下一步主任务**（见 §8）。
>
> 影响（下一个会话要按这个调整）：
> - **kev 变成可归档**：`models\base\`（Qwen3.5-4B-Base，9.32 GB）、`models\kev-4b\`、
>   `models\kev-4b-local\`、`repos\kev\` 仓库与它的 venv —— **venv 还要用**（VLM 复用它跑），
>   权重可删但**别擅自动手**（用户没说要删；磁盘 569 GB 不是瓶颈）。
> - **`client\`（你那边的 Bi-Xian 客户端）的价值只剩两样**：`policies/*.json` 的**阈值带**与 `audit.py` 的**审计格式**
>   （VLM 不暴露 `/v1/systemone`，所以客户端代码本身用不上，那两样是设计资产，照抄即可）。
> - **量化后能做的事变了**：权重 9.7 GB → ~2.5 GB，腾出的空间可以换 **更高分辨率**（native 也不溢出）
>   或**更大的模型**（`Qwen3-VL-8B` 4-bit ≈ 4.5 GB 就能塞进 12 GB 卡 —— 这是只做视觉档之后
>   顺理成章的升级，比 4B 的视觉理解强）。**不要默认还留在 4B**。
>
> **✅ 2026-09-23 已执行**：`models\base\`（Qwen3.5-4B-Base，8.70 GiB）**已永久删除**（用户指示）。
> 后果：文本档后端从此起不来 —— `kev-4b-local\head.pt` 的 `base` 字段就指向它（其起停外壳 2026-09-23 已删）；
> `models\kev-4b[-local]`（各 0.15 GB 适配器）也**已删**（同轮清理）—— `models/` 现在只剩 `qwen3vl-4b`；
> **`kev\.venv` 已同卷搬出成 `vlm\.venv`**（7.74 GB，实测 0.4 s；清掉了两个写死旧路径的 `__editable__*kev*`，
> 端到端复跑 `eyes.py` 通过），环境配方抄到 `vlm\recipe\`；
> `repos\kev\` 只剩 `docs\` + `skills\`（1.24 MB，方法说明书），源码/evals/.git 已删。
> 要复活文本 kev：`git clone` 回 kev → `scripts\fetch_ms_model.py` 重下 `Qwen/Qwen3.5-4B-Base` + `jaredpalmer/kev-4b`
> → 跑 `archive\make_run_dir.py`（venv 用 `vlm\.venv` 或按 `vlm\recipe\` 重建）。


---

## 1. 用户要什么

用户（外行，但方向感准）想给 **athand**（他的 GitHub 项目 `github.com/Offblink/Athand`，Windows 桌面控制工具）
找一条**更快的 computer use 路径**。他的原话推进过程：
"把 bixian 整合进 athand" → "athand 哪一环节换成 bixian 能加速" → "qwen 不是 vlm，能不能跑在 VLM 上" →
"他们既然有现成的，直接复用不就完了" → 最终定为：**用本地 VLM 做 athand 缺的那一格（挑编号）**。

**athand 的形态**（关键）：单文件 `skills/athand/athand.py`（~4542 行），一次调用一进程，无 daemon，
**内部没有任何模型调用**。流程 `windows → targets → act`；`targets` 产出编号候选（a11y / OCR / 形状切分）
+ 一张画了号码的 PNG；**"从候选里挑哪个"明确留给调用方**（现在是子串匹配取第一个，或 agent 读图自己挑）。
`verify` 是程序事实（帧差/控件值），三次徒劳 `ESCALATED` 停手。**它已有候选索引和验证，只差"挑"这一格。**

## 2. 状态：实测已完成，未落地

**已完成**：
- kev-4B 后端在本机部署并验收（见 §4）
- Qwen3-VL-4B 权重下完并验证
- **本机实测跑通**：读 SoM 编号可行，1024 长边下 **342 ms / 10.37 GiB / 7 问对 6**
- 生态调研完成（见 `docs\VLM_DECISION_PROBE.md` §1）
- 实验脚本与原型接缝已固化在 `vlm-probe/`（见 §5）

**未完成**：
- **真实截图上的验证**（现在只有合成图；这要动桌面，**需用户在场**）
- 落地接缝（athand 一行不改的独立脚本）
- 用户尚未决定是否继续

## 3. 核心数据（本机 RTX 5070 Ti Laptop，`card limit 11.94 GiB`）

### 分辨率是生死线（最重要的一条）

| 长边 | 视觉 token | 前向 | 峰值显存 | 备注 |
|---|---|---|---|---|
| native 2240 | 3080 | **133 297 ms** | **14.55 GiB → 溢出到主机内存** | 慢了 400 倍 |
| 1568 | 1519 | 6074 ms | 11.49 GiB | 勉强 |
| **1024** | **640** | **338–342 ms** | **10.37 GiB** | **甜点，用这个** |
| 768 | 360 | 535 ms* | 10.08 GiB | *含预热 |

视觉 token 的代价**超线性**：640→1519（2.4×）→ 342 ms→6074 ms（**18×**）。
**所以 `targets` 的号码图必须先自己用 PIL 降采样到 1024 长边**（别指望 processor 的 size 参数，见 §6 坑 3/4）。

### 准确率（1024，7 问）

```
HIT  #4  p=1.0000   点击发送按钮
MISS #7  p=0.4420   期望 #3  把文件发给 Blinvo    ← 唯一错，且低置信
HIT  #7  p=1.0000   查看家庭群的消息
HIT  #6  p=1.0000   撤回刚才那条消息
HIT  #2  p=1.0000   打开搜索框
HIT  #8  p=1.0000   把这条消息转发给文件传输助手
HIT  #9  p=0.9983   开始写一条新消息
→ 6/7
```

**关键性质：错的那个 p=0.4420，对的都是 1.0000** → **没有"高置信选错"**，错的会被阈值拦成 `UNDECIDED`。
（与 kev 文本侧实测行为一致：对 0.93–0.97 / 错 0.21–0.23。）

### 换序敏感性（9 次轮转）

| 问题 | raw flip_rate | L0 |
|---|---|---|
| 点击发送按钮 | **0.000** | #4 ✓ |
| 把文件发给 Blinvo | **0.125**（1/9 翻成 #5） | #3，**p=0.9994** |

**比 AnyJev 在文本 LLM 上实测的 raw 0.227 低得多。** 推断原因：**编号画在框上（空间锚定）**，
列表顺序不改变"编号 4 在右下角那个框上" → 位置偏置被图像空间消掉。**对 athand 极有利**（它的候选就是号码框）。
→ **raw 够用；L0 是加固**（把边缘的 0.547 提到 0.9994），成本 K 倍（K=9 → 3.3 s）。折中用 AnyJev 的 `max_permutations` 跑 2–3 次。

### 对照

| 方案 | 延迟 |
|---|---|
| kev 文本（raw，5 问一请求） | **102 ms** |
| **本地 VLM @1024（raw）** | **342 ms** |
| kev 文本 + L0 | ~0.9 s |
| 本地 VLM + L0 | ~3.3 s |
| 云端 SoM 挑号（旧做法） | 2.4–8 s |

## 4. 环境事实（都实测过）

- **项目根**：本仓根目录（`bixian\`）
  （2026-09-23 重排：根只留 `README.md` + 7 个子目录，地图见根 `README.md`）
  - `vlm-probe\` 工具：`vlm_pick.py` / `eyes.py` / `tools\` / `cases\` / `evidence\` / `policies\`
  - `vlm\` = `.venv`（现役解释器）+ `recipe\`（pyproject + uv.lock，重建配方）
  - `models\qwen3vl-4b\`（**8.28 GiB，sha256 已验证**）；`models\base\`、`models\kev-4b[-local]\` **已删**
  - `client\` 你自己的 Bi-Xian 客户端（M0 快照，已修 3 个缺陷并加回归用例，44 测试全绿）—— `vlm_pick` 从
    `client\src` import `policy`（**这行路径跟目录绑定，搬目录必须同改**）
  - `repos\kev\` 第三方 clone（只剩 `docs\` + `skills\`）；`docs\` 三份笔记；`scripts\fetch_ms_model.py`
- **kev 起服**：已废（`models\base` 已删）。现在是 `python archive\kev_text.py start` —— 它自己设
  `KEV_MERGE=0` + `KEV_DTYPE=bf16`，缺权重时会带原因失败；`stop` / `selftest` 同理（否则 fp32 的 18.6 GB 会被 `self.to(device)` 搬上 12 GB 卡 → OOM）。
- **kev venv 复用跑 VLM**：只需补 `torchvision 0.23.0+cu128`（其余全有：torch 2.8.0+cu128、transformers 5.17）。
  `bitsandbytes 0.50.2`（Windows 轮子）也已装好，供 4-bit 备用。
- **显存**：`kev-4B`(9.1 GB) 与 `VLM-4B`(10.4 GB) **不能共存**。要分工得把 kev 降到 0.8B 或 4-bit。
- **下载**：用户要求用 **Flower**（`%USERPROFILE%\Desktop\myProject\Flower`）。教训见 §6。
- **网络**：用户会**换网络**；ModelScope 直连比 hf-mirror 快一个数量级；单连接会被限速但**多连接有效**（8 路 2.05 MiB/s）。

## 5. 脚本与原型（都在 `bixian\vlm-probe\`，已确认拷过去后仍能跑）

| 文件 | 作用 |
|---|---|
| `tools\make_som.py` | 生成合成 SoM 图（9 个候选，含 `发送/发送文件/撤回` 近义陷阱），不碰真实屏幕 |
| `tools\vlm_probe.py` | 单次探针：按 PlayJev 做法读编号概率（float32 读出、Jev 置信度、槽 round-trip 校验、allowed_mass/top_token 诊断） |
| `tools\vlm_sweep.py` | 分辨率扫描（PIL 自缩放 + `--load-in-4bit`） |
| `tools\vlm_multi.py` | 多问题电池（给准确率，支持 `--load-in-4bit`） |
| `tools\vlm_flip.py` | 换序敏感性 + L0（cyclic-shift logmean）对比 |
| `tools\pick_l0.py` | **接缝原型**：读 athand listing → 预过滤 → K 次轮转（cyclic-shift）+ 无标签先验矫正 → 输出编号或 UNDECIDED。**后端是 kev（8009）**，不是 VLM |
| `tools\pick.py` | 更早的 raw 版接缝（后端 kev），已被 `tools\pick_l0.py` 取代 |
| **`vlm_pick.py`** | **落地接缝**：listing + 意图 (+frame) → 自己画 1..K 编号 → 一次前向 → 编号或 UNDECIDED。**athand 一行不改**；`--loop` 保持权重常驻 |
| **`tools\matrix.py`** | 模型 × 分辨率矩阵的驱动器：每个 (权重, 分辨率) 起一个独立进程（同进程连跑会把后面的分辨率量歪） |
| **`tools\vlm_battery.py`** | 上面那个进程：一次加载跑完全部桌面的电池，输出一行 `RESULT {...}` json |
| **`tools\rank.py`** | 把 `C:\tmp\scratch\athand-bixian\matrix-*.json` 合成一张跨模型排序表（准确率 → 高置信错 → 延迟 → 显存） |
| **`tools\make_desktops.py`** | 造合成桌面：裸图 + athand 形状的 listing + 由 `annotate` 画的编号图 + `.cases`（标签歧义会断言报错） |
| `tools\make_som.py` | 最早的合成 SoM 图（`--bare` 出不带工具面的裸桌面） |
| `cases\fixture-som.json` | `cases\bare.png` + 打散的 athand 编号（验证 `our #i -> athand #n` 映射） |

跑法（示例）：
```powershell
$py = "vlm\.venv\Scripts\python.exe"          # 都从仓根出发
$mdl = "models\qwen3vl-4b"
cd "vlm-probe"
$env:PYTHONUTF8=1
& $py tools\vlm_multi.py --model $mdl --image cases\som.png --edges 1024 --cases "点击发送按钮=4;查看家庭群的消息=7"
```

## 6. 坑（会重复咬人）

1. **`{"type":"image"}` 不带载荷会被静默忽略** → 只喂进文本，`in_tok` 停在 139，看起来像"模型很弱"。
   正确：`{"type":"image","image":<PIL>}`。
2. **bf16 logits 会让选项打平**（25–50 量级量化 0.125）→ **必须单独保存一份 fp32 的 head 权重重算读出**，
   否则会得出"模型没信号"的错误结论。（PlayJev 的原话。）
3. **`size={"longest_edge":…}` 的语义不是像素**。猜错后图被缩成 2×2 patch（`vis_tok=1`），
   **三个分辨率给出完全相同的结果** → 差点误判成"降分辨率就选错"。**自己用 PIL 缩放，别猜 processor 语义。**
4. **Qwen 的 `size` dict 必须同时给 `shortest_edge` 和 `longest_edge`**（只给一个直接 ValueError）。
5. **单场景成功/失败都不足以下结论** —— 先确认输入真的进去了（看 `in_tok`/`vis_tok`）。
6. **Flower 现在能跨次续传了**（2026-09-23，0.3.0）：part 旁边写 `.part.json` 条子 + `.part.lock` 排他锁，
   被 kill / 断网 / Ctrl-C 之后重跑会接着下（日志里 `{"event":"resumed",...}`），`--fresh` 才是从头下。
   **但外层脚本仍要自己扛住**：那一晚是**我的 `scripts\fetch_ms_model.py` 崩了三次**（子进程输出解码 ×2、
   `progress` 的 `total` 为 null），每次重试都让 Flower 重开一个进程 —— 修法见
   `skill://modelscope-model-fetch-this-box` 与 `skill://windows-child-process-encoding-traps`。
7. **ModelScope 直链是 302**：测速必须 `curl -L`，否则量到的是重定向响应体（会得出"912 B/s"这种荒唐数）。
8. **别在带宽被自己占满时测速**（我犯过：同时跑着两个多连接下载却测单连接，得出"网络只有 94 KB/s"）。
9. **进程日志别搞混**：第一次（bash async）和第二次（hub detached）的日志用了同名文件，`rc=130` 是旧的那份。

## 7. 生态资源（可复用的，详见 `docs\VLM_DECISION_PROBE.md`）

- **读出方式**：`nokia-applied-research/AnyJev`（pip 装、Apache-2.0）—— raw/L0/L1 三档；**L0 零标签**，
  实测把"≤5% 错误下可自动化"从 **7.7% → 47.7%**。backend 接口只有一个方法
  （`next_token_logprobs(prompts, token_ids)`，返回最后位置的受限 log-probs；**签名无图像通道**，
  用"state 里放占位符 + backend 解析"绕过，不必改它）。
- **实现细节**：`OmniJev/PlayJev`（`playjev/model.py` 的 docstring 把读出、置信度公式、槽校验、prompt 模板全写了）。
- **架构范本**：`fastbrowse`（★95）、`wy-coliney/jev-browser-use`（★363）、`droidjev`（AX → typed pick → adb）
  —— 全是"候选索引 → 挑一个 → 代码验证"，**与 athand 同形**。
- **量化证据**：`BuilderIO/jev-computer-use-tests`（严格后置条件下 Jev 34.6% / 视觉 Luna 96.5%；
  但 Jev 便宜 20 倍、快 12 倍）。**读法**：那是端到端任务成功率，不是"挑一个"的准确率。

## 8. 下一步（用户已定：先做量化）

### 8.1 量化实验（主任务）— **已做完（2026-09-22/23 两轮）**

**一句话：4-bit（nf4）在 4B 上不掉准确率、不涨延迟、显存砍一半，还把 native 从溢出变成能跑。
但"哪一档分辨率最优"要拿真字号的桌面才能问出来 —— 最后落在 `4-bit @1536`。**

最终口径（4 张合成桌面 34 问，`tools\matrix.py`，每格一个进程）：
`4B 4-bit @1536 = 34/34、1068 ms、5.82 GiB、零高置信错`；同分的 bf16 @1536 要 1950 ms / 11.37 GiB。
分辨率的作用全在 14px 那两档（768 时 d_settings 只对 4/9），36px 那张 768 就满分。
**2B 上 4-bit 反而是净亏**（1536：27/34 vs bf16 32/34）——量化要按模型分别量。
全部数字、bench 生成器与坑见 `docs\VLM_DECISION_PROBE.md` §五 / §七。
驱动脚本：`tools\matrix.py`（→ `tools\vlm_battery.py`）、`tools\rank.py`（跨模型汇总）、`tools\make_desktops.py`（造桌面）。

以下是第一轮原始跑法（保留备用）：

`bitsandbytes 0.50.2`（Windows 轮子）**已装**在 kev 的 venv 里；`tools\vlm_sweep.py` 与 `tools\vlm_multi.py`
都已带 `--load-in-4bit`（nf4 + double quant + bf16 compute）。直接跑：

```powershell
$py  = "vlm\.venv\Scripts\python.exe"         # 从仓根出发
$mdl = "models\qwen3vl-4b"
cd "vlm-probe"; $env:PYTHONUTF8=1

# 1) 显存/延迟/分辨率：看权重降下去之后能不能把长边抬回 native
& $py tools\vlm_sweep.py --model $mdl --image cases\som.png --question "点击发送按钮" --expect 4 --load-in-4bit
# 2) 准确率：4-bit 有没有伤到判别力（7 问基线是 6/7，1024 长边）
& $py tools\vlm_multi.py --model $mdl --image cases\som.png --edges 1024,1568 --load-in-4bit `
      --cases "点击发送按钮=4;把文件发给 Blinvo=3;查看家庭群的消息=7;撤回刚才那条消息=6;打开搜索框=2;把这条消息转发给文件传输助手=8;开始写一条新消息=9"
```

**判据（对照 §3 的 bf16 基线）**
| 项 | bf16 基线 | 4-bit 期望 |
|---|---|---|
| 空载权重 | 9.72–9.81 GiB | **~2.5–3.5 GiB** |
| @1024 峰值 | 10.37 GiB | 应显著低于 11.94 上限，且**留出余量** |
| native 2240 | **溢出 → 133 s** | **不溢出**（这是量化的主要收益） |
| @1024 延迟 | 338–342 ms | 可能略慢（反量化开销），**要实测** |
| 7 问准确率 | 6/7（错的 p=0.44） | **不应掉**；掉了说明 4-bit 伤了视觉/读出 |

**两条必须知道的风险**
1. **bitsandbytes 会量化所有 linear 层，包括视觉塔** → 视觉精度可能受损。想只量化语言塔可以用
   `BitsAndBytesConfig(..., llm_int8_skip_modules=[...])`，但那是另一轮实验。
2. **读出会跟着一起退化**：PlayJev 那句"head weight is tied to the embedding, keep a float32 copy for
   the readout"在 4-bit 下**不成立** —— `head_w32` 是**从量化权重反量化**来的，量化误差会直接进 logits。
   所以 4-bit 下**必须重测准确率**，不能假设读出还准。备选：把 `lm_head`/`embed_tokens` 排除在量化之外。

### 8.2 模型轴：2B / 4B / 8B 全量完了 —— **结论是不要换大的**

| 模型 | 最优组合 | 准确率 | 延迟 / 峰值 | 备注 |
|---|---|---|---|---|
| **4B** | **4-bit @1536** | **34/34（零高置信错）** | **1068 ms / 5.82 GiB** | **选它** |
| 4B | bf16 @1280 | 33/34（1） | 577 ms / 10.78 GiB | 快，但贴 11.94 上限 |
| 8B | 4-bit @1024–1792 | 32/34（2–3） | 558–2285 ms / 9.0–11.1 GiB | **不如 4B**，且 native 溢出到 150 s/次 |
| 2B | bf16 @1536 | 32/34（1） | 608 ms / 6.23 GiB | 小模型最准 |
| 2B | bf16 @1280 | 31/34（**零高置信错**） | 516 ms / 5.85 GiB | 安全档 |
| 2B | 4-bit @1536 | 27/34（4） | 553 ms / 3.73 GiB | **4-bit 在 2B 上是净亏** |

8B 为什么输：这个任务（读框上的编号 ↔ 匹配一句意图）4B 已经饱和，剩下的错都在"题本身有歧义"
那一类上，换大模型不解决；而且 8B 4-bit 吃 9–11 GiB（手稿里"≈4.5 GiB"的估计是错的），
**没有余量**——4B 只吃 5.8 GiB，能同时留桌面和别的模型。8B native 2240 直接溢出到 **150–163 s/次前向**。

⇒ **不要再往大模型走**；要更好只能走训练路线（PlayJev 形态）或裁剪输入（crop-zoom）。

> **2B / 8B 的权重已删**（2026-09-23，共 21 GB；`models/` 现在只有 `qwen3vl-4b`）。
> 结论留在文档里；要重跑某一行先 `scripts\fetch_ms_model.py` 把对应仓拉回来。

### 8.3 然后才是接缝与真机验证

1. **落地接缝 —— 已做完（2026-09-22 第二轮）**：`vlm-probe/vlm_pick.py`，**athand 一行不改**。
   `listing + 意图 (+frame) → 编号 或 UNDECIDED`，只打印不动作；`--loop` 保持权重常驻
   （加载 8 s > 一次前向 0.37 s）。关键设计：**编号由我们自己画 1..K（K≤9，单 token 槽）再映射回
   athand 的 n** —— athand 的真实编号跑到几十，`27` 是两个 token，读不出来。实测映射正确、
   两条真实意图都 YES（p=1.0000 / 0.9853），`--expect` 命中、`exit 0`。样例与命令见
   `docs\VLM_DECISION_PROBE.md` §六。
2. **真实截图验证（还没做，唯一剩下的验证）**（**需用户在场，要动桌面**）：用 `athand targets --hwnd N`
   产出的真实 frame + 真实 listing 跑一遍（`--frame <frame.png>`）。合成图的 7/7 说明不了中文 OCR 名、
   密集控件与真窗口尺寸。athand 源码不在本机（repo `Offblink/Athand`），要跑得先 clone 或让用户提供 listing。
3. **训练路线**（若 zero-shot 不够）：PlayJev 的形态（`Qwen3.5-0.8B-Base` + 自接视觉塔 + 2.2M 帧 + 3 轮 DAgger），
   Apache-2.0、同底座可参考。本机 12 GB 训 0.8B 级可行，训不动 4B。

## 9. 用户偏好与纪律

- **中文交流**。讨厌废话（原话"再废话你就崩了"），要结论和数据，不要过程叙述。
- 会质疑方向（"胜算不大还敢试？"）—— 回答要**拆开指标**（端到端成功率 vs 单步准确率），别含糊。
- 要求下载用 **Flower**；临时文件放 `C:\tmp\scratch\`（本项目用 `C:\tmp\scratch\athand-bixian\`）。
- 他关心**开源复用**（"直接复用不就完了"）—— 优先找现成的，别从零搓。
- kev 权重**没有删**（磁盘够、可回退）。既然用户已定只做视觉档，`models\base\`（9.32 GB）与
  `models\kev-4b\` 属于**可归档**资产 —— 但**用户没说要删，别擅自删**；能回收的只是磁盘，不是瓶颈。