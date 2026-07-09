# -*- coding: utf-8 -*-
"""Etapa 8 — Entrega.

Empacota o resultado numa pasta limpa e publica o MP4 final com o nome do card:

  VIDEOS-PRONTOS/<nome do card>.mp4        — pasta plana (hardlink; cai p/ cópia)
  ENTREGAS/<slug>/                         — bundle: video_final.mp4 + narracao.mp3 +
                                             imagens/ + capas/ + README.txt

Cria (idempotente) o atalho "Vídeos Prontos" na Área de Trabalho (reusa entrega.py).
Marca `.entregue` (âncora da etapa) ao concluir.

NÃO faz upload no ClickUp (fora do escopo desta v1 — decisão registrada no plano). O editor
pega o MP4 na pasta plana.

Contrato: run(proj, log, cancel, **kw). Idempotente (âncora = .entregue).
"""

import os
import re
import json
import shutil
from datetime import datetime
from pathlib import Path

import entrega
from common import ErroPipeline, LONGFORM_DIR

_INVALIDOS_WIN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
VIDEOS_PRONTOS_DIR = LONGFORM_DIR / "VIDEOS-PRONTOS"
ENTREGAS_DIR = LONGFORM_DIR / "ENTREGAS"


def _nome_card(proj):
    nome = proj.dir.name
    if proj.existe(proj.source):
        try:
            src = json.loads(proj.source.read_text(encoding="utf-8", errors="replace"))
            nome = (src.get("nome_card") or src.get("card_nome") or src.get("titulo")
                    or src.get("title") or nome)
        except (OSError, ValueError):
            pass
    return _INVALIDOS_WIN.sub("_", str(nome)).strip().rstrip(".") or proj.dir.name


def _publicar_plano(proj, nome, log):
    VIDEOS_PRONTOS_DIR.mkdir(parents=True, exist_ok=True)
    destino = VIDEOS_PRONTOS_DIR / (nome + ".mp4")
    if destino.exists():
        try:
            destino.unlink()
        except OSError:
            pass
    try:
        os.link(proj.final_mp4, destino)
        log("    🎬 VIDEOS-PRONTOS/%s (hardlink)." % destino.name)
    except OSError:
        shutil.copyfile(proj.final_mp4, destino)
        log("    🎬 VIDEOS-PRONTOS/%s (cópia)." % destino.name)
    return destino


def _bundle(proj, nome, log):
    destino = ENTREGAS_DIR / proj.dir.name
    destino.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(proj.final_mp4, destino / "video_final.mp4")
    if proj.existe(proj.narration_mp3):
        shutil.copyfile(proj.narration_mp3, destino / "narracao.mp3")
    # imagens do corpo
    dimg = destino / "imagens"; dimg.mkdir(exist_ok=True)
    n_img = 0
    for img in sorted(proj.images_dir.glob("img_*.png")):
        shutil.copyfile(img, dimg / img.name); n_img += 1
    # capas
    dcap = destino / "capas"; dcap.mkdir(exist_ok=True)
    n_cap = 0
    for cap in sorted(proj.covers_dir.glob("capa_*.mp4")):
        shutil.copyfile(cap, dcap / cap.name); n_cap += 1
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    (destino / "README.txt").write_text(
        "CARD : %s\nSLUG : %s\nDATA : %s\n\nvideo_final.mp4 + narracao.mp3 + imagens/ (%d) + capas/ (%d)\n"
        "Projeto original: %s\n" % (nome, proj.dir.name, agora, n_img, n_cap, proj.dir),
        encoding="utf-8")
    log("    📦 ENTREGAS/%s (%d imagens, %d capas)." % (proj.dir.name, n_img, n_cap))
    return destino


def run(proj, log, cancel=None, **_):
    if not proj.existe(proj.final_mp4):
        raise ErroPipeline("Falta out/final.mp4 (Etapa 7) para entregar.")
    nome = _nome_card(proj)
    log("▶ Etapa 8 — entrega: %s" % nome)
    _publicar_plano(proj, nome, log)
    _bundle(proj, nome, log)
    try:
        entrega.criar_atalho_desktop(log)
    except Exception as e:
        log("    (atalho do Desktop não criado: %s)" % e)
    (proj.dir / ".entregue").write_text(
        "%s\n%s\n" % (nome, datetime.now().strftime("%Y-%m-%d %H:%M")), encoding="utf-8")
    log("    ✓ entregue.")


# --- teste standalone: py -3 stages/s8_entrega.py <slug> ------------------------------
if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    slug = sys.argv[1] if len(sys.argv) > 1 else "82-faxineira-bilionario"
    run(projeto_por_slug(slug), print, None)
