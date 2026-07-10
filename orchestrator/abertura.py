# -*- coding: utf-8 -*-
"""abertura.py — ABERTURA animada da Parte 2 (book 2), 2026-07-10.

A editora abre TODO book 2 com uma cena animada antes do capítulo 1. A esteira gerava a P2
começando direto no capítulo. Aqui a esteira TRANSFORMA o thumbnail do card (`thumb_ref.png`,
baixado na Etapa 1 e semeado na pasta da P2 pelo pipeline) num CLIPE animado (image->video via
Magnific) — a "cena animada" de abertura. A montagem (montagem_vertical) encaixa esse clipe como
1º segmento da P2 e cruza (crossfade) pro capítulo 1.

CONTRATO: `garantir_clip(proj, log, cancel)` devolve o Path do clipe de abertura (vídeo), ou None
quando não há como produzir (sem thumbnail, trava de crédito desligada, geração falhou, kill-switch
off). A montagem trata None graciosamente (P2 sem abertura, como antes).

FONTES na ordem de prioridade (o 1º que existir vence):
  1. Clipe LARGADO pela editora em `projects/<slug>/abertura/` (qualquer .mp4/.mov/…) — igual ao
     teaser: se ela quer animar à mão no Flow/VEO, é só largar ali. Sem custo de IA.
  2. Clipe JÁ GERADO `projects/<slug>/abertura/abertura.mp4` (idempotência — não regera).
  3. GERAÇÃO por IA (image->video do thumbnail), atrás da trava de crédito ROTEIRO_ABERTURA_VIDEO_OK=1.

Custo: video_generate COBRA crédito (nenhum modelo é ilimitado via MCP). Por isso a geração é
opt-in explícito (mesma filosofia do corpo, magnific_seam.garantir_corpo_liberado).
"""

import os
from pathlib import Path

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


def _on():
    """Abertura LIGADA por padrão (só a P2 chama). Desliga com ROTEIRO_ABERTURA=0/off."""
    return os.environ.get("ROTEIRO_ABERTURA", "1").strip().lower() not in (
        "0", "off", "none", "nao", "não", "no", "false")


def _credito_ok():
    """Geração por IA liberada? (opt-in explícito — video_generate cobra crédito). Um clipe já
    pronto/ largado pela editora NÃO passa por aqui; a trava só vale pra DISPARAR a geração."""
    return os.environ.get("ROTEIRO_ABERTURA_VIDEO_OK", "0").strip() == "1"


def _aspect():
    return os.environ.get("ROTEIRO_ASPECT", "9:16").strip() or "9:16"


def _duracao():
    try:
        return max(2.0, float(os.environ.get("ROTEIRO_ABERTURA_DUR", "5")))
    except (TypeError, ValueError):
        return 5.0


def _prompt_movimento():
    """Prompt de MOVIMENTO da abertura (image->video). Sutil e sem morphing — a cara/composição do
    thumbnail é sagrada. Sobrescrevível por ROTEIRO_ABERTURA_PROMPT."""
    d = ("Subtle cinematic animation of this romantic cover image: slow gentle camera push-in, "
         "soft parallax and depth, delicate natural movement in hair, fabric and ambient light, "
         "cinematic and photorealistic. Keep the exact same faces, composition and colors — "
         "no morphing, no distortion, no warping, no added text or logos.")
    return (os.environ.get("ROTEIRO_ABERTURA_PROMPT", "") or "").strip() or d


def _modelo():
    """slug do modelo image->video (ex.: um Seedance/Kling barato). Vazio = auto-select do agente."""
    return (os.environ.get("ROTEIRO_ABERTURA_MODEL", "") or "").strip()


def _dir(proj):
    return proj.dir / "abertura"


def _clip_existente(proj):
    """1º vídeo em projects/<slug>/abertura/ (drop da editora ou o abertura.mp4 já gerado). O
    abertura.mp4 gerado tem prioridade explícita; senão o 1º alfabético."""
    d = _dir(proj)
    if not d.is_dir():
        return None
    ger = d / "abertura.mp4"
    if ger.is_file() and ger.stat().st_size > 0:
        return ger
    vids = sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTS and p.stat().st_size > 0)
    return vids[0] if vids else None


def _instrucao(aspect, dur, prompt, modelo):
    slug_txt = ('4b) MODELO: use o slug "%s" (mode do passo 4). ' % modelo) if modelo else (
        "4b) MODELO: chame mcp__magnific__video_models_list {search:\"image\"} (ou "
        "{onlyRecommended:true}) e escolha um modelo BARATO/RÁPIDO que aceite keyframe de imagem "
        "inicial e aspect %s; pegue o `slug`. Em dúvida, OMITA o slug (auto-select). " % aspect)
    slug_field = ('slug:"%s", ' % modelo) if modelo else "<slug:\"<o do passo 4b>\" se escolheu um>, "
    return (
        "Você é a etapa de ABERTURA (P2/book 2) de uma esteira de romance. NÃO peça confirmação — "
        "gere e salve o arquivo, só isso.\n\n"
        "OBJETIVO: transformar a imagem `thumb_ref.png` (a capa/thumbnail deste vídeo) num CLIPE "
        "curto ANIMADO (image->video) com movimento cinematográfico SUTIL, pra abrir o vídeo. "
        "Salve em `abertura/abertura.mp4`.\n\n"
        "PASSOS:\n"
        "1) `Bash mkdir -p abertura`.\n"
        "2) SUBA o thumbnail como creation (o video_generate aceita creation identifier como "
        "keyframe):\n"
        "   (a) mcp__magnific__creations_request_upload {filename:\"thumb_ref.png\", "
        "contentType:\"image/png\"} -> guarde uploadUrl e identifier;\n"
        "   (b) `Bash curl -X PUT -H \"Content-Type: image/png\" --data-binary @thumb_ref.png "
        "\"<uploadUrl>\"`;\n"
        "   (c) mcp__magnific__creations_finalize_upload {identifier:<o do passo a>}.\n"
        "%s"
        "3) GERE o vídeo: mcp__magnific__video_generate {video:{clips:[{ keyframes:{ start:{ "
        "type:\"image\", url:\"<o identifier do passo 2c>\" } }, prompt:\"%s\", "
        "aspectRatio:\"%s\", duration:%.1f, %s}]}}. duration É OBRIGATÓRIA quando há slug.\n"
        "4) ESPERE: mcp__magnific__creations_wait até concluir; pegue a URL final do vídeo "
        "(campo url/webUrl do creation) — NÃO use webUrl como input de tool, só pra baixar.\n"
        "5) BAIXE: `Bash curl -L -o abertura/abertura.mp4 \"<url>\"`.\n\n"
        "CONFIRA que `abertura/abertura.mp4` existe e tem tamanho > 0. Romance platform-safe "
        "(sem nudez/explícito). Se falhar, re-tente 1x. No fim, imprima se salvou o arquivo."
        % (slug_txt, prompt, aspect, dur, slug_field)
    )


def garantir_clip(proj, log, cancel=None):
    """Devolve o Path do clipe de abertura (vídeo) ou None. Gera por IA só se preciso e liberado."""
    if not _on():
        log("    abertura: desligada (ROTEIRO_ABERTURA=0) — P2 sem abertura.")
        return None
    existente = _clip_existente(proj)
    if existente is not None:
        log("    abertura: usando clipe existente (%s)." % existente.name)
        return existente
    # Precisa gerar. Requisitos: thumbnail + trava de crédito.
    if not proj.existe(proj.thumb_ref):
        log("    abertura: sem thumb_ref.png na pasta da P2 — pulando a abertura.")
        return None
    if not _credito_ok():
        log("    abertura: geração por IA TRAVADA (video_generate cobra crédito). Para ligar, "
            "exporte ROTEIRO_ABERTURA_VIDEO_OK=1 — ou largue um clipe pronto em "
            "projects/<slug>/abertura/. P2 sem abertura por enquanto.")
        return None
    _dir(proj).mkdir(parents=True, exist_ok=True)
    aspect, dur, prompt, modelo = _aspect(), _duracao(), _prompt_movimento(), _modelo()
    log("    abertura: animando o thumbnail por IA (image->video, %s, %.1fs%s)..."
        % (aspect, dur, (", modelo=%s" % modelo) if modelo else ", auto-select"))
    try:
        from runner import rodar_claude
        from stages import magnific_seam
        rodar_claude(_instrucao(aspect, dur, prompt, modelo), proj.dir, log, cancel,
                     modelo="sonnet", allowed_tools=magnific_seam.allowed_tools_video())
    except Exception as e:
        log("    ⚠ abertura: geração falhou (%s) — P2 sem abertura." % e)
        return None
    clip = _dir(proj) / "abertura.mp4"
    if clip.exists() and clip.stat().st_size > 0:
        log("    abertura: clipe animado gerado (%s)." % clip.name)
        return clip
    log("    ⚠ abertura: geração não produziu abertura/abertura.mp4 — P2 sem abertura.")
    return None
