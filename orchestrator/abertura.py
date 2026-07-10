# -*- coding: utf-8 -*-
"""abertura.py — CENA DE ABERTURA (antes do teaser), P1 e P2. 2026-07-10.

TODO vídeo abre com uma CENA ANIMADA antes do teaser (na isca P1) / como 1º segmento (na P2). A
fonte PADRÃO dessa cena é um take do PRÓPRIO teaser que TENHA ÁUDIO — o diálogo já gravado no clipe
do VEO — tocado COM esse som (a montagem detecta o áudio via ffprobe e passa o candidato aqui). É a
cara falada do vídeo abrindo antes do gancho mudo do teaser. Vale pra TODAS as categorias.

CONTRATO: `garantir_clip(proj, log, cancel, teaser_com_audio=None, permitir_ia=False)` devolve o
Path do clipe de abertura (vídeo), ou None quando não há fonte. A montagem trata None graciosamente
(vídeo abre direto no teaser / na capa, como antes).

FONTES na ordem de prioridade (a 1ª que existir vence):
  1. Clipe LARGADO pela editora em `projects/<slug>/abertura/` (qualquer .mp4/.mov/…, menos o
     abertura.mp4 gerado) — override manual: se ela quer escolher a cena à mão, é só largar ali.
  2. Take do TEASER com ÁUDIO (`teaser_com_audio`, resolvido pela montagem) — a FONTE PADRÃO.
  3. Clipe JÁ GERADO `projects/<slug>/abertura/abertura.mp4` (idempotência do caminho de IA antigo).
  4. GERAÇÃO por IA (image->video do thumbnail) — só quando `permitir_ia` (hoje: P2) E a trava de
     crédito ROTEIRO_ABERTURA_VIDEO_OK=1. Fallback legado pra quando não há take de teaser com áudio.

Custo: video_generate COBRA crédito (nenhum modelo é ilimitado via MCP). Por isso a geração por IA
é opt-in explícito; o caminho padrão (take do teaser) não custa nada.
"""

import os
from pathlib import Path

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


def _on():
    """Abertura LIGADA por padrão (P1 e P2 chamam). Desliga com ROTEIRO_ABERTURA=0/off."""
    return os.environ.get("ROTEIRO_ABERTURA", "1").strip().lower() not in (
        "0", "off", "none", "nao", "não", "no", "false")


def _credito_ok():
    """Geração por IA liberada? (opt-in explícito — video_generate cobra crédito). Um clipe já
    pronto/ largado pela editora NÃO passa por aqui; a trava só vale pra DISPARAR a geração."""
    return os.environ.get("ROTEIRO_ABERTURA_VIDEO_OK", "0").strip() == "1"


def _aspect():
    return os.environ.get("ROTEIRO_ASPECT", "16:9").strip() or "16:9"


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


def _drop_editora(proj):
    """Clipe que a EDITORA largou em projects/<slug>/abertura/ (override manual): qualquer vídeo
    que NÃO seja o abertura.mp4 gerado por IA. 1º alfabético. None se não houver."""
    d = _dir(proj)
    if not d.is_dir():
        return None
    vids = sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTS
                  and p.name.lower() != "abertura.mp4" and p.stat().st_size > 0)
    return vids[0] if vids else None


def selecionar_teaser_com_audio(clips, tem_audio):
    """1º clipe do teaser que TEM faixa de áudio (o diálogo já gravado no take do VEO) — a fonte
    PADRÃO da cena de abertura. `tem_audio(path)->bool` é injetado pela montagem (usa ffprobe).
    None se nenhum clipe tiver áudio."""
    for c in clips or []:
        try:
            if tem_audio(c):
                return Path(c)
        except Exception:
            continue
    return None


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


def garantir_clip(proj, log, cancel=None, teaser_com_audio=None, permitir_ia=False):
    """Devolve o Path do clipe da CENA DE ABERTURA (vídeo) ou None. Ordem de prioridade:
    drop da editora > take do teaser com áudio > abertura.mp4 gerada > geração por IA (só se
    `permitir_ia` + trava de crédito). Ver o docstring do módulo."""
    if not _on():
        log("    abertura: desligada (ROTEIRO_ABERTURA=0) — sem cena de abertura.")
        return None
    # 1) drop manual da editora (override)
    drop = _drop_editora(proj)
    if drop is not None:
        log("    abertura: usando clipe largado pela editora (%s)." % drop.name)
        return drop
    # 2) take do TEASER com ÁUDIO — a fonte PADRÃO (o diálogo do próprio vídeo)
    if teaser_com_audio is not None:
        ta = Path(teaser_com_audio)
        if ta.is_file() and ta.stat().st_size > 0:
            log("    abertura: cena animada = take do teaser com diálogo (%s)." % ta.name)
            return ta
    # 3) abertura.mp4 já gerada por IA (idempotência do caminho antigo)
    ger = _dir(proj) / "abertura.mp4"
    if ger.is_file() and ger.stat().st_size > 0:
        log("    abertura: usando clipe gerado (%s)." % ger.name)
        return ger
    # 4) geração por IA do thumbnail — fallback legado, só quando permitido (P2) e liberado
    if not permitir_ia:
        log("    abertura: sem take de teaser com áudio nem clipe largado — sem cena de abertura.")
        return None
    if not proj.existe(proj.thumb_ref):
        log("    abertura: sem thumb_ref.png na pasta — sem cena de abertura.")
        return None
    if not _credito_ok():
        log("    abertura: geração por IA TRAVADA (video_generate cobra crédito). Para ligar, "
            "exporte ROTEIRO_ABERTURA_VIDEO_OK=1 — ou largue um clipe em projects/<slug>/abertura/, "
            "ou garanta um take de teaser com áudio. Sem cena de abertura por enquanto.")
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
        log("    ⚠ abertura: geração falhou (%s) — sem cena de abertura." % e)
        return None
    clip = _dir(proj) / "abertura.mp4"
    if clip.exists() and clip.stat().st_size > 0:
        log("    abertura: clipe animado gerado (%s)." % clip.name)
        return clip
    log("    ⚠ abertura: geração não produziu abertura/abertura.mp4 — sem cena de abertura.")
    return None
