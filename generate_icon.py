"""生成 GitLab 快捷工具的应用图标"""
from PIL import Image, ImageDraw, ImageFont
import math
import os


def draw_merge_icon(draw, cx, cy, size, color):
    """绘制合并图标：两个分支汇聚成一个"""
    s = size
    # 分支线宽
    lw = max(int(s * 0.08), 2)
    # 圆点半径
    dot_r = max(int(s * 0.055), 2)

    # 左分支起点 (左上)
    lx = cx - s * 0.28
    ly = cy - s * 0.30
    # 右分支起点 (右上)
    rx = cx + s * 0.28
    ry = cy - s * 0.30
    # 汇合点 (下方)
    mx = cx
    my = cy + s * 0.30

    # 左分支线
    draw.line([(lx, ly), (mx, my)], fill=color, width=lw)
    # 右分支线
    draw.line([(rx, ry), (mx, my)], fill=color, width=lw)

    # 左分支起点圆点
    draw.ellipse([lx - dot_r, ly - dot_r, lx + dot_r, ly + dot_r], fill=color)
    # 右分支起点圆点
    draw.ellipse([rx - dot_r, ry - dot_r, rx + dot_r, ry + dot_r], fill=color)
    # 汇合点圆点
    draw.ellipse([mx - dot_r, my - dot_r, mx + dot_r, my + dot_r], fill=color)

    # 汇合点向下的箭头
    arrow_y = my + s * 0.12
    arrow_w = s * 0.10
    draw.line([(mx, my), (mx, arrow_y)], fill=color, width=lw)
    draw.polygon([
        (mx, arrow_y + s * 0.06),
        (mx - arrow_w, arrow_y - s * 0.02),
        (mx + arrow_w, arrow_y - s * 0.02),
    ], fill=color)


def generate_icon(output_path='app.ico'):
    # GitLab 橙色
    bg_color = (252, 109, 38)
    # 背景渐变底色（稍深的橙色）
    bg_dark = (220, 80, 20)
    white = (255, 255, 255)

    # 生成多尺寸用于 ICO
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = []

    for s in sizes:
        img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        margin = max(int(s * 0.04), 1)
        radius = max(int(s * 0.18), 2)

        # 绘制圆角矩形背景
        x0, y0 = margin, margin
        x1, y1 = s - margin, s - margin
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=bg_color)

        # 绘制顶部高光（半透明白色渐变模拟）
        highlight_h = int(s * 0.4)
        for row in range(highlight_h):
            alpha = int(40 * (1 - row / highlight_h))
            for col in range(x0 + radius, x1 - radius):
                draw.point((col, y0 + radius + row), fill=(255, 255, 255, alpha))

        # 绘制合并图标
        icon_margin = s * 0.15
        draw_merge_icon(draw, s / 2, s / 2, s * 0.55, white)

        images.append(img)

    # 保存为 ICO
    images[-1].save(
        output_path,
        format='ICO',
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1]
    )
    print(f'图标已生成: {os.path.abspath(output_path)}')


if __name__ == '__main__':
    generate_icon()
