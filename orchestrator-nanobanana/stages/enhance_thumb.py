# -*- coding: utf-8 -*-
"""enhance_thumb.py — Tratamento local pós-geração da capa (brilho/brio/de-yellow).

Chamado pelo s6_thumbnails logo depois de cada geração de thumb_01.png: se a imagem sair
escura/opaca, aplica lift de gamma suave nos mid-tones + de-yellow (neutraliza o cast quente
do GPT 2) + contraste e saturação fortes. O resultado tem BRILHO SUAVE + BRIO FORTE sem
lavar a cena (cena noturna continua noturna).

Parâmetros calibrados pelo feedback da Heloyse (2026-06-23):
  - Erro corrigido: TARGET=120/FLOOR=0.55 lavava a cena; SAT baixo tirava o brio.
  - Certo: TARGET=92 (lift gentil), GAMMA_FLOOR=0.82, SAT=1.30, CON=1.12 + de-yellow antes de saturar.
  - De-yellow: WB_B=1.05 / WB_R=0.98 / WB_G=0.99 — remove amarelado da pele sem esfriar a cena.

Standalone: py -3 enhance_thumb.py <entrada.png> [saida.png]
"""
import math
import sys
from pathlib import Path

try:
    from PIL import Image, ImageEnhance, ImageStat
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

# Alvo de luminância baixo: só tira o "opaco", nunca clareia demais
TARGET = 92.0
# Piso do gamma: lift bem suave (nunca lavar a cena; cena noturna continua noturna)
GAMMA_FLOOR = 0.82
# Saturação FORTE (imagens escuras que precisaram de gamma lift): brio máximo
SAT = 1.30
# Saturação LEVE (imagens JÁ CLARAS, sem gamma): evitar estourar o que já está vibrante
# Feedback Heloyse (2026-06-23): thumb 247 era clara e ficou "muito estourada" com SAT=1.30.
SAT_BRIGHT = 1.15
# Contraste -> profundidade/dimensão
CON = 1.12
# De-yellow (balanço de branco): neutraliza o cast quente do GPT 2 sem esfriar a cena.
# Ordem de aplicação: gamma -> de-yellow -> contraste -> saturação (saturar depois do
# de-yellow evita amplificar o amarelo que já estava na imagem).
WB_R = 0.98
WB_G = 0.99
WB_B = 1.05


def _luminance(im):
    return ImageStat.Stat(im.convert("L")).mean[0]


def _gamma_lut(g):
    return [min(255, round(255.0 * (i / 255.0) ** g)) for i in range(256)] * 3


def _scale(ch, f):
    return ch.point(lambda v: min(255, round(v * f)))


def _deyellow(im):
    r, g, b = im.split()
    return Image.merge("RGB", (_scale(r, WB_R), _scale(g, WB_G), _scale(b, WB_B)))


def enhance(src, dst=None):
    """Trata a imagem: gamma lift (se escura) + de-yellow + contraste + saturação.

    Se `dst` for None, sobrescreve `src` in-place. Retorna (before, after, gamma) ou None se
    Pillow não estiver instalado."""
    if not _PIL_OK:
        return None
    dst = dst or src
    im = Image.open(str(src)).convert("RGB")
    before = _luminance(im)
    g = 1.0
    if before < TARGET:
        b = max(before, 1.0) / 255.0
        g = max(GAMMA_FLOOR, math.log(TARGET / 255.0) / math.log(b))
    out = im.point(_gamma_lut(g)) if g < 0.999 else im
    out = _deyellow(out)
    out = ImageEnhance.Contrast(out).enhance(CON)
    # Saturação adaptativa: imagens já claras (sem gamma) recebem boost menor para
    # não estourar o que já é vibrante (feedback Heloyse: thumb 247 ficou "muito estourada")
    sat = SAT if g < 0.999 else SAT_BRIGHT
    out = ImageEnhance.Color(out).enhance(sat)
    after = _luminance(out)
    out.save(str(dst))
    return before, after, g


def enhance_if_dark(path, log=None):
    """Aplica tratamento na capa em `path` (in-place). Loga o resultado se `log` fornecido.

    Safe: não levanta exceção (falha silenciosa com aviso no log — nunca derruba o pipeline)."""
    if not _PIL_OK:
        if log:
            log("    ⚠ Pillow não instalado — tratamento local da capa pulado (pip install pillow).")
        return
    path = Path(path)
    if not path.exists():
        return
    try:
        result = enhance(str(path))
    except Exception as e:  # noqa: BLE001
        if log:
            log("    ⚠ enhance_thumb falhou (%s) — capa original mantida." % e)
        return
    if result and log:
        before, after, g = result
        if g < 0.999:
            log("    ✓ Capa tratada: luminância %.0f→%.0f "
                "(gamma=%.3f, sat=%.2f, contraste=%.2f, WB R/G/B=%.2f/%.2f/%.2f)"
                % (before, after, g, SAT, CON, WB_R, WB_G, WB_B))
        else:
            log("    ✓ Capa já clara (lum=%.0f) — só de-yellow+brio aplicados." % before)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: py -3 enhance_thumb.py <entrada.png> [saida.png]")
        sys.exit(1)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else None
    result = enhance(src, dst)
    if result:
        before, after, g = result
        print("BEFORE=%.1f AFTER=%.1f gamma=%.3f satur=%.2f contraste=%.2f "
              "WB(R/G/B)=%.2f/%.2f/%.2f -> %s"
              % (before, after, g, SAT, CON, WB_R, WB_G, WB_B, dst or src))
