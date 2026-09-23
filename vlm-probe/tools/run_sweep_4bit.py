"""Driver: run vlm_sweep.py in 4-bit without fighting cmd.exe argument quoting.

The batch/one-liner route mangles the Chinese question text (MSYS quoting -> cmd.exe re-parse
splits on spaces inside the arguments), so the argv lives here as UTF-8 source instead.
"""
import pathlib
import runpy
import sys

HERE = pathlib.Path(__file__).resolve().parent

sys.argv = [
    "vlm_sweep.py",
    "--model",
    str(HERE.parent.parent / "models" / "qwen3vl-4b"),
    "--image",
    str(HERE.parent / "cases" / "som.png"),
    "--question",
    "点击发送按钮",
    "--expect",
    "4",
    "--edges",
    "0,1568,1024",
    "--load-in-4bit",
]
runpy.run_path(str(HERE / "vlm_sweep.py"), run_name="__main__")
