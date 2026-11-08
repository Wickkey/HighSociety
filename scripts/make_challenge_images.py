"""
Draws the social preview for each challenge link (/challenge/<bot>): the bot's
picture and "Can you defeat <name>?" on the site's green, 1200x630, into
highsociety/web/static/og/challenge-<key>.png. Run again after adding or
changing an Insane bot's picture, and commit the images.

    python -m scripts.make_challenge_images [--font path/to/bold.ttf]
"""
import argparse
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from highsociety.code.common import bot_roster

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "highsociety", "web", "static")
OUT_DIR = os.path.join(STATIC, "og")
SIZE = (1200, 630)
GREEN_TOP, GREEN_BOTTOM = (16, 74, 54), (10, 50, 38)
GOLD = (200, 160, 77)
CREAM = (246, 241, 230)
MUTED = (178, 196, 186)
AVATAR = 360
FONT_CANDIDATES = [
    "/System/Library/Fonts/Avenir Next.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font(path, size, index=0):
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError:
        return ImageFont.load_default(size)


def background():
    img = Image.new("RGB", SIZE)
    draw = ImageDraw.Draw(img)
    for y in range(SIZE[1]):
        t = y / (SIZE[1] - 1)
        draw.line([(0, y), (SIZE[0], y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(GREEN_TOP, GREEN_BOTTOM)))
    return img


def avatar_disc(path):
    pic = Image.open(path).convert("RGB")
    side = min(pic.size)
    pic = pic.crop(((pic.width - side) // 2, (pic.height - side) // 2,
                    (pic.width + side) // 2, (pic.height + side) // 2)).resize((AVATAR, AVATAR), Image.LANCZOS)
    mask = Image.new("L", (AVATAR * 4, AVATAR * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, AVATAR * 4 - 1, AVATAR * 4 - 1), fill=255)
    return pic, mask.resize((AVATAR, AVATAR), Image.LANCZOS)


def fit(draw, text, path, start, max_width, index):
    size = start
    while size > 30:
        f = font(path, size, index)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 4
    return font(path, size, index)


def draw_card(bot, picture, font_path):
    img = background()
    draw = ImageDraw.Draw(img)
    x0, cy = 110, SIZE[1] // 2
    # gold ring with a soft shadow
    shadow = Image.new("L", SIZE, 0)
    ImageDraw.Draw(shadow).ellipse((x0 - 8, cy - AVATAR // 2 + 10, x0 + AVATAR + 8, cy + AVATAR // 2 + 26), fill=120)
    img.paste((4, 30, 22), (0, 0), shadow.filter(ImageFilter.GaussianBlur(18)))
    draw.ellipse((x0 - 10, cy - AVATAR // 2 - 10, x0 + AVATAR + 10, cy + AVATAR // 2 + 10), fill=GOLD)
    pic, mask = avatar_disc(picture)
    img.paste(pic, (x0, cy - AVATAR // 2), mask)

    tx = x0 + AVATAR + 70
    width = SIZE[0] - tx - 70
    small = font(font_path, 34, index=2)   # Avenir Next: 2 = Demi Bold
    draw.text((tx, cy - 150), "CAN YOU DEFEAT", font=small, fill=GOLD)
    name = fit(draw, f"{bot.name}?", font_path, 92, width, index=0)  # 0 = Bold
    draw.text((tx, cy - 100), f"{bot.name}?", font=name, fill=CREAM)
    draw.line([(tx, cy + 30), (tx + 120, cy + 30)], fill=GOLD, width=4)
    body = font(font_path, 30, index=5)  # 5 = Medium
    draw.text((tx, cy + 60), "An Insane bot, trained by playing itself.", font=body, fill=MUTED)
    draw.text((tx, cy + 104), "High Society · free in your browser", font=body, fill=CREAM)
    return img


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", default=next((p for p in FONT_CANDIDATES if os.path.exists(p)), ""))
    args = parser.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    folder = os.path.join(STATIC, "avatars", "bots")
    for bot in bot_roster.BOT_ROSTER:
        if bot.difficulty != "insane":
            continue
        picture = next((os.path.join(folder, bot.key + ext) for ext in (".webp", ".png", ".jpg", ".jpeg")
                        if os.path.exists(os.path.join(folder, bot.key + ext))), None)
        if picture is None:
            print(f"skip {bot.key}: no picture")
            continue
        out = os.path.join(OUT_DIR, f"challenge-{bot.key}.png")
        draw_card(bot, picture, args.font).save(out, optimize=True)
        print(f"wrote {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
