# -*- coding: utf-8 -*-
"""Генератор медиа для первой тестовой рассылки.

Постер в стиле референса (чёрный фон, светлая антиква с крупным трекингом,
серифные цифры через пробел, тонкий разделитель) и короткая анимация:
буквы заголовка проявляются по одной, кристалл «дышит» и подсвечивается.

    python media_crystal.py still  out.png [--figures] [--title КРИСТАЛЛ]
    python media_crystal.py frames work_dir            # кадры 680x1080 PNG
    python media_crystal.py video  out.mp4             # кадры -> x264
"""
from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets" / "broadcast"
CRYSTAL = ASSETS / "crystal_cut.png"

W, H = 680, 1080
SS = 4                                  # супер-сэмплинг
FONT = "C:/Windows/Fonts/GeorgiaPro-Light.ttf"
NUM = "C:/Windows/Fonts/constan.ttf"

INK = (243, 247, 255)
DIM = (150, 163, 186)
RULE = (176, 189, 212)

TITLE_PX = 84                           # кегль заголовка на канве 680 px
TITLE_WIDTH = 0.80                      # доля ширины канвы; трекинг добирается под неё
TRACK_NUM = 9
OUTLINE = 1.4                           # против волосяного шрифта на тёмном


def serif(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size)


def numerals(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(NUM, size)


def background(size) -> Image.Image:
    """Почти чёрный фон с чуть холодным свечением сверху — как в референсе."""
    w, h = size
    img = Image.new("RGB", size, (0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(120):
        t = 1 - i / 120
        d.ellipse([w * 0.5 - w * 0.8 * t, -h * 0.6 * t,
                   w * 0.5 + w * 0.8 * t, h * 0.6 * t],
                  fill=(int(9 * t), int(11 * t), int(16 * t)))
    return img.filter(ImageFilter.GaussianBlur(70))


def draw_tracked(d: ImageDraw.ImageDraw, x: float, y: float, text: str, fnt,
                 fill, track: float, width: float = 0.0,
                 center: bool = False) -> float:
    """Текст с трекингом; center=True — x это центр строки. Возвращает ширину."""
    widths = [d.textlength(ch, font=fnt) for ch in text]
    total = sum(widths) + track * max(len(text) - 1, 0)
    if center:
        x = x - total / 2
    step = width / SS if width else 0.0
    for ch, cw in zip(text, widths):
        if ch.strip():
            d.text((x, y), ch, font=fnt, fill=fill,
                   stroke_width=int(round(step)), stroke_fill=fill)
        x += cw + track
    return total


# Боковые просветы глифов GeorgiaPro-Light при кегле 200, px: (слева, справа).
# Нужны, чтобы просветы между буквами были равными на глаз: у «Т» почти нет
# воздуха в глифе, у «И» — 11 px.
SIDE_BEARING = {
    "К": (11.0, -1.8), "Р": (9.0, 7.6), "И": (11.0, 10.8), "С": (8.0, 4.0),
    "Т": (1.0, 1.2), "А": (-4.0, -3.6), "Л": (-1.0, 10.8), "М": (6.0, 9.6),
    "E": (9.0, 4.8), "H": (11.0, 11.4), "Н": (11.0, 11.4),
}
BEARING_BASE = 200.0


def join_gaps(letters, widths, gap_ratio: float = 0.13) -> list[float]:
    """Просвет между соседними буквами, одинаковый на глаз.

    Шрифт уже даёт часть воздуха (боковые просветы глифов, в пропорции к кеглю);
    оставшееся добирается до постоянной доли кегля.
    """
    gaps = []
    for i in range(len(letters) - 1):
        left, right = letters[i], letters[i + 1]
        lb = SIDE_BEARING.get(left, (0.0, 0.0))
        rb = SIDE_BEARING.get(right, (0.0, 0.0))
        k = widths[i] / BEARING_BASE
        natural = (lb[1] + rb[0]) * k
        gaps.append(max(0.0, gap_ratio * widths[i] - natural))
    return gaps


def soften_bottom(cr: Image.Image, fade_ratio: float = 0.11) -> Image.Image:
    """Плавно гасит альфу у нижней кромки, чтобы не было ножа-среза."""
    w, h = cr.size
    fade = max(1, int(h * fade_ratio))
    mask = Image.new("L", (w, h), 255)
    md = ImageDraw.Draw(mask)
    for y in range(fade):
        v = int(255 * (y / fade) ** 1.6)
        md.line([(0, h - fade + y), (w, h - fade + y)], fill=v)
    alpha = cr.split()[3].point(lambda a: a)
    cr = cr.copy()
    cr.putalpha(ImageChops.multiply(alpha, mask))
    return cr


def layout(d: ImageDraw.ImageDraw, letters, fnt, width_target: float) -> tuple[list[float], list[float]]:
    """Ширины букв и просветы, растянутые ровно до width_target."""
    widths = [d.textlength(ch, font=fnt) for ch in letters]
    gaps = join_gaps(letters, widths)
    span = sum(widths) + sum(gaps)
    extra = max(0.0, width_target - span) / max(len(letters) - 1, 1)
    return widths, [g + extra for g in gaps]


def render(title: str = "КРИСТАЛЛ", figures: bool = True, reveal: float = 1.0,
           crystal_scale: float = 1.0, glow: float = 1.0, alpha_in: float = 1.0,
           bg_pulse: float = 0.0, crystal_shift: float = 0.0) -> Image.Image:
    """Один кадр 680x1080. reveal 0..1 — доля проявившихся букв заголовка."""
    w, h = W * SS, H * SS
    canvas = background((w, h)).convert("RGBA")

    # ---- кристалл --------------------------------------------------------
    if CRYSTAL.exists() and alpha_in > 0:
        cr = Image.open(CRYSTAL).convert("RGBA")
        cw = int(w * 0.80 * crystal_scale)
        cr = cr.resize((cw, int(cr.height * cw / cr.width)), Image.LANCZOS)
        # нижний край снимка срезан по кристаллу — растворяем его в фоне
        cr = soften_bottom(cr, fade_ratio=0.11)
        cx = (w - cr.width) // 2
        cy = int(h * 0.590 - cr.height * 0.5) + int(crystal_shift * SS)
        halo = cr.filter(ImageFilter.GaussianBlur(16 * SS))
        halo.putalpha(halo.split()[3].point(lambda a: int(a * 0.45 * glow)))
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.alpha_composite(halo, (cx, cy))
        layer.alpha_composite(cr, (cx, cy))
        if alpha_in < 1:
            layer.putalpha(layer.split()[3].point(lambda a: int(a * alpha_in)))
        canvas.alpha_composite(layer)

    if bg_pulse > 0:  # мягкая пульсация свечения в глубине
        pulse = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        pd = ImageDraw.Draw(pulse)
        r = int(w * 0.40 * (1 + 0.05 * bg_pulse))
        pd.ellipse([w / 2 - r, h * 0.56 - r * 0.85, w / 2 + r, h * 0.56 + r * 0.85],
                   fill=(72, 94, 132, int(14 * bg_pulse)))
        canvas.alpha_composite(pulse.filter(ImageFilter.GaussianBlur(50 * SS)))
    d = ImageDraw.Draw(canvas)

    # ---- заголовок -------------------------------------------------------
    title_font = serif(int(TITLE_PX * SS))
    letters = list(title)
    shown = int(round(len(letters) * max(0.0, min(reveal, 1.0))))
    widths, gaps = layout(d, letters, title_font, w * TITLE_WIDTH)
    x = (w - (sum(widths) + sum(gaps))) / 2
    y_title = int(h * 0.086)
    for i, ch in enumerate(letters):
        if i < shown:
            d.text((x, y_title), ch, font=title_font, fill=INK,
                   stroke_width=int(round(OUTLINE * SS)), stroke_fill=INK)
        x += widths[i] + (gaps[i] if i < len(gaps) else 0.0)

    # ---- разделитель и цены ---------------------------------------------
    if figures:
        rule_y = y_title + title_font.getbbox("А")[3] + int(26 * SS)
        d.line([(w / 2 - 82 * SS, rule_y), (w / 2 + 82 * SS, rule_y)],
               fill=RULE, width=max(1, SS))
        num_font = numerals(int(46 * SS))
        top_font = numerals(int(33 * SS))
        y_top = rule_y + int(24 * SS)
        y_big = y_top + int(52 * SS)
        col_l, col_r = w * 0.275, w * 0.725

        draw_tracked(d, col_l, y_top, "0,5", top_font, DIM, TRACK_NUM * SS, OUTLINE * SS, center=True)
        draw_tracked(d, col_l, y_big, "1200", num_font, INK, TRACK_NUM * SS, OUTLINE * SS, center=True)
        draw_tracked(d, col_r, y_top, "1", top_font, DIM, TRACK_NUM * SS, OUTLINE * SS, center=True)
        draw_tracked(d, col_r, y_big, "2000", num_font, INK, TRACK_NUM * SS, OUTLINE * SS, center=True)

        # третья позиция — вторая строка: вертикальный разделитель остаётся
        # между первым рядом и этим, так что сетка читается как 2 x 2.
        y_third_top = y_big + int(96 * SS)
        d.line([(w / 2, y_top + 4 * SS), (w / 2, y_third_top - 12 * SS)],
               fill=(122, 135, 158), width=max(1, SS))
        draw_tracked(d, w / 2, y_third_top, "2", top_font, DIM, TRACK_NUM * SS, OUTLINE * SS, center=True)
        draw_tracked(d, w / 2, y_third_top + int(52 * SS), "3500", num_font, INK, TRACK_NUM * SS, OUTLINE * SS, center=True)

    return canvas.convert("RGB").resize((W, H), Image.LANCZOS)


def timeline(n: int = 72):
    """Покадровые параметры: буквы по одной, кристалл «дышит» и чуть парит."""
    for i in range(n):
        t = i / (n - 1)
        reveal = min(1.0, max(0.0, (t - 0.12) / 0.42))
        alpha_in = min(1.0, max(0.0, (t - 0.02) / 0.30))
        breath = 0.5 - 0.5 * math.cos(2 * math.pi * t * 2)
        yield {
            "reveal": reveal,
            "crystal_scale": 1.0 + 0.010 * breath,
            "crystal_shift": 2.0 * breath - 1.0,
            "glow": 0.80 + 0.30 * breath,
            "alpha_in": alpha_in,
            "bg_pulse": breath,
        }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("still")
    s.add_argument("out")
    s.add_argument("--title", default="КРИСТАЛЛ")
    s.add_argument("--figures", action="store_true")
    f = sub.add_parser("frames")
    f.add_argument("dir")
    f.add_argument("--count", type=int, default=72)
    v = sub.add_parser("video")
    v.add_argument("out")
    v.add_argument("--count", type=int, default=72)
    v.add_argument("--fps", type=int, default=24)
    args = ap.parse_args(argv)

    if args.cmd == "still":
        img = render(args.title, figures=args.figures)
        img.save(args.out)
        print(f"saved {args.out} {img.size}")
        return 0

    if args.cmd == "frames":
        out = Path(args.dir)
        out.mkdir(parents=True, exist_ok=True)
        for i, params in enumerate(timeline(args.count)):
            render(**params).save(out / f"f{i:04d}.png")
        print(f"frames: {args.count} -> {out}")
        return 0

    ff = shutil.which("ffmpeg")
    if not ff:
        print("ffmpeg не найден в PATH", file=sys.stderr)
        return 2
    work = HERE / "assets" / "broadcast" / "_frames"
    if work.exists():
        shutil.rmtree(work)
    main(["frames", str(work), "--count", str(args.count)])
    cmd = [ff, "-y", "-loglevel", "error", "-framerate", str(args.fps),
           "-i", str(work / "f%04d.png"), "-c:v", "libx264",
           "-profile:v", "high", "-pix_fmt", "yuv420p", "-crf", "21",
           "-movflags", "+faststart", "-an", args.out]
    subprocess.run(cmd, check=True)
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())