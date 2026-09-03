"""Generate docs/assets/cod-doc/social-preview.png — README banner."""

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
ACCENT = (255, 122, 26)
FG = (230, 237, 243)
DIM = (139, 148, 158)

img = Image.new("RGB", (W, H))
d = ImageDraw.Draw(img)

# vertical gradient #0d1117 -> #161b22
top, bot = (13, 17, 23), (22, 27, 34)
for y in range(H):
    t = y / H
    d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))

# subtle grid
for x in range(0, W, 40):
    d.line([(x, 0), (x, H)], fill=(26, 32, 42), width=1)
for y in range(0, H, 40):
    d.line([(0, y), (W, y)], fill=(26, 32, 42), width=1)

# accent bar on the left
d.rectangle([80, 190, 92, 470], fill=ACCENT)

font_title = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 150, index=0)
font_sub = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 34, index=0)
font_tag = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 26, index=0)

d.text((130, 185), "COD-DOC", font=font_title, fill=FG)
d.text((133, 360), "Context Orchestrator for Documentation", font=font_sub, fill=ACCENT)
d.text((133, 430), "$ docs that cannot drift · MCP · CLI · Web · SQLite", font=font_tag, fill=DIM)

img.save("docs/assets/cod-doc/social-preview.png", optimize=True)
print("saved")
