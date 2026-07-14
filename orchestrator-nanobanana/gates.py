# -*- coding: utf-8 -*-
"""gates.py — os 2 pontos de decisão humana do pipeline, servidos pelo painel web.

Gate 1 (após o validador): aprovar/editar o roteiro antes de gastar TTS/imagem.
Gate 2 (após a thumb): validar a thumbnail/capa única -> thumb_selected.png (da qual a
Etapa 7 deriva as imagens do corpo do vídeo). Com N_THUMBS=1 (default atual) NÃO há
escolha — gate_thumb auto-confirma a única thumb sem abrir o painel.

Em modo --no-gates, gate_roteiro é pulado e a thumb é escolhida automaticamente
(auto_escolher_thumb), para rodadas de teste ponta a ponta.
"""

import json
import sys
from pathlib import Path

from common import ErroPipeline, PANEL_DIR

sys.path.insert(0, str(PANEL_DIR))
import app as painel  # panel/app.py


def _qa_aprovada(proj):
    """True se o QA do Claude aprovou a capa (ou se não há veredito = QA off/erro → não bloqueia).
    Quando False, o Gate 2 NÃO auto-confirma: abre o painel para a validação humana."""
    if not proj.existe(proj.thumb_qa):
        return True
    try:
        return bool(json.loads(proj.thumb_qa.read_text(encoding="utf-8")).get("approved"))
    except Exception:  # noqa: BLE001
        return True


def _resumo_qa(proj):
    """Linha curta do veredito do QA p/ log (ou '')."""
    if not proj.existe(proj.thumb_qa):
        return ""
    try:
        d = json.loads(proj.thumb_qa.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    return "QA score %s: %s" % (d.get("score", "?"),
                                d.get("verdict") or "; ".join(d.get("issues", []) or []))


def gate_roteiro(proj, log, cancel=None):
    if not proj.existe(proj.roteiro):
        raise ErroPipeline("Gate 1: roteiro.txt não existe.")
    # Se já foi aprovado antes E o roteiro não mudou desde então, pula (ex.: ao "Continuar").
    if proj.existe(proj.gate1_flag) and \
       proj.gate1_flag.stat().st_mtime >= proj.roteiro.stat().st_mtime:
        log("    roteiro já aprovado antes — Gate 1 pulado.")
        return
    log("⏸ GATE 1 — aprove o roteiro no painel para continuar.")
    dec = painel.run_gate(proj, "roteiro", log, cancel)
    if not dec.get("approved"):
        raise ErroPipeline("Gate 1: roteiro não aprovado.")
    proj.gate1_flag.write_text("ok", encoding="utf-8")
    log("    ✓ Roteiro aprovado.")


def gate_thumb(proj, log, cancel=None):
    if proj.existe(proj.thumb_selected):
        log("    thumb_selected.png já existe — Gate 2 pulado.")
        return
    thumbs = sorted(proj.thumbs_dir.glob("thumb_*.png"))
    if not thumbs:
        raise ErroPipeline("Gate 2: não há thumb em thumbs/ (rode a Etapa 6).")
    # 1 capa E o QA do Claude aprovou: não há o que decidir — auto-confirma sem abrir o painel.
    # Se o QA REPROVOU (após os retries da Etapa 6), NÃO auto-confirma em silêncio: abre o painel.
    if len(thumbs) == 1 and _qa_aprovada(proj):
        import shutil
        shutil.copyfile(thumbs[0], proj.thumb_selected)
        log("    (auto) 1 capa + QA aprovou — Gate 2 dispensado. thumb_selected.png = %s"
            % thumbs[0].name)
        return
    if not _qa_aprovada(proj):
        log("⏸ GATE 2 — o QA do Claude SINALIZOU a capa (%s). Valide/edite no painel."
            % (_resumo_qa(proj) or "reprovada"))
    else:
        log("⏸ GATE 2 — valide a thumbnail no painel para continuar.")
    dec = painel.run_gate(proj, "thumb", log, cancel)
    if not dec.get("choice") or not proj.existe(proj.thumb_selected):
        raise ErroPipeline("Gate 2: thumb não validada.")
    log("    ✓ Thumb validada: %s" % dec["choice"])


def auto_escolher_thumb(proj, log):
    """Modo --no-gates: escolhe a primeira thumb automaticamente (mas registra o veredito do QA)."""
    if proj.existe(proj.thumb_selected):
        return
    thumbs = sorted(proj.thumbs_dir.glob("thumb_*.png"))
    if not thumbs:
        raise ErroPipeline("auto_escolher_thumb: não há thumbs.")
    if not _qa_aprovada(proj):
        log("    ⚠ (--no-gates) QA do Claude reprovou a capa (%s) — seguindo mesmo assim."
            % (_resumo_qa(proj) or "reprovada"))
    import shutil
    shutil.copyfile(thumbs[0], proj.thumb_selected)
    log("    (auto) thumb_selected.png = %s" % thumbs[0].name)
