"""Kindle 4 dashboard image server (fake data for now).

Run:  python server.py
The Kindle downloads  http://<laptop-ip>:8000/dash.png  (600x800, 8-bit grayscale).
The design is drawn in landscape (800x600) and rotated for the Kindle's portrait screen.
"""
import bisect
import io
import json
import math
import os
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


MAP_BOX = (510, 160, 766, 386)   # area inside the map card where the track is drawn
MAP_REGION = (494, 148, 780, 396)   # part of the card redrawn with smoothing (inside the card border)
SS = 4                              # supersampling: draw 4x bigger, then shrink = smooth edges
SMOOTH = 3.0                        # smoothing strength in points (0 = off)
TRACK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "track.json")


def _smooth_closed(pts, sigma):
    """Gaussian-smooth a closed loop of points (wraps around). Removes tracing jitter."""
    if sigma <= 0:
        return pts
    n, r = len(pts), int(sigma * 3)
    w = [math.exp(-(i * i) / (2 * sigma * sigma)) for i in range(-r, r + 1)]
    tot = sum(w)
    out = []
    for i in range(n):
        x = sum(w[j + r] * pts[(i + j) % n][0] for j in range(-r, r + 1)) / tot
        y = sum(w[j + r] * pts[(i + j) % n][1] for j in range(-r, r + 1)) / tot
        out.append((x, y))
    return out


def _load_track():
    """Load track.json, fit it into MAP_BOX, and prepare it for progress lookups."""
    try:
        with open(TRACK_FILE) as f:
            raw = json.load(f)["points"]
    except (OSError, KeyError, ValueError):
        return None
    raw = _smooth_closed([tuple(p) for p in raw], SMOOTH)
    xs = [p[0] for p in raw]
    ys = [p[1] for p in raw]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    bx0, by0, bx1, by1 = MAP_BOX
    k = min((bx1 - bx0) / (x1 - x0), (by1 - by0) / (y1 - y0))
    ox = bx0 + ((bx1 - bx0) - k * (x1 - x0)) / 2 - k * x0     # centre in the box
    oy = by0 + ((by1 - by0) - k * (y1 - y0)) / 2 - k * y0
    pts = [(ox + k * x, oy + k * y) for x, y in raw]
    pts.append(pts[0])                                        # close the loop
    dist = [0.0]
    for a, b in zip(pts, pts[1:]):
        dist.append(dist[-1] + math.dist(a, b))
    return pts, dist


TRACK = _load_track()


def track_point(u):
    """Point on the track for progress u (0..1, 0 = start/finish). Uses distance, so speed looks even."""
    if TRACK is None:                                         # fallback: old placeholder oval
        a = 2 * math.pi * u
        r = 1 + 0.16 * math.cos(2 * a + 0.6) + 0.09 * math.sin(3 * a)
        return 637 + 100 * r * math.cos(a), 268 + 78 * r * math.sin(a)
    pts, dist = TRACK
    d = (u % 1.0) * dist[-1]
    i = min(max(bisect.bisect_right(dist, d) - 1, 0), len(pts) - 2)
    seg = dist[i + 1] - dist[i]
    f = (d - dist[i]) / seg if seg else 0.0
    return (pts[i][0] + f * (pts[i + 1][0] - pts[i][0]),
            pts[i][1] + f * (pts[i + 1][1] - pts[i][1]))


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
    # Track is drawn 4x bigger in its own layer, then shrunk: smooth, anti-aliased lines.
    rx0, ry0, rx1, ry1 = MAP_REGION
    layer = Image.new("L", ((rx1 - rx0) * SS, (ry1 - ry0) * SS), 255)
    ld = ImageDraw.Draw(layer)

    def L(p):
        return ((p[0] - rx0) * SS, (p[1] - ry0) * SS)

    pts = TRACK[0] if TRACK else [track_point(i / 120) for i in range(121)]
    lp = [L(p) for p in pts]
    road = 8 * SS
    ld.line(lp, fill=0, width=road, joint="curve")
    for p in (lp[0], lp[-1]):                        # round the ends of the line
        ld.ellipse((p[0] - road / 2, p[1] - road / 2, p[0] + road / 2, p[1] + road / 2), fill=0)
    sx, sy = L(track_point(0))
    sx2, sy2 = L(track_point(0.004))                 # direction of travel at the line
    n = math.hypot(sx2 - sx, sy2 - sy) or 1.0
    nx, ny = -(sy2 - sy) / n, (sx2 - sx) / n         # perpendicular
    ld.line((sx - 9 * SS * nx, sy - 9 * SS * ny, sx + 9 * SS * nx, sy + 9 * SS * ny), fill=0, width=4 * SS)
    px, py = L(track_point(lap))
    ld.ellipse((px - 14 * SS, py - 14 * SS, px + 14 * SS, py + 14 * SS), fill=255)   # halo
    ld.ellipse((px - 9 * SS, py - 9 * SS, px + 9 * SS, py + 9 * SS), fill=0)         # car
    img.paste(layer.resize((rx1 - rx0, ry1 - ry0), Image.LANCZOS), (rx0, ry0))

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
