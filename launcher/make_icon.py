# -*- coding: utf-8 -*-
"""Gera o icone do Editor Auto (assets/editor-auto.ico)."""
import os
from PIL import Image, ImageDraw

BG = (20, 20, 28)        # #14141c
CARD = (36, 36, 47)      # #24242f
ACC = (138, 123, 216)    # #8a7bd8
ACC2 = (108, 92, 200)

SIZE = 512
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# fundo arredondado
r = 96
d.rounded_rectangle([16, 16, SIZE - 16, SIZE - 16], radius=r, fill=BG)
d.rounded_rectangle([16, 16, SIZE - 16, SIZE - 16], radius=r, outline=CARD, width=8)

# triangulo de "play" (edicao/render) centralizado
cx, cy = SIZE // 2, SIZE // 2
w = 150
tri = [(cx - w + 30, cy - w), (cx - w + 30, cy + w), (cx + w + 10, cy)]
# glow
d.polygon([(x + 6, y + 6) for x, y in tri], fill=ACC2)
d.polygon(tri, fill=ACC)

# duas barrinhas de "timeline" embaixo
by = SIZE - 120
d.rounded_rectangle([120, by, 300, by + 26], radius=13, fill=ACC)
d.rounded_rectangle([320, by, 392, by + 26], radius=13, fill=CARD)

out = os.path.join(os.path.dirname(__file__), "..", "assets", "editor-auto.ico")
out = os.path.abspath(out)
sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
img.save(out, sizes=sizes)
print("OK", out)
