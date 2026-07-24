"""Render the brand assets (social card, home-screen icons, favicon) from HTML.

The card and icons are drawn with the same CSS the site uses, then screenshotted
with headless Chromium, so they stay pixel-identical to the real hero instead of
being hand-redrawn. Regenerate after a brand change:

    python scripts/make_brand_assets.py frontend
    python scripts/make_brand_assets.py <gh-pages checkout>

Needs: playwright (+ `playwright install chromium`) and Pillow. Neither is a
runtime dependency of the miner -- this is a build-time tool and the generated
PNGs are committed.
"""
import io
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

GOLD = "#f0b90b"
ORANGE = "#ff9f1c"
BG = "#0a0a0f"

# Lifted verbatim from the site hero so the card shows the same shiba.
SHIBA = """
<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%">
  <polygon points="15,36 25,5 45,25" fill="#d98324"/><polygon points="85,36 75,5 55,25" fill="#d98324"/>
  <polygon points="21,30 27,13 38,23" fill="#b96a55"/><polygon points="79,30 73,13 62,23" fill="#b96a55"/>
  <ellipse cx="50" cy="58" rx="35" ry="31" fill="#eda64f"/>
  <ellipse cx="27" cy="68" rx="13" ry="12" fill="#fdf4e3"/><ellipse cx="73" cy="68" rx="13" ry="12" fill="#fdf4e3"/>
  <ellipse cx="50" cy="72" rx="20" ry="15" fill="#fdf4e3"/>
  <ellipse cx="35" cy="44" rx="4.5" ry="3" fill="#fdf4e3"/><ellipse cx="65" cy="44" rx="4.5" ry="3" fill="#fdf4e3"/>
  <ellipse cx="35" cy="53" rx="3.4" ry="4.4" fill="#3b2b1d"/><ellipse cx="65" cy="53" rx="3.4" ry="4.4" fill="#3b2b1d"/>
  <circle cx="36.2" cy="51.4" r="1.2" fill="#fff"/><circle cx="66.2" cy="51.4" r="1.2" fill="#fff"/>
  <ellipse cx="50" cy="65" rx="4.6" ry="3.4" fill="#33251a"/>
  <path d="M50 68 L50 73" stroke="#33251a" stroke-width="2" stroke-linecap="round"/>
  <path d="M42 74 Q46 79 50 74 Q54 79 58 74" stroke="#33251a" stroke-width="2.4" fill="none" stroke-linecap="round"/>
</svg>
"""

# --- social card (og:image / twitter:image) -------------------------------
# 1200x630 is the size every unfurler (iMessage, Slack, X, Discord, FB) crops to.
CARD_HTML = f"""
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ width:1200px; height:630px; background:{BG}; color:#e2e8f0; overflow:hidden;
         font-family:"Segoe UI",system-ui,-apple-system,sans-serif; position:relative; }}
  /* the hero's gold graph-paper grid + a warm glow so the card never reads as a flat black box */
  .grid {{ position:absolute; inset:0;
    background-image:
      linear-gradient(rgba(240,185,11,.055) 1px, transparent 1px),
      linear-gradient(90deg, rgba(240,185,11,.055) 1px, transparent 1px);
    background-size:44px 44px; }}
  .glow {{ position:absolute; inset:0;
    background:radial-gradient(ellipse 780px 460px at 62% 42%, rgba(240,185,11,.16), transparent 68%); }}
  .edge {{ position:absolute; inset:0; box-shadow:inset 0 0 190px 60px rgba(0,0,0,.72); }}
  .bar {{ position:absolute; top:0; left:0; right:0; height:8px;
          background:linear-gradient(90deg,{GOLD},{ORANGE},{GOLD}); }}
  .wrap {{ position:relative; height:100%; padding:60px 64px 54px; display:flex; flex-direction:column; }}

  .brand {{ display:flex; align-items:center; gap:14px; }}
  .coin {{ width:60px; height:60px; border-radius:16px;
           background:linear-gradient(135deg,{GOLD},{ORANGE}); color:{BG};
           display:flex; align-items:center; justify-content:center;
           font-size:38px; font-weight:800; line-height:60px;
           box-shadow:0 0 34px rgba(240,185,11,.42); }}
  .name {{ font-size:25px; font-weight:800; letter-spacing:5px; }}
  .name b {{ color:{GOLD}; }}

  .body {{ flex:1; display:flex; align-items:center; gap:40px; margin-top:6px; }}
  h1 {{ font-size:82px; font-weight:900; letter-spacing:-2.5px; line-height:1.03; }}
  h1 .gold {{ color:{GOLD}; }}
  h1 .sub {{ display:block; font-size:57px; letter-spacing:-1.5px; margin-top:6px; }}
  p.tag {{ color:#94a3b8; font-size:23px; line-height:1.45; margin-top:22px; max-width:640px; }}
  p.tag b {{ color:#e2e8f0; }}
  .shiba {{ width:270px; height:270px; flex:none; filter:drop-shadow(0 0 46px rgba(240,185,11,.34)); }}

  .foot {{ display:flex; align-items:center; gap:12px; flex-wrap:nowrap; }}
  .pill {{ border:1px solid #2a2a33; background:rgba(17,17,20,.85); border-radius:999px;
           padding:9px 17px; font-size:17px; color:#94a3b8;
           font-family:Consolas,ui-monospace,monospace; white-space:nowrap; }}
  .pill b {{ color:#34d399; }}
  .pill.mit b {{ color:{GOLD}; }}
  .url {{ margin-left:auto; font-size:20px; color:{GOLD}; font-weight:700;
          font-family:Consolas,ui-monospace,monospace; }}
</style>
<div class="grid"></div><div class="glow"></div><div class="edge"></div><div class="bar"></div>
<div class="wrap">
  <div class="brand"><div class="coin">&#208;</div><div class="name"><b>DOGE</b>MINER</div></div>
  <div class="body">
    <div>
      <h1>MUCH<span class="gold">WOW</span>.<span class="sub">REAL DOGECOIN MINING.</span></h1>
      <p class="tag">Open-source pool miner with a live dashboard. Real Scrypt work
         on your <b>CPU or GPU</b> &mdash; <b>no registration</b>, no API keys.</p>
    </div>
    <div class="shiba">{SHIBA}</div>
  </div>
  <div class="foot">
    <span class="pill mit"><b>MIT</b> free forever</span>
    <span class="pill"><b>&#10003;</b> one-click start</span>
    <span class="pill"><b>&#10003;</b> Windows &middot; macOS &middot; Linux</span>
    <span class="pill"><b>&#10003;</b> no signup</span>
    <span class="url">doge-miner.io</span>
  </div>
</div>
"""

# --- home-screen icon -----------------------------------------------------
# Rendered opaque and full-bleed: iOS ignores transparency (it composites onto
# black) and applies its own rounded-rect mask, so we ship square gold edge-to-edge.
ICON_HTML = f"""
<style>
  * {{ margin:0; padding:0; }}
  body {{ width:1024px; height:1024px; overflow:hidden;
          background:linear-gradient(135deg,{GOLD} 0%,{ORANGE} 52%,{GOLD} 100%);
          font-family:"Segoe UI",system-ui,sans-serif; }}
  /* ~58% of the canvas: iOS masks a rounded rect over this, so the glyph needs
     margin on every side or the corners of the D get shaved off. */
  .d {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center;
        color:{BG}; font-size:600px; font-weight:800; line-height:1024px;
        text-shadow:0 10px 30px rgba(0,0,0,.16); }}
</style>
<div class="d">&#208;</div>
"""

# --- favicon --------------------------------------------------------------
# Gold coin on the app's near-black, so it stays legible on both light and dark
# browser tab strips (a transparent glyph disappears against one of them).
FAVICON_HTML = f"""
<style>
  * {{ margin:0; padding:0; }}
  body {{ width:512px; height:512px; overflow:hidden; background:{BG};
          font-family:"Segoe UI",system-ui,sans-serif; }}
  .c {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; }}
  .coin {{ width:470px; height:470px; border-radius:50%;
           background:linear-gradient(135deg,{GOLD},{ORANGE});
           display:flex; align-items:center; justify-content:center;
           color:{BG}; font-size:360px; font-weight:800; line-height:1; padding-bottom:34px; }}
</style>
<div class="c"><div class="coin">&#208;</div></div>
"""


def shot(page, html, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.set_content(html)
    page.wait_for_timeout(120)
    return Image.open(io.BytesIO(page.screenshot(omit_background=False))).convert("RGB")


def resize(img, size):
    return img.resize((size, size), Image.LANCZOS)


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=1)

        card = shot(page, CARD_HTML, 1200, 630)
        card.save(out / "og.png", optimize=True)
        written.append("og.png")

        icon = shot(page, ICON_HTML, 1024, 1024)
        # 180 = iOS home screen. 192/512 = Android + PWA install prompt.
        for size, name in ((180, "apple-touch-icon.png"), (192, "icon-192.png"), (512, "icon-512.png")):
            resize(icon, size).save(out / name, optimize=True)
            written.append(name)

        fav = shot(page, FAVICON_HTML, 512, 512)
        resize(fav, 32).save(out / "favicon-32.png", optimize=True)
        written.append("favicon-32.png")
        # Multi-resolution .ico: browsers and crawlers still probe /favicon.ico.
        resize(fav, 48).save(out / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
        written.append("favicon.ico")

        browser.close()

    for name in written:
        print(f"{name:24} {(out / name).stat().st_size / 1024:7.1f} KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "frontend")
