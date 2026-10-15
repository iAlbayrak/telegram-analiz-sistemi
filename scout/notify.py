"""Telegram image-card notifications with a plain-text fallback."""
from __future__ import annotations

from io import BytesIO


def _card_image(text: str) -> BytesIO:
    """Render a readable image card without relying on Telegram Markdown."""
    from PIL import Image, ImageDraw, ImageFont

    width, margin = 1080, 54
    max_text_width = width - margin * 2
    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font = ImageFont.truetype(font_path, 29)
        title_font = ImageFont.truetype(font_path, 25)
    except OSError:
        font = ImageFont.load_default()
        title_font = font

    probe = Image.new("RGB", (width, 100), "#101827")
    probe_draw = ImageDraw.Draw(probe)
    lines = []
    for raw in text.splitlines():
        if not raw:
            lines.append("")
            continue
        current = ""
        for word in raw.split():
            candidate = word if not current else current + " " + word
            if probe_draw.textbbox((0, 0), candidate, font=font)[2] <= max_text_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)

    line_height = 43
    height = min(max(360, 150 + len(lines) * line_height), 7800)
    image = Image.new("RGB", (width, height), "#101827")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=28,
                           fill="#172338", outline="#34445f", width=2)
    draw.rounded_rectangle((margin, 42, width - margin, 96), radius=16, fill="#243652")
    draw.text((margin + 18, 53), "SCOUT V2  •  PİYASA ANALİZİ  •  EMİR GÖNDERMEZ",
              font=title_font, fill="#d8e5f7")

    y = 126
    for line in lines:
        if y + line_height > height - 48:
            draw.text((margin, y), "… kartta kısaltıldı; metin yedeğine bakın.",
                      font=title_font, fill="#f6c76a")
            break
        color = "#e8eef8"
        if "LONG" in line or "🟢" in line:
            color = "#75e0ad"
        elif "SHORT" in line or "🔴" in line:
            color = "#ff9292"
        elif line.startswith("Stop:"):
            color = "#ffb4a8"
        elif line.startswith("TP1:"):
            color = "#8fd6ff"
        draw.text((margin, y), line, font=font, fill=color)
        y += line_height

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def split_message(text: str, limit: int = 3800):
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def send_telegram(text: str, token: str, chat_id: str) -> bool:
    import requests

    try:
        image = _card_image(text)
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": "Scout v2 · saatlik kırılım analizi"},
            files={"photo": ("scout-card.png", image, "image/png")},
            timeout=30,
        )
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code == 200 and body.get("ok", False):
            return True
        print(f"TELEGRAM KART HATASI {response.status_code}: {str(body or response.text)[:200]}")
    except Exception as exc:
        print(f"TELEGRAM KART GÖNDERİM HATASI: {type(exc).__name__}: {str(exc)[:160]}")

    ok = True
    for part in split_message(text):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": part.rstrip(), "disable_web_page_preview": True},
                timeout=20,
            )
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code != 200 or not body.get("ok", False):
                print(f"TELEGRAM HATASI {r.status_code}: {str(body or r.text)[:200]}")
                ok = False
        except Exception as exc:
            print(f"TELEGRAM GÖNDERİM HATASI: {type(exc).__name__}: {str(exc)[:160]}")
            ok = False
    return ok
