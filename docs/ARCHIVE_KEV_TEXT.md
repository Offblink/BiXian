# kev 文本决策档：已废，只剩一个小件（2026-09-23）

## 结论

这条线**跑不起来了，外壳也已删**：

- `models\base\`（Qwen3.5-4B-Base，8.70 GiB）、`models\kev-4b\`、`models\kev-4b-local\`
  **已按用户指示删除**；
- `repos\kev\` 只剩 `docs\` + `skills\` → 本机 `python -m kev.serve` 已无模块可跑；
- 起停外壳（3 个 `.ps1` + 3 个中文双击 `.cmd`）**已合成一个 Python**：`archive\kev_text.py`
  （`start` / `stop` / `selftest`）—— 用户裁决：`.cmd`+`.ps1` 双文件壳「一看就不专业」，
  何况是死档。**没有删掉任何能力**：起、停、四段自检都还在，只是换成一个非交互的命令行。

现在活着的是**视觉档**：`vlm\.venv` + `models\qwen3vl-4b` + `vlm-probe\`（见 `VLM_PROBE.md`）。

## 留下的两个小件

### `archive\kev_text.py`（起停 + 自检，纯标准库、无 shell）

```powershell
python archive\kev_text.py start      # 8009；自己设 KEV_MERGE=0 + KEV_DTYPE=bf16
python archive\kev_text.py stop       # 按端口找 PID（netstat，无依赖）-> taskkill -> 确认端口释放
python archive\kev_text.py selftest   # 四段：后端在不在 / 客户端 selftest / 三条真实判断计时 / 不可逆闸门+审计
```
非交互：失败给原因 + 非零退出码，不再有"按回车退出"。`selftest` 的判据是 eth0 级的：
`/v1/models` 里**没有** `models[].base` 就判"端口上有东西但不是 kev"（当年那套要防的正是这种"看起来成功"）。

### `archive\make_run_dir.py`（生成运行副本）

上游发布件不能直接跑：`head.pt` 里的 `base` 是 **Hub id**（`Qwen/Qwen3.5-4B-Base` + revision），
`transformers` 拿到「本地路径 + revision」会报错。这个脚本把发布件复制成 `models\kev-4b-local\`，
把 `base` 改成本地绝对路径、清空 `base_revision`。**上游仓一个字节不改**。

它也是"本机跑过上游发布件"唯一动过的手 —— 打补丁 ≠ 训练（权重是 jaredpalmer 在 Modal 上产的）。

## 要复活它（三步）

```powershell
# 1. 重下权重（本项目的下载器，零外部依赖）
python scripts\fetch_ms_model.py Qwen/Qwen3.5-4B-Base -d models\base
python scripts\fetch_ms_model.py jaredpalmer/kev-4b   -d models\kev-4b     # 注意：这个仓在 hf-mirror，不在 ModelScope

# 2. clone kev 源码回 repos\kev\（只剩 docs/skills 不够跑）
# 3. 生成运行副本，然后起服务（起服务那步会把两个环境变量设好）
..\vlm\.venv\Scripts\python.exe ..\archive\make_run_dir.py
python archive\kev_text.py start
```

客户端自检仍然可用（它只打 HTTP）：`python archive\kev_text.py selftest`

**为什么必须 `KEV_MERGE=0`**：合并路径把 dtype 强制成 fp32，而 `DecisionModel.__init__` 最后一行是
`self.to(device)` —— fp32 的 4B（约 18.6 GB）会被直接搬上这张 12 GB 卡，必然 CUDA OOM。

完整配方、四条「看起来成功」的静默缺陷（后端身份进缓存键、温度解析形状…）、审计与阈值的坑，
都在 `BIXIAN_KEV_NOTES.md`。
