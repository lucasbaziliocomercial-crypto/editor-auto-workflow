# -*- coding: utf-8 -*-
"""Etapa 7 — Montagem final (FFmpeg).

O FORMATO (vertical 9:16 ou horizontal 16:9) NÃO é fixo aqui: vem das envs ROTEIRO_W/H,
que o pipeline força por canal (ex.: Lena = 1920x1080 horizontal — ver common.CANAL_FORMATO).
O engine se chama `montagem_vertical` por herança do Romance Maker, mas respeita a env.

Delega ao engine `montagem_vertical.construir`, que reproduz a estrutura do Romance Maker
(teaser sincronizado ao gancho → capítulos com capa + imagens sincronizadas à narração →
resumo P2 → CTA, música de fundo a -10 dB) renderizando um MP4 headless.

Pré-requisitos (etapas anteriores): narration.mp3 + narration.srt (3), capitulos.json (3),
prompts_imagens.txt (4), images/*.png (5), covers/*.mp4 (6). Drop-in por canal em
materiais/<canal>/ (teaser, book2, cta, padronizados) — o que faltar é pulado com aviso.

Contrato: run(proj, log, cancel, **kw). Idempotente (âncora = out/final.mp4).
"""

from common import ErroPipeline
import montagem_vertical


def run(proj, log, cancel=None, **kw):
    if proj.existe(proj.final_mp4):
        log("    out/final.mp4 já existe — montagem pulada.")
        return
    faltando = []
    if not proj.existe(proj.narration_mp3):
        faltando.append("narration.mp3 (Etapa 3)")
    if not proj.existe(proj.dir / "capitulos.json"):
        faltando.append("capitulos.json (Etapa 3)")
    if not list(proj.images_dir.glob("img_*.png")):
        faltando.append("images/*.png (Etapa 5)")
    if faltando:
        raise ErroPipeline("Montagem sem pré-requisitos: %s." % ", ".join(faltando))

    import os, time
    w = os.environ.get("ROTEIRO_W", "1920"); h = os.environ.get("ROTEIRO_H", "1080")
    asp = os.environ.get("ROTEIRO_ASPECT", "16:9")
    orient = "horizontal" if _int(w) >= _int(h) else "vertical"
    log("▶ Etapa 7 — montagem final (FFmpeg, %sx%s %s / %s)..." % (w, h, asp, orient))
    _t0 = time.perf_counter()
    montagem_vertical.construir(proj, log, cancel, parte=kw.get("parte"))
    # Tempo REAL desta montagem (wall-clock) — a editora quer saber quanto a Etapa 7 leva por
    # máquina (RTX vs notebook Intel) e por parte (P1/P2). Só medição; não muda o render.
    _el = time.perf_counter() - _t0
    _rot = str(kw.get("parte") or "").upper()
    log("    ⏱ Etapa 7 (%s) levou %d min %02d s (wall-clock, esta máquina)."
        % (_rot or "P1", int(_el // 60), int(_el % 60)))


def _int(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


# --- teste standalone: py -3 stages/s7_montagem.py <slug> -----------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    slug = sys.argv[1] if len(sys.argv) > 1 else "82-faxineira-bilionario"
    run(projeto_por_slug(slug), print, None)
