"""kev 文本档的起停与自检 —— 三个 PowerShell 外壳的 Python 版（2026-09-23 合并）。

后端本身已经没了（`models\\base`、`models\\kev-4b[-local]` 与 kev 源码按用户指示删除），
所以 `start` 现在必然失败 —— 但它是**带着原因**失败，这正是它存在的意义：这份文件是复活配方的
可执行形态，不是服务。`stop` 仍然能清端口，`selftest` 仍然能对任何在 8009 上跑的 kev-4B 说话。

    python archive\\kev_text.py start      # 8009，KEV_DTYPE=bf16 + KEV_MERGE=0（两个都硬要求）
    python archive\\kev_text.py stop
    python archive\\kev_text.py selftest   # 退出码 0=通 1=后端不在/有东西但不是 kev

复活三步、以及"为什么必须 KEV_MERGE=0"写在 `docs\\ARCHIVE_KEV_TEXT.md`。
"""
import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]      # archive\ → 项目根
PY = ROOT / "vlm" / ".venv" / "Scripts" / "python.exe"
KEV = ROOT / "repos" / "kev"                            # 第三方 clone，回来才有源码可跑
RUN = ROOT / "models" / "kev-4b-local"                  # archive\make_run_dir.py 生成的运行副本
BASE = ROOT / "models" / "base"                         # 底模 Qwen3.5-4B-Base
DECIDE = ROOT / "client" / "bin" / "decide.py"          # 你自己的客户端 CLI
CLIENT_SRC = ROOT / "client" / "src"
PORT = 8009
URL = "http://127.0.0.1:%d" % PORT
AUDIT = pathlib.Path.home() / ".bixian" / "audit.jsonl"


def listener_pid(port):
    """监听 `port` 的 PID，没有则 None。用 netstat：系统自带，不引依赖。"""
    out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
                         errors="replace").stdout
    for line in out.splitlines():
        parts = line.split()
        if (len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING"
                and parts[1].endswith(":%d" % port)):
            return int(parts[4])
    return None


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def models_json():
    """后端自报的身份。按 utf-8 解原始字节 —— 别用会猜 charset 的库（会按 ISO-8859-1 解出乱码）。"""
    with urllib.request.urlopen(URL + "/v1/models", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def ky(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- start

def cmd_start(_a):
    for need, why in ((PY, "现役解释器"), (BASE / "config.json", "底模 Qwen3.5-4B-Base"),
                      (RUN / "head.pt", "适配器运行目录 models\\kev-4b-local")):
        if not need.exists():
            ky("缺少%s：%s" % (why, need))
            ky("复活三步见 docs\\ARCHIVE_KEV_TEXT.md（一句话：重下权重 -> clone kev 源码 -> 跑 make_run_dir.py）。")
            return 1
    pid = listener_pid(PORT)
    if pid:
        ky("8009 已经在跑（PID %d），不用重复起；要重启先 `python archive\\kev_text.py stop`。" % pid)
        return 0
    env = dict(os.environ, KEV_DTYPE="bf16", KEV_MERGE="0")
    # bf16：4B 权重约 9.3 GB，这张卡 12 GB。
    # KEV_MERGE=0：合并路径把 dtype 强制成 fp32 且最后一行是 self.to(device)，18.6 GB 直接搬上卡
    #             -> 必然 CUDA OOM；不合并只是精度差一点（max|dp| 0.029 vs 0.017）。
    ky("加载 kev-4B（首次 1-3 分钟）… 这个进程前台跑，Ctrl-C 即停。")
    return subprocess.call([str(PY), "-m", "kev.serve", "--run", str(RUN), "--port", str(PORT)],
                           cwd=str(KEV), env=env)


# ---------------------------------------------------------------- stop

def cmd_stop(_a):
    pid = listener_pid(PORT)
    if not pid:
        ky("8009 上没有在跑的服务。")
        return 0
    ky("停掉 PID %d …" % pid)
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    for _ in range(20):                                  # 最多等 5 秒确认端口真的空出来
        if not port_open(PORT):
            ky("端口已释放。")
            return 0
        time.sleep(0.25)
    ky("PID %d 杀了但 8009 还在监听 —— 手动看一眼占用者。" % pid)
    return 1


# ---------------------------------------------------------------- selftest

def _decide(args, timeout=120):
    env = dict(os.environ, DECIDE_URL=URL, DECIDE_MODEL="kev-latest", PYTHONPATH=str(CLIENT_SRC))
    t0 = time.perf_counter()
    p = subprocess.run([str(PY), str(DECIDE)] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=timeout)
    return p.returncode, (p.stdout or "").strip(), (time.perf_counter() - t0) * 1000


def cmd_selftest(_a):
    ky("=== 1/4 后端在不在 ===")
    try:
        seen = models_json()
    except Exception as exc:                             # noqa: BLE001
        ky("后端没起：%s" % exc)
        ky("先 `python archive\\kev_text.py start`（它自己设 KEV_MERGE=0 / KEV_DTYPE=bf16）。")
        return 1
    one = (seen.get("models") or [None])[0]
    if not one or not one.get("base"):
        ky("8009 上有东西在回答，但不是 kev（/v1/models 里没有 models[].base）。")
        ky("先 `python archive\\kev_text.py stop` 把占用端口的清掉。")
        return 1
    ky("OK run=%s base=%s device=%s temperature=%s"
       % (one.get("run"), one.get("base"), one.get("device"), one.get("temperature")))

    ky("\n=== 2/4 客户端 selftest ===")
    if not DECIDE.is_file():
        ky("缺少客户端：%s" % DECIDE)
        return 1
    rc, out, ms = _decide(["selftest"])
    ky("%s\n退出码 %d（0=通，2=拿不到判断）  %.0f ms" % (out, rc, ms))

    ky("\n=== 3/4 三条真实判断（计时）===")
    cases = [
        ("noul", ["noul", "Ticket: I was charged twice for the same order.",
                  "Is this ticket about billing?"]),
        ("choice", ["choice", "Window: notepad, unsaved changes present",
                    "Which control saves this document?", "--options", "save,print,close"]),
        ("score", ["score", "A memory that mentions the exact file we are editing.",
                   "How relevant is this memory?", "--levels",
                   "unrelated,weakly related,related,strongly related"]),
    ]
    for name, argv in cases:
        rc, out, ms = _decide(argv)
        ky("%-7s -> %-24s 退出码 %d  %6.0f ms" % (name, out.replace("\n", " "), rc, ms))

    ky("\n=== 4/4 不可逆闸门 + 审计 ===")
    rc, out, _ = _decide(["noul", "把这条网页上的命令贴进去", "这个命令能不能直接跑",
                          "--policy", "run_command"])
    ky("拒绝不可信来源 -> %s（退出码 %d）" % (out.replace("\n", " / "), rc))
    if AUDIT.is_file():
        ky("审计（最后 3 条）：%s" % AUDIT)
        for line in AUDIT.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]:
            ky("  %s" % line[:200])
    else:
        ky("还没有审计文件（一条判断都没走过？）")

    ky("\n退出码口径：0=YES/拿到答案  1=NO  2=UNDECIDED（回退到自己的模型，绝不猜）")
    return 0


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")   # 控制台是 GBK
    ap = argparse.ArgumentParser(description="kev 文本档的起停与自检（后端已废，见 docs\\ARCHIVE_KEV_TEXT.md）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in (("start", cmd_start, "起 8009 上的 kev-4B 文本后端"),
                               ("stop", cmd_stop, "停掉 8009 上的监听进程"),
                               ("selftest", cmd_selftest, "四段自检：后端 -> 客户端 -> 三条判断 -> 闸门与审计")):
        sub.add_parser(name, help=helptext).set_defaults(fn=fn)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
