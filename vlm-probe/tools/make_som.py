"""Make a synthetic desktop with numbered candidates (SoM), for probing a VLM.

Nothing here reads the real screen: the point is a picture whose ground truth we know,
so "did the model pick the right candidate" is answerable without a human looking.

The style copies athand's `_mark_targets`: a red box per candidate and a number on a
white patch at its top-left, big enough to survive the downscale the VLM applies.

    python tools/make_som.py [path]       # 2240x1400, like this box's screen
                                          # 默认写 cases/som.png
"""
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 2240, 1400
FONT = r"C:\Windows\Fonts\msyh.ttc"          # 微软雅黑: the UI text is Chinese here

# (number, label, rect) -- the trap is deliberate: 4/5/6 are near-synonyms
CANDIDATES = [
    (1, "微信",        (70, 60, 300, 130)),
    (2, "搜索",        (360, 150, 620, 210)),
    (3, "Blinvo",      (360, 300, 700, 360)),
    (4, "发送",        (1700, 1230, 1900, 1310)),
    (5, "发送文件",    (1420, 980, 1700, 1050)),
    (6, "撤回",        (1980, 520, 2140, 580)),
    (7, "家庭群",      (360, 440, 700, 500)),
    (8, "文件传输助手", (360, 580, 760, 640)),
    (9, "写消息",      (360, 1180, 1700, 1300)),
]

WINDOW = (40, 30, 2200, 1370)
SIDEBAR = (300, 140, 780, 1340)
CHAT = (780, 140, 2200, 1340)


def font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def main(path, bare=False):
    img = Image.new("RGB", (W, H), (245, 246, 248))
    d = ImageDraw.Draw(img)
    d.rectangle(WINDOW, fill=(255, 255, 255), outline=(200, 205, 212), width=3)
    d.rectangle((40, 30, 2200, 120), fill=(238, 240, 244))
    d.text((80, 52), "微信", font=font(44), fill=(20, 20, 20))
    d.rectangle(SIDEBAR, outline=(226, 229, 234), width=2)
    d.rectangle(CHAT, outline=(226, 229, 234), width=2)
    # a chat pane with bubbles, and a composer at the bottom
    for i, (x0, y0, x1, y1) in enumerate([(900, 300, 1600, 390), (1100, 430, 1900, 520), (900, 560, 1500, 650)]):
        d.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=(233, 236, 240))
    d.rounded_rectangle((700, 1150, 2140, 1340), radius=14, outline=(210, 214, 220), width=2)

    # the numbers first, so the boxes do not cover them
    for n, label, rect in CANDIDATES:
        if bare:
            break                            # --bare = the raw desktop: the tool face is drawn by the caller
        d.rectangle(rect, outline=(220, 40, 40), width=4)
        patch = d.textbbox((0, 0), str(n), font=font(40))
        pw, ph = patch[2] - patch[0] + 16, patch[3] - patch[1] + 12
        d.rectangle((rect[0] - 2, rect[1] - 2, rect[0] + pw, rect[1] + ph), fill=(255, 255, 255),
                    outline=(220, 40, 40), width=2)
        d.text((rect[0] + 6, rect[1] + 2), str(n), font=font(40), fill=(200, 20, 20))
    # the labels (what a human reads, and what OCR would find)
    for n, label, rect in CANDIDATES:
        d.text((rect[0] + 56, rect[1] + 6), label, font=font(36), fill=(30, 30, 30))
    img.save(path)
    print(f"wrote {path}  {img.size}  {len(CANDIDATES)} numbered candidates")
    for n, label, rect in CANDIDATES:
        print(f"   #{n} {label:12s} {rect}  centre=({(rect[0]+rect[2])//2},{(rect[1]+rect[3])//2})")


if __name__ == "__main__":
    default = str(pathlib.Path(__file__).resolve().parent.parent / "cases" / "som.png")
    args = sys.argv[1:]
    bare = "--bare" in args
    args = [a for a in args if a != "--bare"]
    main(args[0] if args else default, bare=bare)
