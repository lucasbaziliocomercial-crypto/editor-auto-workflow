# -*- coding: utf-8 -*-
"""Etapa 7 — Montagem final (vídeo vertical, FFmpeg).

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

    log("▶ Etapa 7 — montagem final (FFmpeg, vertical)...")
    montagem_vertical.construir(proj, log, cancel, parte=kw.get("parte"))


# --- teste standalone: py -3 stages/s7_montagem.py <slug> -----------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    slug = sys.argv[1] if len(sys.argv) > 1 else "82-faxineira-bilionario"
    run(projeto_por_slug(slug), print, None)
