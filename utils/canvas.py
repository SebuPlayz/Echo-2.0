import io
import aiohttp

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = ImageDraw = ImageFont = ImageFilter = ImageOps = None


def _get_font(size: int, bold: bool = False):
    if not HAS_PIL:
        return None
    font_names = [
        "C:\\Windows\\Fonts\\segoeuib.ttf" if bold else "C:\\Windows\\Fonts\\segoeui.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]
    for fn in font_names:
        try:
            return ImageFont.truetype(fn, size)
        except Exception:
            continue
    return ImageFont.load_default()


async def fetch_image_bytes(url: str, timeout: float = 3.0) -> bytes | None:
    if not url or not str(url).startswith("http"):
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception:
        pass
    return None


def generate_music_banner(
    title: str,
    author: str,
    artwork_bytes: bytes = None,
    badge_text: str = "NOW PLAYING",
    bot_name: str = "ECHO",
    sub_info: str = "",
    progress_pct: float = 0.0
) -> io.BytesIO:
    """
    Generates a dynamic 900x300 Canvas Banner card for music views:
    - Dark blurred cover art background
    - Rounded cover art preview box
    - Bot name badge ("ECHO • NOW PLAYING" / "ECHO • MOST PLAYED TRACK")
    - Track title & author
    - Progress / metrics bar
    """
    if not HAS_PIL:
        return None

    width, height = 900, 300
    
    bg = Image.new("RGBA", (width, height), (18, 18, 26, 255))
    
    artwork_img = None
    if artwork_bytes:
        try:
            artwork_img = Image.open(io.BytesIO(artwork_bytes)).convert("RGBA")
        except Exception:
            artwork_img = None

    if artwork_img:
        # Create blurred background
        bg_art = artwork_img.resize((width, height))
        bg_art = bg_art.filter(ImageFilter.GaussianBlur(35))
        bg.paste(bg_art, (0, 0))
    
    # Overlay dark glassmorphism tint & subtle gradient
    overlay = Image.new("RGBA", (width, height), (10, 10, 16, 215))
    bg = Image.alpha_composite(bg, overlay)
    
    draw = ImageDraw.Draw(bg)
    
    # Fonts
    font_title = _get_font(28, bold=True)
    font_author = _get_font(20, bold=False)
    font_badge = _get_font(14, bold=True)
    font_sub = _get_font(15, bold=False)

    # Cover Art Box
    cover_size = 220
    cover_x, cover_y = 40, 40

    if artwork_img:
        cover_cropped = ImageOps.fit(artwork_img, (cover_size, cover_size), Image.Resampling.LANCZOS)
        mask = Image.new("L", (cover_size, cover_size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle([0, 0, cover_size, cover_size], radius=16, fill=255)
        
        bg.paste(cover_cropped, (cover_x, cover_y), mask)
        draw.rounded_rectangle([cover_x, cover_y, cover_x + cover_size, cover_y + cover_size], radius=16, outline=(255, 255, 255, 60), width=2)
    else:
        draw.rounded_rectangle([cover_x, cover_y, cover_x + cover_size, cover_y + cover_size], radius=16, fill=(30, 30, 42, 255), outline=(255, 255, 255, 40), width=2)
        draw.text((cover_x + 85, cover_y + 95), "MUSIC", font=font_badge, fill=(150, 150, 180, 255))

    text_x = cover_x + cover_size + 35
    curr_y = 42

    # Dynamic Badge: "ECHO • NOW PLAYING"
    badge_str = f"{bot_name.upper()}  •  {badge_text.upper()}"
    bbox = draw.textbbox((0, 0), badge_str, font=font_badge)
    text_w = bbox[2] - bbox[0]
    badge_w = text_w + 24
    badge_h = 26
    
    draw.rounded_rectangle([text_x, curr_y, text_x + badge_w, curr_y + badge_h], radius=6, fill=(88, 101, 242, 230))
    draw.text((text_x + 12, curr_y + 4), badge_str, font=font_badge, fill=(255, 255, 255, 255))
    
    curr_y += 42

    # Track Title
    disp_title = title
    if len(disp_title) > 38:
        disp_title = disp_title[:35] + "..."
    draw.text((text_x, curr_y), disp_title, font=font_title, fill=(255, 255, 255, 255))

    curr_y += 44

    # Artist Name
    disp_author = f"by {author}" if author else "Unknown Artist"
    if len(disp_author) > 45:
        disp_author = disp_author[:42] + "..."
    draw.text((text_x, curr_y), disp_author, font=font_author, fill=(180, 180, 205, 255))

    curr_y += 38

    # Sub Info / Details
    if sub_info:
        draw.text((text_x, curr_y), sub_info, font=font_sub, fill=(200, 200, 220, 255))
        curr_y += 26

    # Progress bar line if applicable
    bar_width = width - text_x - 45
    bar_height = 6
    draw.rounded_rectangle([text_x, curr_y, text_x + bar_width, curr_y + bar_height], radius=3, fill=(45, 45, 60, 255))
    
    pct = max(0.0, min(1.0, progress_pct))
    filled_w = int(bar_width * pct)
    if filled_w > 0:
        draw.rounded_rectangle([text_x, curr_y, text_x + filled_w, curr_y + bar_height], radius=3, fill=(88, 101, 242, 255))
        draw.ellipse([text_x + filled_w - 5, curr_y - 3, text_x + filled_w + 5, curr_y + bar_height + 3], fill=(255, 255, 255, 255))

    buf = io.BytesIO()
    bg.save(buf, format="PNG")
    buf.seek(0)
    return buf
