"""Driver: same as run_multi_1024_bf16.py but with 4-bit weights (the production candidate)."""
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
    "--load-in-4bit",
    "--cases",
    CASES,
]
runpy.run_path(str(HERE / "vlm_multi.py"), run_name="__main__")
