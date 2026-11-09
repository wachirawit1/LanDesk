"""Generate multi-resolution Windows .ico files for LanDesk, Setup, and Uninstaller."""
from pathlib import Path
from PIL import Image, ImageDraw

root = Path.cwd()


def draw_base_icon(size, bg_top, bg_bottom, accent_color, badge_kind=None):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = max(1, size // 16)
    radius = max(3, size // 5)
    x0, y0, x1, y1 = pad, pad, size - pad - 1, size - pad - 1

    # Draw rounded squircle background with vertical gradient
    mask = Image.new('L', (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=255)

    grad = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(grad)
    for y in range(y0, y1 + 1):
        t = (y - y0) / max(1, (y1 - y0))
        r = int(bg_top[0] * (1 - t) + bg_bottom[0] * t)
        g = int(bg_top[1] * (1 - t) + bg_bottom[1] * t)
        b = int(bg_top[2] * (1 - t) + bg_bottom[2] * t)
        grad_draw.line([(x0, y), (x1, y)], fill=(r, g, b, 255))

    img.paste(grad, (0, 0), mask)

    # Subtle inner border
    border_w = max(1, size // 32)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, outline=(255, 255, 255, 70), width=border_w)

    # Draw Monitor frame inside the icon
    mx0 = int(size * 0.19)
    my0 = int(size * 0.21)
    mx1 = int(size * 0.81)
    my1 = int(size * 0.67)
    m_rad = max(2, size // 18)
    frame_w = max(2, size // 16)

    # Monitor screen fill
    draw.rounded_rectangle([mx0, my0, mx1, my1], radius=m_rad, fill=(17, 36, 48, 235), outline=(255, 255, 255, 245), width=frame_w)

    # Monitor stand neck & base
    cx = size // 2
    stand_top = my1
    stand_bot = int(size * 0.78)
    neck_w = max(2, size // 12)
    draw.rectangle([cx - neck_w // 2, stand_top, cx + neck_w // 2, stand_bot], fill=(255, 255, 255, 245))
    base_w = int(size * 0.36)
    base_h = max(2, size // 18)
    draw.rounded_rectangle([cx - base_w // 2, stand_bot - base_h // 2, cx + base_w // 2, stand_bot + base_h], radius=max(1, base_h // 2), fill=(255, 255, 255, 245))

    # Draw AnyDesk-style bidirectional remote connection arrows inside the monitor screen
    sx = (mx0 + mx1) // 2
    sy = (my0 + my1) // 2
    span = max(3, int((mx1 - mx0) * 0.28))
    stroke = max(2, size // 18)

    # Left-pointing and right-pointing chevrons / remote link symbol
    # Left diamond/arrow in bright cyan/mint
    draw.line([(sx - span, sy), (sx - span // 4, sy - span // 2)], fill=accent_color, width=stroke)
    draw.line([(sx - span, sy), (sx - span // 4, sy + span // 2)], fill=accent_color, width=stroke)
    # Right diamond/arrow in white
    draw.line([(sx + span, sy), (sx + span // 4, sy - span // 2)], fill=(255, 255, 255, 255), width=stroke)
    draw.line([(sx + span, sy), (sx + span // 4, sy + span // 2)], fill=(255, 255, 255, 255), width=stroke)
    # Center connection dot
    dot_r = max(1, size // 22)
    draw.ellipse([sx - dot_r, sy - dot_r, sx + dot_r, sy + dot_r], fill=accent_color)

    # Optional corner badge for Setup (install arrow) or Uninstall (X)
    if badge_kind in ('setup', 'uninstall'):
        br = int(size * 0.22)
        bx = int(size * 0.76)
        by = int(size * 0.76)
        badge_bg = (0, 150, 136, 255) if badge_kind == 'setup' else (211, 47, 47, 255)
        draw.ellipse([bx - br, by - br, bx + br, by + br], fill=badge_bg, outline=(255, 255, 255, 245), width=max(1, size // 28))
        bw = max(2, size // 20)
        if badge_kind == 'setup':
            # Downward install arrow
            draw.line([(bx, by - int(br * 0.55)), (bx, by + int(br * 0.45))], fill=(255, 255, 255, 255), width=bw)
            draw.line([(bx - int(br * 0.45), by + int(br * 0.05)), (bx, by + int(br * 0.50))], fill=(255, 255, 255, 255), width=bw)
            draw.line([(bx + int(br * 0.45), by + int(br * 0.05)), (bx, by + int(br * 0.50))], fill=(255, 255, 255, 255), width=bw)
        else:
            # X mark
            d = int(br * 0.42)
            draw.line([(bx - d, by - d), (bx + d, by + d)], fill=(255, 255, 255, 255), width=bw)
            draw.line([(bx - d, by + d), (bx + d, by - d)], fill=(255, 255, 255, 255), width=bw)

    return img


def save_ico(target_path, bg_top, bg_bottom, accent_color, badge_kind=None):
    sizes = [256, 128, 64, 48, 32, 16]
    master = draw_base_icon(256, bg_top, bg_bottom, accent_color, badge_kind=badge_kind)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    master.save(
        target_path,
        format='ICO',
        sizes=[(s, s) for s in sizes],
    )
    print(f'Generated {target_path} ({target_path.stat().st_size} bytes)')


if __name__ == '__main__':
    save_ico(root / 'LanDesk/landesk.ico', (0, 137, 123), (0, 77, 64), (100, 255, 218), badge_kind=None)
    save_ico(root / 'work/setup.ico', (2, 136, 209), (0, 96, 100), (128, 222, 234), badge_kind='setup')
    save_ico(root / 'work/uninstall.ico', (198, 40, 40), (127, 0, 0), (255, 138, 128), badge_kind='uninstall')
