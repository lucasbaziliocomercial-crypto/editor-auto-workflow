# -*- coding: utf-8 -*-
"""resumo_cta.py — geração automática dos segmentos RESUMO P2 e CTA FINAL da montagem.

Substitui o drop-in fixo (resumo_p2.mp4 / CTA por-vídeo) por GERAÇÃO a cada vídeo, reusando
os CLIPES DO TEASER (as mídias Veo daquele vídeo) como visual — MESMA referência de
personagem, cenas SEMPRE diferentes. Decisão do editor (2026-07-09).

TAKES INTEIROS (2026-07-09): os takes do VEO tocam COMPLETOS (ciclando a lista até cobrir o
áudio; o último fecha inteiro, o rabo do áudio vira silêncio) — NÃO são mais picotados em
cortes de ~3s (que deixavam tudo rápido demais e os vídeos incompletos). Ver _visual_completo.

  RESUMO P2 — o TEXTO já está no roteiro (o bloco-gancho logo antes de "PARTE 2" / depois de
    "END OF PART 1"; ver roteiro_estrutura.resumo_parte2). Narra esse texto na voz do canal
    (TTS CapCut) e mostra os takes do VEO inteiros por baixo. Override manual:
    projects/<slug>/resumo_p2.txt. VINHETA de abertura (a "cara" do resumo, fornecida pela
    editora) entra na frente: projects/<slug>/resumo/vinheta.* > materiais/<canal>/resumo/
    vinheta.* (aceita intro/abertura/bumper/card). Ver _vinheta.
  CTA — BASE FIXA por canal (materiais/<canal>/cta/cta_base.mp4): o ÁUDIO é fixo (a mesma
    chamada final sempre); só o VISUAL muda, reposto pelos takes do VEO inteiros.

Legenda: NÃO é queimada aqui. O passo final da montagem (montagem_vertical._legendar)
re-transcreve o vídeo inteiro JÁ MONTADO e queima UMA legenda única (queimar aqui dobraria).
Então o "textinho na tela" da CTA aparece por essa legenda — desde que esteja FALADO no
áudio-base (o caso normal dessas CTAs narradas).

Falha graciosa: sem clipes de teaser, sem texto de resumo, ou sem base de CTA -> devolve None
(o chamador, montagem_vertical.construir, cai no drop-in legado `_drop_seg`).
"""
import os
import json
import random
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


# ---------------------------------------------------------------------------
# Visual COMPLETO: cada take do VEO tocando INTEIRO (sem picotar)
# ---------------------------------------------------------------------------
# Decisão da editora (2026-07-09): no resumo/CTA os takes do VEO (teaser) devem aparecer
# COMPLETOS — nada de cortes de ~3s pra "caber" no áudio (o que deixava tudo rápido demais e os
# vídeos incompletos). A lista é ciclada até COBRIR o áudio; cada take entra na íntegra e o
# ÚLTIMO fecha completo (o rabo do áudio, se sobrar, vira silêncio no mux). Substitui o
# _visual_teaser (que picotava) SÓ no resumo/CTA — o ultimo_minuto continua com _plano_cortes.

def _ordem_embaralhada(clips, seed):
    """Ordem EMBARALHADA dos clipes do teaser (decisão da editora 2026-07-10: 'embaralhar tudo'
    — as mídias do resumo/CTA não devem sair na MESMA sequência do teaser). Determinística por
    `seed` (slug + tipo de segmento): re-render dá a mesma ordem (idempotente), mas resumo e CTA
    ficam com ordens DIFERENTES entre si e diferentes do teaser."""
    if not clips or len(clips) <= 2 or not seed:
        return list(clips)
    out = list(clips)
    random.Random(seed).shuffle(out)
    return out


def _plano_completo(ff, clips, dur_alvo, max_voltas=12):
    """Sequência de clipes (ciclando a lista) cujas durações NATURAIS somam >= dur_alvo, cada
    um INTEIRO (nunca cortado). Uma passada no mínimo; o teto evita loop infinito."""
    if not clips:
        return []
    durs = [max(0.3, MV._dur(ff, c)) for c in clips]
    seq, acc, i, teto = [], 0.0, 0, len(clips) * max(1, max_voltas)
    while acc < dur_alvo and len(seq) < teto:
        seq.append(clips[i % len(clips)])
        acc += durs[i % len(clips)]
        i += 1
    return seq or list(clips)


def _concat_full(ff, clips, w, h, fps, out, log):
    """Concatena os clipes JÁ INTEIROS (cada um enquadrado em w×h, mudo) — SEM trim.
    -c copy; re-encoda no fallback se os formatos divergirem."""
    if not clips:
        return False
    tmp = out.parent
    partes = []
    for k, c in enumerate(clips):
        pc = tmp / ("_full_%02d.mp4" % k)
        MV._video_ajustado(ff, c, None, w, h, fps, pc, log, mudo=True)  # dur=None => inteiro
        if pc.exists() and pc.stat().st_size > 0:
            partes.append(pc)
    if not partes:
        return False
    lista = tmp / "_full_concat.txt"
    lista.write_text("".join("file '%s'\n" % p.name for p in partes), encoding="utf-8")
    MV._run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(lista), "-c", "copy", str(out)], log, "full-concat")
    if not (out.exists() and out.stat().st_size > 0):
        MV._run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                 "-i", str(lista), *MV._venc_args(fps), "-an", str(out)], log, "full-concat-reenc")
    lista.unlink(missing_ok=True)
    for p in partes:
        p.unlink(missing_ok=True)
    return out.exists() and out.stat().st_size > 0


def _visual_completo(ff, clips, dur_alvo, w, h, fps, out, log, seed=None):
    """Vídeo mudo = takes do VEO INTEIROS, EMBARALHADOS (por `seed`), ciclando até cobrir
    dur_alvo."""
    seq = _plano_completo(ff, _ordem_embaralhada(clips, seed), dur_alvo)
    if not seq:
        return False
    return _concat_full(ff, seq, w, h, fps, out, log)


def _mux_video_lead(ff, video_mudo, audio, out, log):
    """Junta vídeo (INTEIRO, manda na duração) + áudio, preenchendo o rabo do áudio com
    silêncio (apad) quando ele é mais curto que o vídeo — assim o último take fecha COMPLETO
    em vez de ser cortado pelo -shortest (o vídeo, finito, é quem termina)."""
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(video_mudo), "-i", str(audio),
           "-filter_complex", "[1:a]apad[a]", "-map", "0:v:0", "-map", "[a]",
           "-c:v", "copy", *MV._AENC, "-shortest", str(out)]
    MV._run(cmd, log, "mux-video-lead")
    return out.exists() and out.stat().st_size > 0


# ---------------------------------------------------------------------------
# Vinheta: a "cara" que ANUNCIA o segmento (fornecida pela editora)
# ---------------------------------------------------------------------------
# A editora larga um clipe-vinheta e a esteira só troca a mídia de fundo atrás dele (porta a
# ideia do template CapCut do Romance Maker: vinheta fixa + faixa de vídeo trocável). Sem a
# vinheta o resumo somia no meio do vídeo ("não vi aparecer"). Resolvido por-vídeo > por-canal.

_VIN_ALIASES = ("vinheta", "intro", "abertura", "bumper", "card")


def _vinheta(proj, base_mat, tipo):
    """Clipe-vinheta que abre o segmento: projects/<slug>/<tipo>/(vinheta|intro|...).<ext> >
    materiais/<canal>/<tipo>/... (+ herdado). `tipo` = 'resumo' ou 'cta'. None se não houver."""
    dirs = [proj.dir / tipo] + [d / tipo for d in common.materiais_dirs(base_mat.name)]
    for d in dirs:
        if not d.is_dir():
            continue
        for al in _VIN_ALIASES:
            for p in sorted(d.glob(al + ".*")):
                if p.is_file() and p.suffix.lower() in MV.VIDEO_EXTS:
                    return p
    return None


def _seg_de_clipe(ff, clip, w, h, fps, out, log):
    """Transforma um clipe (a vinheta) num segmento uniforme (vídeo+áudio em w×h). Mantém o
    áudio próprio; se não tiver, gera silêncio da mesma duração (fica compatível pra concat)."""
    if MV._tem_audio(ff, clip):
        MV._video_ajustado(ff, clip, None, w, h, fps, out, log, mudo=False)
        return out.exists() and out.stat().st_size > 0
    vmudo = out.parent / ("_v_" + out.stem + ".mp4")
    MV._video_ajustado(ff, clip, None, w, h, fps, vmudo, log, mudo=True)
    aud = out.parent / ("_a_" + out.stem + ".m4a")
    MV._silencio(ff, MV._dur(ff, vmudo), aud, log)
    ok = MV._mux(ff, vmudo, aud, out, log)
    vmudo.unlink(missing_ok=True)
    aud.unlink(missing_ok=True)
    return ok


def _concat_segs(ff, partes, fps, out, log):
    """Concatena segmentos (vídeo+áudio) uniformes -> out (ex.: [vinheta, corpo]).
    -c copy; re-encoda no fallback."""
    partes = [p for p in partes if p and p.exists() and p.stat().st_size > 0]
    if not partes:
        return False
    if len(partes) == 1:
        import shutil
        shutil.copyfile(partes[0], out)
        return out.exists() and out.stat().st_size > 0
    lista = out.parent / ("_" + out.stem + "_segs.txt")
    lista.write_text("".join("file '%s'\n" % p.name for p in partes), encoding="utf-8")
    MV._run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(lista), "-c", "copy", str(out)], log, "seg-concat")
    if not (out.exists() and out.stat().st_size > 0):
        MV._run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                 "-i", str(lista), *MV._venc_args(fps), *MV._AENC, str(out)], log, "seg-concat-reenc")
    lista.unlink(missing_ok=True)
    return out.exists() and out.stat().st_size > 0


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

    # CORPO: takes do VEO INTEIROS, EMBARALHADOS (ciclando até cobrir a narração) — nada de picotar.
    body_v = tmp / "_v_resumo.mp4"
    if not _visual_completo(ff, teaser_clips, dur, w, h, fps, body_v, log,
                            seed="resumo:" + proj.dir.name):
        return None
    body = tmp / "_resumo_body.mp4"
    # A mídia TERMINA JUNTO com a narração (item da editora 2026-07-10: 'quando acaba o texto do
    # resumo, a mídia continua até o fim dela — que termine junto com o áudio'). MV._mux usa
    # -shortest: as cenas (que somam >= a narração) são APARADAS na duração do áudio. (Antes o
    # _mux_video_lead deixava o vídeo liderar e o áudio ganhava silêncio no fim → mídia sobrava.)
    ok = MV._mux(ff, body_v, aud, body, log)
    body_v.unlink(missing_ok=True)
    if not (ok and body.exists() and body.stat().st_size > 0):
        return None

    # VINHETA (a "cara" do resumo) — fornecida pela editora; abre o segmento.
    partes = []
    vin = _vinheta(proj, base_mat, "resumo")
    if vin:
        vin_seg = tmp / "_resumo_vinheta.mp4"
        if _seg_de_clipe(ff, vin, w, h, fps, vin_seg, log):
            partes.append(vin_seg)
            log("    resumo P2: vinheta de abertura '%s' (%.1fs)." % (vin.name, MV._dur(ff, vin_seg)))
    else:
        log("    resumo P2: sem vinheta — largue materiais/%s/resumo/vinheta.mp4 (ou "
            "projects/<slug>/resumo/vinheta.mp4) p/ anunciar o resumo." % base_mat.name)
    partes.append(body)

    ok = _concat_segs(ff, partes, fps, seg, log)
    (tmp / "_resumo_vinheta.mp4").unlink(missing_ok=True)
    body.unlink(missing_ok=True)
    if ok and seg.exists() and seg.stat().st_size > 0:
        log("    ✓ resumo P2 gerado (narração %.1fs + %d take(s) do VEO INTEIROS%s)."
            % (dur, len(teaser_clips), " + vinheta" if vin else ""))
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


# ---------------------------------------------------------------------------
# Layout da CTA: cenas ao fundo (opacidade) + LEGENDA CENTRAL gerada (item da editora 2026-07-10)
# ---------------------------------------------------------------------------
# A editora pediu p/ 'adaptar a CTA ao modelo enviado: texto no meio da tela e cenas ao fundo com
# opacidade em 80%'. Então: as cenas do teaser entram escurecidas (80% opacidade sobre preto) e o
# TEXTO da CTA aparece CENTRALIZADO — gerado do próprio áudio-base via Whisper (a fala fixa da CTA)
# e queimado no meio da tela. O áudio continua o fixo do cta_base. A montagem protege este segmento
# da legenda global re-transcrita (detalhe_segs), então NÃO há legenda dupla (a de baixo + a central).

def _cta_opacidade():
    """Opacidade das cenas ao fundo da CTA (0..1). Default 0.8 (a editora pediu 80%).
    Env: ROTEIRO_CTA_OPACITY."""
    try:
        return max(0.05, min(1.0, float(os.environ.get("ROTEIRO_CTA_OPACITY", "0.8"))))
    except (TypeError, ValueError):
        return 0.8


def _aplicar_opacidade(ff, src, op, fps, out, log):
    """Re-encoda `src` multiplicando o vídeo por `op` (0..1) — 'cenas ao fundo a op×100% de
    opacidade' sobre preto. Vídeo MUDO. True em sucesso. `colorlevels` com o teto de saída em `op`
    escala cada canal por `op` (preto continua preto; o brilho cai p/ op×) — o efeito de opacidade
    sobre fundo preto, numa passada só."""
    op = max(0.05, min(1.0, op))
    vf = "colorlevels=romax=%.3f:gomax=%.3f:bomax=%.3f" % (op, op, op)
    MV._run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
             "-vf", vf, *MV._venc_args(fps), "-an", str(out)], log, "cta-opacidade")
    return out.exists() and out.stat().st_size > 0


def _legenda_central_cta(proj, ff, video, audio, w, h, fps, tmp, log):
    """Transcreve o áudio-base da CTA (Whisper) e queima a legenda CENTRALIZADA (meio da tela)
    sobre `video`. Devolve o Path legendado (mudo) ou None (o chamador segue sem — vídeo cru).
    Reusa o fatiamento de UMA linha (MV._preparar_ass) e a fonte/cor da casa, só troca o
    alinhamento p/ Alignment=5 (meio-centro). `video` e a saída ficam em `tmp` (subtitles roda
    com cwd=tmp e nomes relativos p/ escapar o ':' do Windows)."""
    import shutil
    try:
        srt = MV._whisper_srt(proj, Path(audio), tmp, log)
    except Exception as e:
        log("    ⚠ CTA: Whisper falhou (%s) — sem legenda central." % e)
        return None
    if not srt:
        log("    ⚠ CTA: sem SRT do áudio-base (Whisper) — sem legenda central.")
        return None
    try:
        ass = MV._preparar_ass(ff, srt, MV._caption_max_chars(), w, h)
    except Exception as e:
        log("    ⚠ CTA: falha ao preparar a legenda central (%s)." % e)
        return None
    fs = os.environ.get("ROTEIRO_CTA_CAPTION_FONTSIZE", str(max(44, int(h * 0.070))))
    # FONTE e COR PRÓPRIAS da CTA (item da editora 2026-07-10: 'o texto do CTA deve ser centralizado
    # e com OUTRA FONTE e COR'). Default = distinto do corpo (corpo = Times amarelo): fonte da casa
    # 'Playfair Display' + BRANCO. Ambos overridáveis por env. Alignment=5 = centro-centro (já era).
    cta_font = (os.environ.get("ROTEIRO_CTA_CAPTION_FONT", "") or "Playfair Display").strip()
    _cor = (os.environ.get("ROTEIRO_CTA_CAPTION_COLOR", "") or "").strip()
    _apel = {"amarelo": "&H0000FFFF", "yellow": "&H0000FFFF",
             "branco": "&H00FFFFFF", "white": "&H00FFFFFF"}
    cta_cor = _apel.get(_cor.lower(), _cor) or "&H00FFFFFF"
    style = ("FontName=%s,Fontsize=%s,Bold=1,PrimaryColour=%s,OutlineColour=&H00000000,"
             "BackColour=&H64000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=5,MarginV=0"
             % (cta_font, fs, cta_cor))
    fontsdir_frag = ""
    try:
        _ff_file = MV._fonte_legenda_arquivo(cta_font)
        if _ff_file:
            _fdir = tmp / "_fonts"; _fdir.mkdir(exist_ok=True)
            _dest = _fdir / _ff_file.name
            if not _dest.exists():
                shutil.copyfile(_ff_file, _dest)
            fontsdir_frag = ":fontsdir=_fonts"
    except Exception:
        pass
    out = tmp / "_v_cta_capd.mp4"
    sub_f = "subtitles=%s%s:force_style='%s'" % (ass.name, fontsdir_frag, style)
    MV._run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", Path(video).name,
             "-vf", sub_f, *MV._venc_args(fps), "-an", out.name], log, "cta-legenda-central", cwd=tmp)
    if out.exists() and out.stat().st_size > 0:
        log("    ✓ CTA: legenda central gerada (Whisper do áudio-base, centralizada; fonte=%s, cor=%s)."
            % (cta_font, cta_cor))
        return out
    log("    ⚠ CTA: falha ao queimar a legenda central — seguindo sem.")
    return None


def construir_cta(proj, base_mat, modelos, teaser_clips, w, h, fps, tmp, log, cancel=None):
    """Gera out/seg_91_cta.mp4 = áudio FIXO da base do canal + cenas do teaser ao fundo (a
    _cta_opacidade) + LEGENDA CENTRAL gerada do áudio-base (item 5 da editora: 'texto no meio,
    cenas ao fundo a 80%'). Devolve o Path do segmento, ou None (o chamador cai no drop-in).
    A mídia termina JUNTO com o áudio-base (MV._mux -shortest)."""
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

    # VISUAL: takes do VEO INTEIROS, EMBARALHADOS, cobrindo o áudio fixo da CTA (sem picotar).
    vmudo = tmp / "_v_cta.mp4"
    if not _visual_completo(ff, teaser_clips, dur, w, h, fps, vmudo, log,
                            seed="cta:" + proj.dir.name):
        aud.unlink(missing_ok=True)
        return None

    # 80% de opacidade nas cenas ao fundo (item 5). Falha → segue com as cenas cheias.
    op = _cta_opacidade()
    vbase = vmudo
    if op < 0.999:
        vdark = tmp / "_v_cta_dark.mp4"
        if _aplicar_opacidade(ff, vmudo, op, fps, vdark, log):
            vbase = vdark

    # LEGENDA CENTRAL gerada (Whisper do áudio-base, centralizada). Falha → vídeo sem legenda
    # (a montagem NÃO queima a global por cima: a CTA está em detalhe_segs).
    vcap = _legenda_central_cta(proj, ff, vbase, aud, w, h, fps, tmp, log)
    vfinal = vcap or vbase

    ok = MV._mux(ff, vfinal, aud, seg, log)   # mídia termina junto com o áudio-base
    for p in (vmudo, tmp / "_v_cta_dark.mp4", tmp / "_v_cta_capd.mp4"):
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass
    aud.unlink(missing_ok=True)
    if ok and seg.exists() and seg.stat().st_size > 0:
        log("    ✓ CTA gerada (áudio-base '%s' %.1fs + cenas a %d%% + legenda central%s)."
            % (base.name, dur, int(op * 100), "" if vcap else " (falhou → sem)"))
        return seg
    return None
