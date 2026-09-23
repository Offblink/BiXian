"""Driver: 7-question battery at edge 1024 only, bf16 -- the timing twin of run_multi_1024_4bit.py.

The two harnesses disagree about how long a 4-bit forward at 1024 takes (vlm_sweep said 357 ms,
vlm_multi said ~884 ms), so this pins the same script, same edge, one question battery, and only
the weight format changes.
"""
import pathlib
import runpy
import sys

HERE = pathlib.Path(__file__).resolve().parent

CASES = ";".join(
    [
        "点击发送按钮=4",
        "把文件发给 Blinvo=3",
        "查看家庭群的消息=7",
        "撤回刚才那条消息=6",
        "打开搜索框=2",
        "把这条消息转发给文件传输助手=8",
        "开始写一条新消息=9",
    ]
)

sys.argv = [
    "vlm_multi.py",
    "--model",
    str(HERE.parent.parent / "models" / "qwen3vl-4b"),
    "--image",
    str(HERE.parent / "cases" / "som.png"),
    "--edges",
    "1024",
    "--cases",
    CASES,
]
runpy.run_path(str(HERE / "vlm_multi.py"), run_name="__main__")
