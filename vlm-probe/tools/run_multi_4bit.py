"""Driver: run vlm_multi.py in 4-bit for the 7-question battery at three resolutions.

Same reason as run_sweep_4bit.py: keep the Chinese case list out of the shell's quoting rules.
Baseline to beat (bf16, edge 1024): 6/7, the miss is 把文件发给 Blinvo at p=0.4420.
"""
import pathlib
import runpy
import sys

HERE = pathlib.Path(__file__).resolve().parent

sys.argv = [
    "vlm_multi.py",
    "--model",
    str(HERE.parent.parent / "models" / "qwen3vl-4b"),
    "--image",
    str(HERE.parent / "cases" / "som.png"),
    "--edges",
    "0,1568,1024",
    "--load-in-4bit",
    "--cases",
    ";".join(
        [
            "点击发送按钮=4",
            "把文件发给 Blinvo=3",
            "查看家庭群的消息=7",
            "撤回刚才那条消息=6",
            "打开搜索框=2",
            "把这条消息转发给文件传输助手=8",
            "开始写一条新消息=9",
        ]
    ),
]
runpy.run_path(str(HERE / "vlm_multi.py"), run_name="__main__")
