# -*- coding: utf-8 -*-
"""Etapa 8 — Entrega.

Empacota o resultado e publica o MP4 final AGRUPADO POR CARD (P1 + P2 no MESMO lugar).
Decisão do editor 2026-07-09 (revisada): a editora abre a pasta do card e o vídeo final
tem que estar NA CARA — sem cavar entre artefatos intermediários. Layout:

  ENTREGAS/<card>/
     <card> — Parte 1.mp4          ← o vídeo final da isca (no topo, pega e sobe)
     <card> — Parte 2.mp4          ← o vídeo final da extensão (mesma pasta)
     extras/
        parte-1/  narracao.mp3 + imagens/ + capas/ (+ thumb/roteiro se existirem) + README.txt
        parte-2/  idem

  VIDEOS-PRONTOS/<card> — Parte N.mp4   ← lista PLANA de TODOS os vídeos prontos (hardlink),
                                          o jeito mais rápido de bater o olho e pegar um final.

Onde N∈{1,2}: a isca (P1) e a extensão (P2) do MESMO card resolvem o mesmo <card> (o nome-base
sem o sufixo ' - P2') e caem na mesma pasta — cada rodada só ACRESCENTA a sua parte (idempotente).

Cria (idempotente) os atalhos do Desktop (reusa entrega.py). Marca `.entregue` ao concluir.
NÃO faz upload no ClickUp (fora do escopo desta v1). O editor pega os MP4s na pasta do card.

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

_SUFIXO_P2 = re.compile(r'\s*-\s*P2\s*$', re.IGNORECASE)


def _card_e_parte(proj):
    """(nome-base do card SEM ' - P2', parte∈{1,2}).

    A semente da P2 anexa ' - P2' ao nome_card e marca source.parte='p2'. Removendo o sufixo,
    P1 e P2 do MESMO card resolvem o MESMO nome-base → caem na mesma pasta de entrega."""
    nome = proj.dir.name
    parte = 1
    if proj.existe(proj.source):
        try:
            src = json.loads(proj.source.read_text(encoding="utf-8", errors="replace"))
            nome = (src.get("nome_card") or src.get("card_nome") or src.get("titulo")
                    or src.get("title") or nome)
            if str(src.get("parte") or "").strip().lower() == "p2":
                parte = 2
        except (OSError, ValueError):
            pass
    nome = _SUFIXO_P2.sub("", str(nome))                     # tira ' - P2' do nome_card da P2
    nome = re.sub(r'-p2$', '', nome, flags=re.IGNORECASE)   # e do fallback proj.dir.name (<slug>-p2)
    nome = _INVALIDOS_WIN.sub("_", nome).strip().rstrip(".")
    return (nome or _SUFIXO_P2.sub("", proj.dir.name) or proj.dir.name), parte


def _nome_video(base, parte):
    return "%s — Parte %d.mp4" % (base, parte)


def _publicar_plano(proj, base, parte, log):
    """VIDEOS-PRONTOS/<card> — Parte N.mp4 — lista PLANA (hardlink; cai p/ cópia)."""
    VIDEOS_PRONTOS_DIR.mkdir(parents=True, exist_ok=True)
    destino = VIDEOS_PRONTOS_DIR / _nome_video(base, parte)
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


def _copiar_se_existe(proj, src, dst):
    """Copia src→dst só se existir e não for vazio. Retorna True se copiou."""
    try:
        if proj.existe(src):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            return True
    except OSError:
        pass
    return False


def _bundle(proj, base, parte, log):
    """ENTREGAS/<card>/: vídeo final NO TOPO + extras/parte-N/ com o resto."""
    card_dir = ENTREGAS_DIR / base
    card_dir.mkdir(parents=True, exist_ok=True)

    # 1) vídeo final na cara, no topo da pasta do card
    shutil.copyfile(proj.final_mp4, card_dir / _nome_video(base, parte))

    # 2) tudo o mais vai pra extras/parte-N/ (fora do caminho de quem só quer o vídeo)
    ex = card_dir / "extras" / ("parte-%d" % parte)
    if ex.exists():
        shutil.rmtree(ex, ignore_errors=True)      # re-entrega limpa dessa parte
    ex.mkdir(parents=True, exist_ok=True)

    _copiar_se_existe(proj, proj.narration_mp3, ex / "narracao.mp3")
    _copiar_se_existe(proj, proj.thumb_selected, ex / "thumb_capa.png")
    _copiar_se_existe(proj, proj.thumb_ref, ex / "thumb_referencia.png")
    _copiar_se_existe(proj, proj.roteiro_pdf, ex / "roteiro.pdf")
    _copiar_se_existe(proj, proj.roteiro_docx, ex / "roteiro.docx")

    dimg = ex / "imagens"; dimg.mkdir(exist_ok=True)
    n_img = 0
    for img in sorted(proj.images_dir.glob("img_*.png")):
        shutil.copyfile(img, dimg / img.name); n_img += 1

    dcap = ex / "capas"; dcap.mkdir(exist_ok=True)
    n_cap = 0
    for cap in sorted(proj.covers_dir.glob("capa_*.mp4")):
        shutil.copyfile(cap, dcap / cap.name); n_cap += 1

    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    (ex / "README.txt").write_text(
        "CARD : %s\nPARTE: %d\nDATA : %s\n\n"
        "O vídeo final está UM NÍVEL ACIMA: \"%s\".\n"
        "Esta pasta (extras/parte-%d) tem só os materiais de apoio: narracao.mp3, "
        "imagens/ (%d) e capas/ (%d)%s.\n"
        "Projeto original: %s\n"
        % (base, parte, agora, _nome_video(base, parte), parte, n_img, n_cap,
           " + thumb/roteiro" if (ex / "roteiro.pdf").exists() or (ex / "thumb_capa.png").exists() else "",
           proj.dir),
        encoding="utf-8")
    log("    📦 ENTREGAS/%s/  (Parte %d no topo + extras/parte-%d: %d imagens, %d capas)."
        % (base, parte, parte, n_img, n_cap))
    return card_dir


def run(proj, log, cancel=None, **_):
    if not proj.existe(proj.final_mp4):
        raise ErroPipeline("Falta out/final.mp4 (Etapa 7) para entregar.")
    base, parte = _card_e_parte(proj)
    log("▶ Etapa 8 — entrega: %s (Parte %d)" % (base, parte))
    _publicar_plano(proj, base, parte, log)
    _bundle(proj, base, parte, log)
    try:
        entrega.criar_atalho_desktop(log)
    except Exception as e:
        log("    (atalho do Desktop não criado: %s)" % e)
    (proj.dir / ".entregue").write_text(
        "%s - Parte %d\n%s\n" % (base, parte, datetime.now().strftime("%Y-%m-%d %H:%M")),
        encoding="utf-8")
    log("    ✓ entregue.")


# --- teste standalone: py -3 stages/s8_entrega.py <slug> ------------------------------
if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    slug = sys.argv[1] if len(sys.argv) > 1 else "82-faxineira-bilionario"
    run(projeto_por_slug(slug), print, None)
