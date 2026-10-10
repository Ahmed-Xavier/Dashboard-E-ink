"""Kindle 4 dashboard image server (fake data for now).

Run:  python server.py
The Kindle downloads  http://<laptop-ip>:8000/dash.png  (600x800, 8-bit grayscale).
The design is drawn in landscape (800x600) and rotated for the Kindle's portrait screen.
"""
import io
import math
import random
import time
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageDraw, ImageFont

W, H = 800, 600   # design size (landscape)
ROTATE = 90       # 90 or 270: pick the one that is right-side-up for how the Kindle is mounted
PORT = 8000

frame = 0


@lru_cache(maxsize=None)
def font(size):
    for name in ("bahnschrift.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            f = ImageFont.truetype(name, size)
        except OSError:
            continue
        if name == "bahnschrift.ttf":
            try:
                f.set_variation_by_name("Bold")
            except Exception:
                pass
        return f
    return ImageFont.load_default()


def fake_data(t):
    """Smooth fake values so the layout can be judged. Replace with real data later."""
    rnd = random.Random(int(t))
    speed = 30 + 14 * math.sin(t / 9) + rnd.uniform(-1.5, 1.5)
    speed = max(0.0, min(59.9, speed))
    battery = 100 - (t / 3) % 100        # -1 % every 3 s, wraps around (shows the LOW alert)
    temp = 38 + 14 * math.sin(t / 25)    # 24..52 C (shows the HIGH alert)
    lap = (t / 40) % 1                   # position on the track, one lap per 40 s
    return speed, battery, temp, lap


def card(d, box, inverted=False):
    """Rounded card. Returns the ink colour to use inside it (white on black when inverted)."""
    d.rounded_rectangle(box, radius=14, outline=0, width=4, fill=0 if inverted else 255)
    return 255 if inverted else 0


def track_point(u, cx=637, cy=268, rx=100, ry=78):
    r = 1 + 0.16 * math.cos(2 * u + 0.6) + 0.09 * math.sin(3 * u)
    return cx + rx * r * math.cos(u), cy + ry * r * math.sin(u)


def render(rotate=ROTATE):
    global frame
    frame += 1
    t = time.time()
    speed, batt, temp, lap = fake_data(t)

    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)

    # ---- top bar -------------------------------------------------------
    d.text((16, 24), "SHELL ECO-MARATHON", font=font(26), fill=0, anchor="lm")
    d.text((400, 24), f"#{frame}", font=font(22), fill=0, anchor="mm")
    if frame % 2:                                   # heartbeat: flips on every refresh
        d.rectangle((610, 14, 630, 34), fill=0)
    else:
        d.rectangle((610, 14, 630, 34), outline=0, width=3)
    d.text((784, 24), time.strftime("%H:%M:%S"), font=font(26), fill=0, anchor="rm")
    d.line((16, 46, 784, 46), fill=0, width=3)

    # ---- speed bar: 24 rising segments ---------------------------------
    segs, gap = 24, 5
    sw = (768 - gap * (segs - 1)) / segs
    on = round(speed / 60 * segs)
    for i in range(segs):
        x0 = 16 + i * (sw + gap)
        h = 14 + 26 * i / (segs - 1)
        box = (x0, 100 - h, x0 + sw, 100)
        if i < on:
            d.rectangle(box, fill=0)
        else:
            d.rectangle(box, outline=0, width=2)

    # ---- big speed number ----------------------------------------------
    d.text((243, 262), f"{speed:.0f}", font=font(270), fill=0, anchor="mm")
    d.text((243, 418), "KM/H", font=font(44), fill=0, anchor="mm")

    # ---- battery card (inverts when low) -------------------------------
    low = batt < 20
    ink = card(d, (16, 466, 470, 584), low)
    d.text((32, 488), "BATTERY  LOW" if low else "BATTERY", font=font(22), fill=ink, anchor="lm")
    d.text((32, 538), f"{batt:.0f}%", font=font(76), fill=ink, anchor="lm")
    full = math.ceil(batt / 10)
    for i in range(10):
        x0 = 225 + i * 23
        box = (x0, 508, x0 + 18, 558)
        if i < full:
            d.rectangle(box, fill=ink)
        else:
            d.rectangle(box, outline=ink, width=2)

    # ---- temperature card (inverts when hot) ---------------------------
    hot = temp > 45
    ink = card(d, (490, 412, 784, 584), hot)
    d.text((506, 434), "TEMP  HIGH" if hot else "TEMP", font=font(22), fill=ink, anchor="lm")
    d.text((506, 488), f"{temp:.0f}\u00b0C", font=font(84), fill=ink, anchor="lm")
    frac = max(0.0, min(1.0, (temp - 20) / 40))     # bar scale: 20..60 C
    d.rectangle((506, 538, 768, 562), outline=ink, width=3)
    d.rectangle((509, 541, 509 + int(256 * frac), 559), fill=ink)

    # ---- map card: fake track + moving car -----------------------------
    d.rounded_rectangle((490, 112, 784, 400), radius=14, outline=0, width=4)
    d.text((506, 134), "TRACK", font=font(22), fill=0, anchor="lm")
    pts = [track_point(2 * math.pi * i / 120) for i in range(121)]
    d.line(pts, fill=0, width=16, joint="curve")    # road
    d.line(pts, fill=255, width=2, joint="curve")   # centre line
    sx, sy = track_point(0)
    d.line((sx - 14, sy, sx + 14, sy), fill=255, width=6)   # start / finish tick
    px, py = track_point(2 * math.pi * lap)
    d.ellipse((px - 20, py - 20, px + 20, py + 20), fill=255)   # halo
    d.ellipse((px - 13, py - 13, px + 13, py + 13), fill=0)     # car

    out = img.rotate(rotate, expand=True) if rotate else img
    buf = io.BytesIO()
    out.save(buf, "PNG", compress_level=1)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("favicon.ico"):
            self.send_error(404)
            return
        data = render()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Serving on http://0.0.0.0:{PORT}/dash.png  (Ctrl+C to stop)")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
