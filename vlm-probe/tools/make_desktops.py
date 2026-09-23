"""Two more synthetic desktops (and their batteries), so a model/resolution matrix has more than
one image to be confused by -- and so resolution is a variable at all.

`som.png` alone is 7 questions on one image at 36 px labels: a question flips hit/miss across
resolutions there, which is noise. Worse, at that text size higher resolution changes nothing, so
a matrix over it could only ever say "768 is enough". Real UI text is 12-20 px, so the bench is
built in tiers:

  * `som.png`            -- 36 px labels, big boxes: the easy tier (already exists)
  * `d_notepad`          -- English menu/toolbar, 20 px labels, three confusable items
                            (Search / Replace / Find Next) plus an unnamed text area
  * `d_settings`         -- Chinese dialog, **14 px** labels: the tier where a 768-long-edge
                            downscale turns the text into mush and the pick has to come from somewhere
  * `d_browser`          -- English browser chrome, **14 px**, a second layout so the "you need
                            ~1536" claim does not rest on one image

Each desktop is emitted four ways:

    d_<name>.png        bare mock desktop (no tool face)
    d_<name>.json       athand-shaped listing, n deliberately scattered (21, 24, ...)
    d_<name>.som.png    the same frame with OUR 1..K numbers drawn by vlm_pick.annotate
    d_<name>.cases      "<intent>=<our index>" lines, for vlm_battery.py / matrix.py

The numbered image comes from `vlm_pick.annotate`, not a second implementation: one numbering,
one place to fix. Ground truth is the control's position in the list; the athand `n` is only
printed, so the mapping can be checked by eye.

    python make_desktops.py
"""
import json
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))      # vlm_pick.py lives at the probe root, not in tools/

import vlm_pick  # noqa: E402

CASES = HERE.parent / "cases"     # every artifact this script writes lands here
FONT = r"C:\Windows\Fonts\msyh.ttc"
W, H = 2240, 1400

DESKTOPS = {
    "notepad": {
        "title": "Notepad++",
        "font": 20,                     # medium tier: a real menu bar
        "chrome": "menu",
        "controls": [
            ("File", (60, 60, 150, 92), "a11y"),
            ("Edit", (160, 60, 250, 92), "a11y"),
            ("Search", (260, 60, 380, 92), "a11y"),
            ("Replace", (390, 60, 520, 92), "a11y"),
            ("Find Next", (530, 60, 680, 92), "a11y"),
            ("Save", (740, 60, 830, 92), "a11y"),
            ("Print", (840, 60, 930, 92), "a11y"),
            ("Close", (940, 60, 1030, 92), "a11y"),
            ("", (60, 150, 2180, 1360), "shape"),
        ],
        "questions": [
            ("open the File menu", "File"),
            ("edit the document", "Edit"),
            ("save this document to disk", "Save"),
            ("print the current document", "Print"),
            ("close this window", "Close"),
            ("find the next occurrence of the search term", "Find Next"),
            ("replace text in the document", "Replace"),
            ("search for text in the document", "Search"),
            ("click into the document body to start typing", ""),
        ],
    },
    "settings": {
        "title": "设置",
        "font": 14,                     # hard tier: real dialog text
        "chrome": "dialog",
        "controls": [
            ("确定", (1620, 1230, 1720, 1262), "ocr"),
            ("取消", (1740, 1230, 1840, 1262), "ocr"),
            ("应用", (1500, 1230, 1600, 1262), "ocr"),
            ("高级设置", (90, 250, 330, 282), "ocr"),
            ("恢复默认", (90, 300, 330, 332), "ocr"),
            ("导入配置", (90, 350, 330, 382), "ocr"),
            ("导出配置", (90, 400, 330, 432), "ocr"),
            ("帮助", (90, 450, 330, 482), "ocr"),
            ("", (700, 250, 2000, 290), "shape"),
        ],
        "questions": [
            ("确认并关闭这个对话框", "确定"),
            ("放弃这次修改", "取消"),
            ("保存设置但不关闭窗口", "应用"),
            ("打开高级设置", "高级设置"),
            ("把设置恢复成默认值", "恢复默认"),
            ("从文件导入配置", "导入配置"),
            ("把配置导出到文件", "导出配置"),
            ("打开帮助", "帮助"),
            ("在设置里搜索", ""),
        ],
    },
    "browser": {
        "title": "浏览器",
        "font": 14,                     # second hard tier: another 14 px layout, other words
        "chrome": "menu",
        "controls": [
            ("Back", (60, 60, 140, 88), "a11y"),
            ("Forward", (150, 60, 250, 88), "a11y"),
            ("Reload", (260, 60, 360, 88), "a11y"),
            ("", (380, 60, 1400, 88), "shape"),
            ("Bookmark", (1410, 60, 1530, 88), "a11y"),
            ("Downloads", (1540, 60, 1660, 88), "a11y"),
            ("Menu", (1670, 60, 1750, 88), "a11y"),
            ("Close tab", (1000, 10, 1130, 42), "a11y"),
            ("", (60, 120, 2180, 1360), "shape"),
        ],
        "questions": [
            ("go back to the previous page", "Back"),
            ("go forward to the next page", "Forward"),
            ("reload the current page", "Reload"),
            ("bookmark this page", "Bookmark"),
            ("open the downloads list", "Downloads"),
            ("open the browser menu", "Menu"),
            ("close this tab", "Close tab"),
            ("type a new web address", 4),  # the two shape controls have no label to match on
            ("click into the page content", 9),
        ],
    },
}


def font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def draw(spec):
    img = Image.new("RGB", (W, H), (245, 246, 248))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W - 1, H - 1), fill=(255, 255, 255), outline=(200, 205, 212), width=3)
    d.rectangle((0, 0, W, 46), fill=(238, 240, 244))
    d.text((24, 10), spec["title"], font=font(22), fill=(20, 20, 20))
    if spec["chrome"] == "menu":
        d.rectangle((0, 100, W, 130), fill=(248, 249, 251))           # toolbar strip
    else:
        d.rectangle((60, 70, 400, 1330), fill=(249, 250, 252))        # sidebar
    for label, (x0, y0, x1, y1), _src in spec["controls"]:
        d.rounded_rectangle((x0, y0, x1, y1), radius=6, outline=(150, 156, 166), width=2)
        if label:
            # clear of the number patch at the box's top-left corner (athand's tool face)
            d.text((x0 + 30, y0 + (y1 - y0 - spec["font"]) // 2), label, font=font(spec["font"]),
                   fill=(25, 25, 25))
    return img


def main():
    for name, spec in DESKTOPS.items():
        frame = CASES / f"d_{name}.png"
        draw(spec).save(frame)

        targets = []
        for i, (label, rect, src) in enumerate(spec["controls"]):
            targets.append({"n": 21 + 3 * i, "name": label, "cls": "text" if label else "",
                            "rect": list(rect), "patterns": [], "source": src})
        listing = {"hwnd": 4653616 + len(name), "title": spec["title"],
                   "note": f"synthetic: {frame.name} + this listing (athand numbers scattered)",
                   "at": "2026-09-22 23:59:00", "rect": [0, 0, W, H], "state": "normal",
                   "frame": frame.name, "targets": targets}
        (CASES / f"d_{name}.json").write_text(json.dumps(listing, ensure_ascii=False, indent=1), encoding="utf-8")

        rows = vlm_pick.shortlist(listing, "", None, len(spec["controls"]))
        assert [r["name"] for r in rows] == [c[0] for c in spec["controls"]], "numbering order drifted"
        vlm_pick.annotate(frame, rows).save(CASES / f"d_{name}.som.png")

        lines = []
        for intent, which in spec["questions"]:
            if isinstance(which, int):  # a shape has no label to name it by
                idx = which
            else:
                hits = [i for i, c in enumerate(spec["controls"], start=1) if c[0] == which]
                assert len(hits) == 1, f"{name}: {which!r} matches {hits}; use an index"
                idx = hits[0]
            lines.append(f"{intent}={idx}")
        (CASES / f"d_{name}.cases").write_text("\n".join(lines) + "\n", encoding="utf-8")

        print(f"{name}: {len(spec['controls'])} candidates, {len(lines)} questions, "
              f"{spec['font']}px labels -> d_{name}.png / .json / .som.png / .cases")
        for i, (label, rect, src) in enumerate(spec["controls"], start=1):
            print(f"   our #{i} = athand #{21 + 3 * (i - 1):<3} {label or '(no name)':14s} {src} {rect}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
