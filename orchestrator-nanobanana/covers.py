"""
Gera as capas de "troca de capítulo" replicando o preset do Canva:
imagem de fundo (opcional) + título do capítulo CENTRALIZADO + fonte
configurável + contorno.

Duas saídas:
  - PNG estático (prévia/fallback) via `generate_cover`/`generate_covers`.
  - MP4 animado (estilo CapCut: zoom-in lento no fundo + fade-in do título) via
    `generate_cover_video`/`generate_cover_videos`. O MP4 é a entrega de download.

PNG usa só Pillow. O MP4 renderiza os frames com Pillow e codifica via
imageio + imageio-ffmpeg (ffmpeg embutido — sem dependência de rede nem de
ffmpeg no PATH).
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb(value: str, default=(255, 255, 255)) -> tuple[int, int, int]:
    try:
        v = value.strip().lstrip("#")
        if len(v) == 3:
            v = "".join(c * 2 for c in v)
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except Exception:
        return default


def _bundled_font() -> str | None:
    """Caminho da fonte embutida das capas quando `font_path` está vazio.

    Padrão = **The Seasons Regular** — a serifada de alto contraste, elegante, que
    a equipe usa de fato nas capas de troca de capítulo (confirmada por análise do
    vídeo-referência do canal Lena: figuras "01" lining + hairlines finíssimas).
    Playfair Display Regular fica como fallback (era o palpite antigo do "preset do
    Canva", mais encorpado e com figuras old-style — NÃO batia com a referência).

    Resolve em duas bases para ser robusto a empacotamento (PyInstaller): no app
    EMPACOTADO a fonte mora em `sys._MEIPASS/assets/fonts`; em DEV, relativo ao
    módulo (`assets/fonts`). Sem essa busca dupla, o app instalado não achava o
    .ttf e caía no fallback do sistema."""
    bases = []
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        bases.append(Path(sys._MEIPASS))
    bases.append(Path(__file__).resolve().parent.parent)
    for name in ("TheSeasons-Regular.ttf", "PlayfairDisplay-Regular.ttf"):
        for base in bases:
            p = base / "assets" / "fonts" / name
            if p.exists():
                return str(p)
    return None


def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    # 1) Fonte escolhida pelo usuário (campo "Fonte das capas (.ttf)" na UI)
    if font_path and Path(font_path).exists():
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    # 2) Fonte embutida (The Seasons Regular — padrão das capas)
    bundled = _bundled_font()
    if bundled:
        try:
            return ImageFont.truetype(bundled, size)
        except Exception:
            pass
    # 3) Fallbacks comuns no Windows — não-bold primeiro (a capa não deve ficar bold).
    for guess in ("arial.ttf", "segoeui.ttf", "arialbd.ttf", "segoeuib.ttf"):
        try:
            return ImageFont.truetype(guess, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    try:
        return draw.textlength(text, font=font)
    except Exception:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]


def _wrap(draw, text: str, font, max_width: float) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        candidate = f"{cur} {w}"
        if _text_width(draw, candidate, font) <= max_width:
            cur = candidate
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _cover_background(cfg: dict, bg_image: str | None) -> Image.Image:
    w = int(cfg.get("width", 1080))
    h = int(cfg.get("height", 1920))
    bg_color = _hex_to_rgb(cfg.get("bg_color", "#0E0E16"), (14, 14, 22))

    canvas = Image.new("RGB", (w, h), bg_color)

    # Sem fundo específico → usa o fundo padrão da config (se houver)
    if not bg_image:
        bg_image = cfg.get("bg_image_default", "") or None

    use_img = cfg.get("use_chapter_image_as_bg", True)
    if use_img and bg_image and Path(bg_image).exists():
        try:
            src = Image.open(bg_image).convert("RGB")
            # Cover: redimensiona mantendo proporção e corta o excedente
            scale = max(w / src.width, h / src.height)
            new_size = (max(1, int(src.width * scale)), max(1, int(src.height * scale)))
            src = src.resize(new_size, Image.LANCZOS)
            left = (src.width - w) // 2
            top = (src.height - h) // 2
            src = src.crop((left, top, left + w, top + h))
            canvas.paste(src, (0, 0))
            # Overlay escuro para o título ficar legível
            alpha = float(cfg.get("overlay_alpha", 0.45))
            if alpha > 0:
                overlay = Image.new("RGB", (w, h), (0, 0, 0))
                canvas = Image.blend(canvas, overlay, min(0.95, max(0.0, alpha)))
        except Exception:
            pass
    return canvas


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def _render_title_layer(title: str, cfg: dict) -> Image.Image:
    """
    Desenha o título centralizado numa camada RGBA transparente (tamanho da capa).
    Usada tanto pelo PNG quanto pelo MP4 (a camada faz fade-in no vídeo).
    """
    w = int(cfg.get("width", 1080))
    h = int(cfg.get("height", 1920))
    font_size = int(cfg.get("font_size", 84))
    text_color = _hex_to_rgb(cfg.get("text_color", "#FFFFFF"), (255, 255, 255))
    stroke_color = _hex_to_rgb(cfg.get("stroke_color", "#000000"), (0, 0, 0))
    stroke_width = int(cfg.get("stroke_width", 0))
    line_spacing = float(cfg.get("line_spacing", 1.05))
    max_line_ratio = float(cfg.get("max_line_ratio", 0.82))
    max_lines = max(1, int(cfg.get("max_lines", 2)))

    # Sombra suave (no lugar do contorno grosso) — só para legibilidade, igual Canva.
    shadow_color = _hex_to_rgb(cfg.get("shadow_color", "#000000"), (0, 0, 0))
    shadow_alpha = float(cfg.get("shadow_alpha", 0.55))
    shadow_offset = int(cfg.get("shadow_offset", 4))
    shadow_blur = float(cfg.get("shadow_blur", 8))

    # Remove aspas do título (não ficam bonitas na capa). Mantém apóstrofos.
    title = (title or "")
    for _q in ('"', '“', '”', '„', '«', '»'):
        title = title.replace(_q, "")
    title = title.strip().strip("'‘’").strip()

    if cfg.get("uppercase"):
        title = (title or "").upper()

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # Auto-ajuste: começa no tamanho-base e reduz a fonte até o título caber em
    # no máximo `max_lines` linhas (títulos longos encolhem; curtos ficam no
    # tamanho cheio). Evita as 3+ linhas e mantém o visual do Canva.
    max_width = w * max_line_ratio
    font_path = cfg.get("font_path", "")
    fit_size = font_size
    while fit_size > 48:
        font = _load_font(font_path, fit_size)
        lines = _wrap(draw, title or "", font, max_width)
        if len(lines) <= max_lines:
            break
        fit_size -= 4
    else:
        font = _load_font(font_path, fit_size)
        lines = _wrap(draw, title or "", font, max_width)

    ascent, descent = font.getmetrics() if hasattr(font, "getmetrics") else (font_size, 0)
    line_h = (ascent + descent) * line_spacing
    total_h = line_h * len(lines)
    y0 = (h - total_h) / 2  # centraliza verticalmente

    def _draw_lines(target: ImageDraw.ImageDraw, dx: float, dy: float, fill, sw: int, sfill):
        y = y0
        for line in lines:
            line_w = _text_width(target, line, font)
            x = (w - line_w) / 2  # centraliza horizontalmente
            target.text(
                (x + dx, y + dy), line, font=font, fill=fill,
                stroke_width=sw, stroke_fill=sfill,
            )
            y += line_h

    # 1) Camada de sombra (desenhada, borrada e composta por baixo do texto)
    if shadow_alpha > 0 and (shadow_offset != 0 or shadow_blur > 0):
        a = max(0, min(255, int(round(shadow_alpha * 255))))
        shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow_layer)
        _draw_lines(sdraw, shadow_offset, shadow_offset, shadow_color + (a,), 0, None)
        if shadow_blur > 0:
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow_blur))
        layer = Image.alpha_composite(layer, shadow_layer)
        draw = ImageDraw.Draw(layer)

    # 2) Texto principal (stroke vem da config — 0 por padrão)
    _draw_lines(
        draw, 0, 0, text_color + (255,),
        stroke_width, (stroke_color + (255,)) if stroke_width > 0 else None,
    )

    return layer


def generate_cover(title: str, cfg: dict, out_path: Path, bg_image: str | None = None) -> Path:
    """Gera uma capa PNG com o título centralizado."""
    canvas = _cover_background(cfg, bg_image).convert("RGBA")
    canvas = Image.alpha_composite(canvas, _render_title_layer(title, cfg))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path, "PNG")
    return out_path


def generate_covers(
    titles: list[str],
    cfg: dict,
    out_dir: Path,
    backgrounds: list[str] | None = None,
) -> list[str]:
    """
    Gera uma capa por título. `backgrounds[i]` (se houver) vira o fundo da
    capa i. Retorna os caminhos absolutos das capas geradas.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    backgrounds = backgrounds or []
    paths: list[str] = []
    for i, title in enumerate(titles):
        bg = backgrounds[i] if i < len(backgrounds) else None
        out_path = out_dir / f"capa_{i + 1:02d}.png"
        generate_cover(title, cfg, out_path, bg_image=bg)
        paths.append(str(out_path))
    return paths


# ---------------------------------------------------------------------------
# Vídeo animado (estilo CapCut): zoom-in lento no fundo + fade-in do título
# ---------------------------------------------------------------------------

def _zoom_crop(bg: Image.Image, zoom: float, w: int, h: int) -> Image.Image:
    """Aproxima o fundo `zoom`× e recorta de volta para w×h (centralizado)."""
    if zoom <= 1.0:
        return bg
    zw, zh = max(w + 1, int(round(w * zoom))), max(h + 1, int(round(h * zoom)))
    big = bg.resize((zw, zh), Image.LANCZOS)
    left = (zw - w) // 2
    top = (zh - h) // 2
    return big.crop((left, top, left + w, top + h))


def generate_cover_video(
    title: str, cfg: dict, out_path: Path, bg_image: str | None = None
) -> Path:
    """
    Gera a capa como MP4 animado: o fundo faz um zoom-in lento (Ken Burns) e o
    título entra com fade-in. Reaproveita `_cover_background` e `_render_title_layer`.
    """
    import numpy as np  # via imageio/cv2
    import imageio.v2 as imageio  # imageio-ffmpeg embute o ffmpeg

    w = int(cfg.get("width", 1080))
    h = int(cfg.get("height", 1920))
    fps = max(1, int(cfg.get("fps", 30)))
    duration_s = max(0.1, float(cfg.get("duration_s", 2.5)))
    fade_in_s = max(0.0, float(cfg.get("fade_in_s", 0.6)))
    zoom_max = max(1.0, float(cfg.get("zoom", 1.10)))

    bg = _cover_background(cfg, bg_image).convert("RGBA")
    title_layer = _render_title_layer(title, cfg)
    base_alpha = title_layer.getchannel("A")

    total = max(2, round(fps * duration_s))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    writer = imageio.get_writer(
        str(out_path), fps=fps, codec="libx264", pixelformat="yuv420p",
        macro_block_size=1, ffmpeg_log_level="error",
        output_params=["-crf", "20", "-preset", "medium"],
    )
    try:
        for i in range(total):
            p = i / (total - 1)               # 0..1 ao longo do clipe
            z = 1.0 + (zoom_max - 1.0) * p     # zoom-in linear
            t = i / fps
            a = 1.0 if fade_in_s <= 0 else min(1.0, t / fade_in_s)

            frame = _zoom_crop(bg, z, w, h)
            if a > 0:
                layer = title_layer.copy()
                if a < 1.0:
                    layer.putalpha(base_alpha.point(lambda v: int(v * a)))
                frame = Image.alpha_composite(frame, layer)

            writer.append_data(np.asarray(frame.convert("RGB")))
    finally:
        writer.close()
    return out_path


def generate_cover_videos(
    titles: list[str],
    cfg: dict,
    out_dir: Path,
    backgrounds: list[str] | None = None,
) -> list[str]:
    """Gera um MP4 animado por título (`capa_NN.mp4`). Retorna os caminhos."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    backgrounds = backgrounds or []
    paths: list[str] = []
    for i, title in enumerate(titles):
        bg = backgrounds[i] if i < len(backgrounds) else None
        out_path = out_dir / f"capa_{i + 1:02d}.mp4"
        generate_cover_video(title, cfg, out_path, bg_image=bg)
        paths.append(str(out_path))
    return paths


def zip_covers(out_dir: Path, zip_name: str = "trocas_de_capitulo.zip") -> str:
    """Compacta os MP4s das capas em um .zip (entrega de download) e retorna o caminho."""
    out_dir = Path(out_dir)
    zip_path = out_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for mp4 in sorted(out_dir.glob("capa_*.mp4")):
            zf.write(mp4, mp4.name)
    return str(zip_path)
