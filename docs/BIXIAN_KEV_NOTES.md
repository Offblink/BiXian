# Bi-Xian 后端（kev-4B）部署笔记

这台机同时是**开发机**和 §8 说的**GPU 那台**（RTX 5070 Ti Laptop / **12 GB 显存** / 31.7 GiB 内存）。
客户端的仓在 `bixian\`（M0 快照，来自另一台机），后端在 `kev\`（上游 clone），权重在 `models\`。

## 怎么用（双击）

| 双击 | 干什么 |
|---|---|
> 2026-09-23：下面三个双击入口与它们的 `.ps1` **已合成一个 Python**：`archive\kev_text.py start|stop|selftest`
> （`.cmd`/`.ps1` 双文件壳不专业），
> 且文本档后端本身已废（`models\base` 已删）—— 这张表保留作「当时是什么」的记录。

| `起服务.cmd` | 在 `127.0.0.1:8009` 起 kev-4B（约 12–45 秒加载）。**窗口别关**；已在跑会提示，不重复起 |
| `停止服务.cmd` | 停掉 8009 上的服务 |
| `自检.cmd` | 四步：后端在不在 → 客户端 selftest → 三条真实判断（计时）→ 不可逆闸门与审计 |

命令行等价物（客户端零安装）：

```powershell
$env:DECIDE_URL = "http://127.0.0.1:8009"; $env:DECIDE_MODEL = "kev-latest"
python .\client\bin\decide.py noul "<state>" "<问题>"
# 退出码即答案：0=YES 1=NO 2=UNDECIDED（回退到自己的模型，绝不猜）
```

## 目录

```
project\bixian\           2026-09-23 重排：根只留 README.md + 7 个子目录
├─ README.md            目录地图（活/死、怎么跑一行命令）
├─ vlm\.venv\           **现役解释器**（从 kev\.venv 同卷搬出，7.74 GB：
│                       torch 2.8.0+cu128 / transformers 5.17 / tbnb 0.50.2 / torchvision / pillow）
├─ vlm\recipe\          pyproject.toml + uv.lock —— 重建这套环境的配方（uv sync）
├─ models\qwen3vl-4b\   现役权重（Qwen3-VL-4B 官方，8.28 GB，零微调）
├─ vlm-probe\           vlm_pick.py / eyes.py + tools\ cases\ evidence\ policies\
├─ docs\                本笔记 + VLM_DECISION_PROBE.md + HANDOFF_VLM_ATHAND.md
├─ scripts\             fetch_ms_model.py（从 ModelScope 拉模型仓，逐文件 sha256）
├─ client\              **你自己的** Bi-Xian 客户端（M0 快照：CLI / 策略 / 审计 / 评测 / 技能契约）——
│                       现役：vlm_pick 从 `client\src` 里 import policy/policies
├─ repos\kev\           **第三方** jaredpalmer/kev 的 clone **只剩 docs\ + skills\（1.24 MB）**：
│                       2026-09-23 按用户指示删掉 evals/runs/.git/源码/pyproject 等 110 MB；
│                       源码没了 → 本机 `python -m kev.serve` 已不可能，要复活先 `git clone` 回来
└─ archive\              文本档留下的两个小件（base 已删、跑不起来）：
   ├─ kev_text.py      start / stop / selftest 三合一（无 shell、非交互、给退出码）
   └─ make_run_dir.py  把 head.pt 的 base 从 Hub id 改成本地路径
                        （复活步骤见 docs\ARCHIVE_KEV_TEXT.md）
   已删 2026-09-23：models\base\（8.70 GB）、models\kev-4b\、models\kev-4b-local\（各 0.15 GB）
```

> **venv 为什么能搬**：同卷 `Move-Item` 是元数据操作（实测 **0.4 s**，不复制 7.7 GB）；
> venv 靠**自己根目录的 `pyvenv.cfg`**（`home` 指向系统 Python 3.13）定位 base，不靠 kev 源码。
> 搬完只需清掉两个写死旧路径的 `__editable__*kev*`（`.pth` + finder）——我们不 `import kev`。
> `Scripts\*.exe` 的控制台 shim 内嵌旧路径，所以只用 `python.exe`（`-c` / 脚本 / `-m`），别用它们。

## 权重来历存档（2026-09-23 删库前抄下来的，别再猜）

`models\kev-4b[ -local]` 已于 2026-09-23 删除；下面是 `kev-4b-local\head.pt` 的元数据（删前实测）：

```
base            <本机路径>\models\base        ← archive\make_run_dir.py 改写过的；原本是 Qwen/Qwen3.5-4B-Base
base_revision   None
lora            16            head_dim 256     option_isolation False   special_embeddings False
args            {'base': 'Qwen/Qwen3.5-4B-Base', 'n_per_source': 1000, 'epochs': 1,
                 'lr': 2e-05, 'head_lr': 0.0, 'weight_decay': 0.01, 'lora': 16, 'accum': 2, ...}
init_source     {'init_from': 'jaredpalmer/kev-4b',
                 'resolved': '/__modal/volumes/vo-kEMu8BkBAIrorAQI6V8f2D/hub/'
                             'models--jaredpalmer--kev-4b/snapshots/f780095d...'}
temperature     2.1435469250725863      temperature_fit {'rows': 'runs/nigh…'}
```

**读法**：这是**上游作者（jaredpalmer）在 Modal 上产出**的发布件 —— 训练脚本、LoRA、温度标定、评测（`result.json`）
全是随包发的；本机唯一动过的手是 `archive\make_run_dir.py` 把 `head.pt` 里的 `base` 从 Hub id 改成**本地路径**
（离线可跑）。`kev\` 里的 `runs\*`（651 个 tracked 文件、`train_kev.log` 写着 `device=mps`）也是**上游台账**，
不是本机跑的。

## 装的时候发生的三件事（重装时照做）

1. **上游 clone**：`git -c http.proxy=http://127.0.0.1:7897 clone --depth 1 https://github.com/jaredpalmer/kev.git`
2. **依赖**：`uv sync --extra serve`（清华当唯一 index；用系统 Python 3.13，避免 uv 去 GitHub 拉托管解释器）。
   **然后必须换掉 torch**：PyPI 上 Windows 的 `torch 2.8.0` 是 **CPU 版**（230 MB，`torch.version.cuda` 为 None）。
   要 `torch-2.8.0+cu128`（3.46 GB，cu128 的编译架构列表里有 `sm_120`）：
   `uv pip install --reinstall --no-deps <wheel>`（`--no-deps` 是故意的，依赖已由 uv sync 装好）。
3. **权重用 Flower 下**（`skill://flower-this-box`），**源按仓库分别选**：
   - `Qwen/Qwen3.5-4B-Base` → **ModelScope**（比 hf-mirror 快一个数量级，且每个文件都发 sha256）
   - `jaredpalmer/kev-4b` → **hf-mirror**（ModelScope 没有这个仓）
   - 两个 shard 的 ModelScope sha256 与 HF 的 LFS sha256 **逐字节相同**，所以拿 HF 的哈希校验 ModelScope 的下载成立。
   - 命令形状：`python -m flower <链接> -d <目录> --no-proxy -n 16 --sha256 <hex> --json`
   - **坑**：Flower 对 ModelScope 上**几 KB 级的小文件**会 probe 成 `size: 1, ranges: false` 然后判"提前结束"而失败
     （大文件都正常）。小文件直接 `curl`/`urlopen` 下来再核 sha256 —— 本机 `models\base\*.json` 就是这么补的。
   - pytorch 的 cu128 wheel 在 aliyun 的 `pytorch-wheels` 镜像上有（tsinghua/bfsu/nju 都是 404），
     单连接只有 ~120 KB/s，必须走 Flower 多连接。**官方不公布 wheel 哈希**，所以它的完整性只能靠
     大小一致 + `torch.cuda.is_available()` + 一次真 matmul 来证明。

## 为什么需要一个 `kev-4b-local`

`kev.serve --run <目录>` 收的是一个目录，但 checkpoint 的 `head.pt` 里把底模记成 **Hub id**
（`Qwen/Qwen3.5-4B-Base` + revision `1001bb4d…`），`kev.checkpoint` 会把这两个字符串直接交给
`transformers`。这台机已经把底模下到本地了，所以运行副本把 `base` 改成那个本地目录、并把
`base_revision` 清空（本地路径配 revision，transformers 不接受）。**上游仓一个字节都没改**。

## 为什么起服务必须带 `KEV_MERGE=0`

`kev/checkpoint.py` 的合并路径把 dtype 强制成 **fp32**，而 `DecisionModel.__init__` 的最后一行是
`self.to(device)`。也就是 fp32 的 4B（约 18.6 GB）会被**直接搬上显卡** —— 这张卡 12 GB，必然 CUDA OOM。

`KEV_MERGE=0` 时模型按 `KEV_DTYPE`（bf16）建，9.3 GB 上卡，落得下；代价只是适配器不参与合并
（README 记的 `max|dp|`：不合并 0.029 对 fp32 合并后转 bf16 0.017，都远小于阈值尺度）。

> 注意：客户端仓 `deploy/serve-kev.sh` 里**没有** `KEV_MERGE=0`，而它的注释假设的是 16 GB 卡 ——
> 按上面的代码路径，16 GB 也装不下 18.6 GB。这在 24 GB 的 L4（他们 Modal 上）不暴露，但在消费级卡上会炸。
> 本条是**代码级推断**，没有实跑那条失败路径（那要先把 18.6 GB 建在只有 ~19.6 GB 可用的内存上）。

## flash-linear-attention：不装就是慢，装了快 27%

Qwen3.5 有 Gated DeltaNet 层（混合骨干）。没装 FLA 时服务日志自己会打：

> `chunk_gated_delta_rule` is falling back to its reference PyTorch implementation because
> `flash-linear-attention` is not installed. This is correct but much slower

Windows 上官方 `triton` 没有轮子，要先垫第三方的 `triton-windows`（cp313 轮子有）：

```powershell
uv pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple triton-windows==3.8.0.post28 flash-linear-attention
```

实测：5 问一请求的中位延迟 **139.6 ms → 102.1 ms**。首次调用会 JIT 编译内核（约 1.3 s），之后稳定。

## 实测数字（2026-09-22）

| 项 | 实测 |
|---|---|
| 加载 | `Loading weights 100% | 426/426`；进程起到可服务约 12–15 s（盘缓存热），冷读盘约 45 s |
| 显存 | **9136 / 12227 MiB**（bf16 4B 预期值，余约 3 GB） |
| 服务端延迟 | 5 问一请求（168 token）中位 **102 ms**（装 FLA 前 139.6 ms） |
| 端到端 | 单条 CLI 中位 **196 ms**（含 Python 启动税 168–248 ms 的一部分），自检脚本里 102–136 ms |
| §6 M1④ 判据 | 端到端 <300 ms → **满足** |
| §7.3 换序 | 两道题 argmax 都不翻转（spread 0.01 / 0.06） |
| 题间隔离 | packed vs `separate` 差值 ≤0.01 |
| §7.4 接上了没 | 服务端日志出现来自 omp judge 的 `POST /v1/systemone 200 OK` |
| §7.6 注入 | **0.13 → 0.55（+0.42）**：注入把判断从 NO 拖成了 YES |
| omp judge | `TYPESAFE_API_KEY=local` + `TYPESAFE_BASE_URL=http://127.0.0.1:8009` 后，`omp models --kind judge` 发现 `jev-latest`；真调用 `judge()` 返回 `{'billing': {'type': 'bool', 'bool': 0.92}}` |

**注入这条是这次最重要的一条**：kev 自身**不抗注入**（它把 state 里的话当真指令）。所以 §7.6 的
"不可逆闸门必须 `--origin trusted`"不是设计偏好而是必需项（客户端已实现并有测试）；**同时**
`explore` / `default` 档允许 untrusted 文本，它们**仍会吃到被注入的内容** —— 能喂不可信文本的闸门，
必须自己保证 state 干净，或把阈值抬到注入能抬到的水位（实测能抬到 0.55）之上。

修复后的最终冒烟（真后端，2026-09-22）：`selftest` 报 `temperature=2.1435469250725863`；
billing 票 `YES p=0.89`；白屏票 `NO p=0.02`（退出码 1）；注入 state 撞不可逆闸门 → `rc=2 UNDECIDED`，
且审计行里 `p` / `checkpoint_temperature` / `cached` **全为空** —— 那条请求根本没发出去
（拒绝发生在发送之前），温度为空是正确结果而不是遗漏。

## 阈值的来源（不是拍脑袋）

`models/kev-4b/result.json` 是发布方自己的评测，直接给了 risk–coverage 上的几个点（`calibrated_clean`）：

| 口径 | n | 准确率 | ECE | 5% 错误预算下可自动化 |
|---|---|---|---|---|
| 分布内 | 1264 | 0.872 | 0.0129 | **0.779** |
| 跨域 | 656 | 0.797 | 0.0209 | **0.610** |

`confident_error_rate` 0.021 / 0.023；换序 `mean_max_delta` 0.014 / 0.053。

**这修正了 handoff §12.4 的天花板口径**：那里记的 0.45–0.57 是 **Kev-9B** 的数字。但 §4.1 的纪律不变 ——
这些是**别人的域**，`policies/default.json` 里的 0.20 / 0.50 / 0.99 仍然只是起点，要在自己的冻结集上重估。

## 还没做的

- 开机自启（§8 的降级矩阵要求消费端自己实现；本机可以挂任务计划）。
- 上游 `kev/serve.py` 把 host 写死成 `127.0.0.1`，所以**别的机器连不上**；跨机用得起一个自己的入口。
- 阈值重估：要用 `evals\` 冻结集 + `tools\eval.py` 出 ECE/Brier 与「5% 预算下可自动化比例」再定。

### 客户端实测缺陷：三个，已修（2026-09-22）

三个都是"看起来成功"的静默错误 —— 退出码 0、不报任何错。实测证据与修法：

1. **温度取不到 → 审计缺 `checkpoint_temperature`**。`client.model_info` 只认
   `{"model":…, "checkpoint":{"temperature":…}}` 与 `{"data":[…]}`，而真 kev 返回
   `{"models":[{"id":…,"temperature":2.1435…,"run":…}]}`。
   **修**：`client.py` 三种形状都读（kev 自己的 / System One 网关 / OpenAI 风格列表）。
   **证**：修前 `selftest` 打 `temperature=None`、审计行没有该字段；修后
   `temperature=2.1435469250725863`，审计行 `checkpoint_temperature` 一致。
2. **缓存键不含后端身份**。`make_key(url,payload,model)` 里 url 相同就把桩和真 kev 混成一条。
   **修**：键增加后端身份（`GET /v1/models` 的 `run`）。
   **证**：实测同一条输入，走缓存 `p=0.5/server_ms=0.11`（桩的答案）vs `--no-cache`
   `p=0.56/server_ms=202.7`；修后真 kev 写下的缓存条目，其键**等于**含 `run` 的键、**不等于**不含的键。
3. **`_model_meta` 的 1 小时缓存不绑定后端身份**（实测缓存过 `{"run":"kev-stub","temperature":2.2}`）。
   **修**：默认**每次决策问一次** `GET /v1/models`（`BIXIAN_MODELS_TTL=<秒>` 才是显式的"用新鲜度换一次 GET"）；
   身份随本次调用传递，不做进程内 memo —— 否则同一个 bug 会搬到"长驻进程内 import CLI"的场景。

**回归用例 10 条**：`tests/test_identity.py`（8 条，其中"kev 真实响应"是逐字抄下来的）
+ `tests/test_ouija.py` 的"换后端不复用旧答案""审计行必须带温度""身份每决策问一次""TTL 是显式选择"。
测试总数 32 → **44**。

**并且证明了这些用例真的钉得住**（仓里没有 git 历史，所以用副本回退法）：

| 副本的状态 | 结果 |
|---|---|
| 修复前客户端 + **新桩（真 kev 形状）** | **6 条红**，含**原有**的 `test_audit_row_records_what_is_needed_to_explain_it` |
| 修复前客户端 + **旧桩（客户端想象出来的形状）** | 4 条红 —— 那两条温度用例**变绿了** |

也就是说：**旧的桩把两条温度相关的用例洗白了**；而那条"逐字抄真响应"的用例在两个状态下都红。
这是 handoff §11.1（"桩要照目标后端的真实形状写"）的实证，所以桩本身也改成了 kev 的真实形状，
并新增 `set_identity(run=…)` 用来扮演"同一个 URL 换了后端"。
