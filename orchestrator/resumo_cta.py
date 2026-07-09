# -*- coding: utf-8 -*-
"""resumo_cta.py — geração automática dos segmentos RESUMO P2 e CTA FINAL da montagem.

Substitui o drop-in fixo (resumo_p2.mp4 / CTA por-vídeo) por GERAÇÃO a cada vídeo, reusando
os CLIPES DO TEASER (as mídias Veo daquele vídeo) como visual — MESMA referência de
personagem, cenas SEMPRE diferentes. Decisão do editor (2026-07-09).

  RESUMO P2 — o TEXTO já está no roteiro (o bloco-gancho logo antes de "PARTE 2" / depois de
    "END OF PART 1"; ver roteiro_estrutura.resumo_parte2). Narra esse texto na voz do canal
    (TTS CapCut), distribui os clipes do teaser pela duração do áudio e sincroniza.
    Override manual: projects/<slug>/resumo_p2.txt.
  CTA — BASE FIXA por canal (materiais/<canal>/cta/cta_base.mp4): o ÁUDIO é fixo (a mesma
    chamada final sempre); só o VISUAL muda, reposto pelos clipes do teaser.

Legenda: NÃO é queimada aqui. O passo final da montagem (montagem_vertical._legendar)
re-transcreve o vídeo inteiro JÁ MONTADO e queima UMA legenda única (queimar aqui dobraria).
Então o "textinho na tela" da CTA aparece por essa legenda — desde que esteja FALADO no
áudio-base (o caso normal dessas CTAs narradas).

Falha graciosa: sem clipes de teaser, sem texto de resumo, ou sem base de CTA -> devolve None
(o chamador, montagem_vertical.construir, cai no drop-in legado `_drop_seg`).
"""
import os
import json
from pathlib import Path

import common
from common import achar_ffmpeg
import montagem_vertical as MV
import roteiro_estrutura


def _canal(proj):
    if proj.existe(proj.source):
        try:
            return (json.loads(proj.source.read_text(encoding="utf-8")).get("canal") or "").strip()
        except (OSError, ValueError):
            pass
    return ""


# ---------------------------------------------------------------------------
# Visual: clipes do teaser distribuídos pela duração-alvo
# ---------------------------------------------------------------------------

def _plano_cortes(clips, dur, alvo=3.0):
    """(sequência de clipes, durações) cobrindo EXATAMENTE `dur`: ~`alvo`s por corte, ciclando
    a lista de clipes (no máx 3 voltas) pra não repetir demais nem estourar."""
    if not clips or dur <= 0:
        return [], []
    n = max(1, min(len(clips) * 3, round(dur / max(1.0, alvo))))
    seq = [clips[i % len(clips)] for i in range(n)]
    return seq, [dur / n] * n


def _visual_teaser(ff, clips, dur, w, h, fps, out, log):
    seq, durs = _plano_cortes(clips, dur)
    if not seq:
        return False
    return MV._concat_videos_trim(ff, seq, durs, w, h, fps, out, log)


def _extrair_audio(ff, src, out, log):
    """Extrai a faixa de áudio de um vídeo em AAC uniforme (mesmo formato dos outros segmentos)."""
    MV._run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
             "-vn", *MV._AENC, str(out)], log, "cta-extrai-audio")
    return out.exists() and out.stat().st_size > 0


# ---------------------------------------------------------------------------
# TTS curto do resumo (voz do canal) — reusa a cadeia de fallback da Etapa 3
# ---------------------------------------------------------------------------

def _sintetizar(proj, texto, saida, log):
    from capcut_tts import garantir_sidecar, sintetizar, RateLimitError
    _nome, vid = common.voz_do_canal(_canal(proj), "f")
    primaria = vid or os.environ.get("CAPCUT_TTS_VOICE", common.VOZ_IDS.get("joanne", ""))
    fb = os.environ.get("LONGFORM_TTS_VOICE_FALLBACK", "cool_lady,labebe").split(",")
    cadeia = []
    for v in [primaria, *[x.strip() for x in fb]]:
        if v and v not in cadeia:
            cadeia.append(v)
    if not cadeia:
        return False
    base = garantir_sidecar(log=log)
    for v in cadeia:
        try:
            sintetizar(texto, v, str(saida), base=base, log=lambda *a, **k: None)
            log("    resumo P2: TTS ok (voz=%s, %d chars)." % (v, len(texto)))
            return True
        except RateLimitError:
            log("    ⚠ resumo P2: voz '%s' em rate-limit — próxima." % v)
        except SystemExit as e:
            log("    ⚠ resumo P2: TTS falhou (%s) — próxima voz." % e)
    return False


# ---------------------------------------------------------------------------
# RESUMO P2
# ---------------------------------------------------------------------------

def construir_resumo_p2(proj, base_mat, teaser_clips, w, h, fps, tmp, log, cancel=None):
    """Gera out/seg_90_resumo.mp4 = TTS do bloco-resumo do roteiro + clipes do teaser.
    Devolve o Path do segmento, ou None (o chamador cai no drop-in)."""
    ff = achar_ffmpeg()
    seg = tmp / "seg_90_resumo.mp4"
    if seg.exists() and seg.stat().st_size > 0:
        return seg
    if not teaser_clips:
        return None

    override = proj.dir / "resumo_p2.txt"
    if override.is_file() and override.read_text(encoding="utf-8", errors="replace").strip():
        texto = override.read_text(encoding="utf-8", errors="replace").strip()
        log("    resumo P2: usando override resumo_p2.txt (%d chars)." % len(texto))
    else:
        if not proj.existe(proj.roteiro):
            return None
        texto = roteiro_estrutura.resumo_parte2(
            proj.roteiro.read_text(encoding="utf-8", errors="replace")).strip()
        if not texto:
            log("    resumo P2: nenhum bloco-gancho detectado no roteiro.")
            return None
        log("    resumo P2: bloco-gancho do roteiro (%d chars)." % len(texto))

    aud = proj.dir / "_resumo_p2.mp3"
    if not (aud.exists() and aud.stat().st_size > 0):
        if not _sintetizar(proj, texto, aud, log):
            return None
    dur = MV._dur(ff, aud)
    if dur <= 0.3:
        log("    ⚠ resumo P2: áudio TTS vazio/curto — pulando geração.")
        return None

    vmudo = tmp / "_v_resumo.mp4"
    if not _visual_teaser(ff, teaser_clips, dur, w, h, fps, vmudo, log):
        return None
    ok = MV._mux(ff, vmudo, aud, seg, log)
    vmudo.unlink(missing_ok=True)
    if ok and seg.exists() and seg.stat().st_size > 0:
        log("    ✓ resumo P2 gerado (%.1fs, %d clipe(s) do teaser reaproveitados)."
            % (dur, len(teaser_clips)))
        return seg
    return None


# ---------------------------------------------------------------------------
# CTA FINAL
# ---------------------------------------------------------------------------

def _cta_base(base_mat, modelos):
    """Base fixa da CTA, em ordem: cta/cta_base.<vid> > 1º vídeo em cta/ > modelo cta_final.
    Varre a pasta do canal e, se vazia, a herdada (Rowan/Kay ← Lena). None se nada servir."""
    for base in common.materiais_dirs(base_mat.name):
        cta_dir = base / "cta"
        for p in sorted(cta_dir.glob("cta_base.*")):
            if p.suffix.lower() in MV.VIDEO_EXTS:
                return p
        vids = [p for p in sorted(cta_dir.glob("*"))
                if p.is_file() and p.suffix.lower() in MV.VIDEO_EXTS]
        if vids:
            return vids[0]
    m = (modelos or {}).get("cta_final")
    if m and Path(m).is_file() and Path(m).suffix.lower() in MV.VIDEO_EXTS:
        return Path(m)
    return None


def construir_cta(proj, base_mat, modelos, teaser_clips, w, h, fps, tmp, log, cancel=None):
    """Gera out/seg_91_cta.mp4 = áudio FIXO da base do canal + clipes do teaser como visual.
    Devolve o Path do segmento, ou None (o chamador cai no drop-in)."""
    ff = achar_ffmpeg()
    seg = tmp / "seg_91_cta.mp4"
    if seg.exists() and seg.stat().st_size > 0:
        return seg
    if not teaser_clips:
        return None
    base = _cta_base(base_mat, modelos)
    if not base:
        return None
    if not MV._tem_audio(ff, base):
        log("    ⚠ CTA: base '%s' sem áudio — geração impossível (visual ficaria mudo). "
            "Caindo no drop-in." % base.name)
        return None

    aud = tmp / "_a_cta_base.m4a"
    if not _extrair_audio(ff, base, aud, log):
        return None
    dur = MV._dur(ff, aud)
    if dur <= 0.3:
        aud.unlink(missing_ok=True)
        return None

    vmudo = tmp / "_v_cta.mp4"
    if not _visual_teaser(ff, teaser_clips, dur, w, h, fps, vmudo, log):
        aud.unlink(missing_ok=True)
        return None
    ok = MV._mux(ff, vmudo, aud, seg, log)
    vmudo.unlink(missing_ok=True)
    aud.unlink(missing_ok=True)
    if ok and seg.exists() and seg.stat().st_size > 0:
        log("    ✓ CTA gerada (base fixa '%s' %.1fs + %d clipe(s) do teaser)."
            % (base.name, dur, len(teaser_clips)))
        return seg
    return None
