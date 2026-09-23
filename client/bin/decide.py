#!/usr/bin/env python3
"""Run the CLI straight from a clone, with nothing installed:

    python bin/decide.py noul "<state>" "<question>"

Installing the package (`pip install -e .`) gives the same thing as `decide`.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from bixian.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
