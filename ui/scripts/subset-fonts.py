"""
字体本地子集化（策划书 §4.3 / §7 预算：字体 ≤ 600KB）。

源文件不进仓库：先下载到临时目录，再用 fonttools 实例化 + 子集化。
用法：
  python scripts/subset-fonts.py [--src <源目录>] [--out <输出目录>]

源目录需包含（Google Fonts 仓库 ofl/ 下的 OFL 字体）：
  CormorantGaramond[wght].ttf
  IBMPlexSans[wdth,wght].ttf
  IBMPlexMono-Regular.ttf
  NotoSerifSC[wght].ttf
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

HERE = os.path.dirname(os.path.abspath(__file__))
UI_ROOT = os.path.dirname(HERE)
SRC_ROOT = os.path.join(UI_ROOT, "src")

# ASCII 全量 + 中文标点/箭头/编号（界面静态文案里出现的都从这里兜底）
EXTRA_CODEPOINTS = list(range(0x20, 0x7F)) + [
    0x00B0,  # °
    0x00B7,  # ·
    0x2014,  # —
    0x2018,
    0x2019,
    0x201C,
    0x201D,
    0x2026,  # …
    0x2192,  # →
    0x2460,
    0x2461,
    0x2462,
    0x2463,
    0x2464,  # ①–⑤
    0x3001,
    0x3002,  # 、。
    0x300C,
    0x300D,
    0x300E,
    0x300F,  # 「」『』
    0xFF08,
    0xFF09,
    0xFF0C,
    0xFF1A,
    0xFF1B,
]


def collect_chars() -> set[str]:
    chars: set[str] = set()
    for path in glob.glob(os.path.join(SRC_ROOT, "**", "*.*"), recursive=True):
        if path.endswith((".vue", ".ts", ".json")):
            with open(path, encoding="utf-8") as f:
                chars.update(f.read())
    return chars


def codepoints(chars: set[str]) -> list[int]:
    cps = {ord(c) for c in chars if c != "\n" and c != "\r" and c != "\t"}
    cps.update(EXTRA_CODEPOINTS)
    return sorted(cps)


def subset_font(src: str, out: str, cps: list[int], axes: dict[str, float] | None) -> int:
    font = TTFont(src)
    if axes:
        instantiateVariableFont(font, axes, inplace=True)

    options = subset.Options()
    options.flavor = "woff2"
    options.hinting = False
    options.desubroutinize = True
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.recalc_bounds = True

    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=cps)
    subsetter.subset(font)

    subset.save_font(font, out, options)
    font.close()
    return os.path.getsize(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=os.path.join(os.environ.get("TEMP", "/tmp"), "ars-fonts"))
    parser.add_argument("--out", default=os.path.join(SRC_ROOT, "assets", "fonts"))
    args = parser.parse_args()

    chars = collect_chars()
    cps = codepoints(chars)
    cjk = [c for c in chars if "\u4e00" <= c <= "\u9fff"]
    print(f"字符集：{len(cps)} 个码位，其中汉字 {len(cjk)} 个")

    os.makedirs(args.out, exist_ok=True)

    jobs = [
        ("CormorantGaramond[wght].ttf", "cormorant-garamond-400.woff2", {"wght": 400}),
        ("IBMPlexSans[wdth,wght].ttf", "ibm-plex-sans-400.woff2", {"wght": 400, "wdth": 100}),
        ("IBMPlexSans[wdth,wght].ttf", "ibm-plex-sans-500.woff2", {"wght": 500, "wdth": 100}),
        ("IBMPlexMono-Regular.ttf", "ibm-plex-mono-400.woff2", None),
        ("NotoSerifSC[wght].ttf", "noto-serif-sc-400.woff2", {"wght": 400}),
    ]

    total = 0
    for src_name, out_name, axes in jobs:
        src = os.path.join(args.src, src_name)
        if not os.path.exists(src):
            print(f"缺少源文件：{src}", file=sys.stderr)
            return 1
        size = subset_font(src, os.path.join(args.out, out_name), cps, axes)
        total += size
        print(f"  {out_name:32s} {size / 1024:7.1f} KB")

    print(f"合计 {total / 1024:.1f} KB")
    if total > 600 * 1024:
        print("超出 600KB 预算", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
