# -*- coding: utf-8 -*-
"""ultimo_minuto.py — geração do segmento ÚLTIMO MINUTO (retenção pós-CTA).

Depois da CTA, o vídeo continua rolando por ~1 min de "tela morta" que mantém o watch-time
alto enquanto o espectador vai aos comentários / decide ver a Parte 2. Reusa os CLIPES DO
TEASER (as mídias Veo daquele vídeo) como visual, igual resumo_cta.py.

Dois templates (horizontais 1920x1080, o vídeo troca de "cara" no fim — decisão do editor):

  COMMENTS  — tela de comentários com o pin "The link will be here." + cursor animado.
              Reforça, logo após a CTA, ONDE está o link. Vai como está (loopada até
              `ROTEIRO_UM_COMMENTS_DUR`s), sem overlay.
  PART2     — card "You won't believe what he did... Part 2" com um QUADRADO VAZIO no meio.
              Os clipes do teaser tocam DENTRO do quadrado, ciclando entre todos (varia as
              cenas) pra preencher o resto do minuto.

Os cards são 16:9; a saída da esteira é 9:16 — então cada card entra CENTRALIZADO
(letterbox preto em cima/embaixo) no canvas vertical, preservando o formato horizontal.

Ordem/duração por env:
  ROTEIRO_ULTIMO_MINUTO=0        desliga (default ligado).
  ROTEIRO_ULTIMO_MINUTO_DUR=60   duração-alvo total do segmento (s).
  ROTEIRO_UM_COMMENTS_DUR=8      quanto dura a tela de comentários (resto vai pro card).
  ROTEIRO_UM_ORDER=comments,card ordem dos dois blocos.
  ROTEIRO_UM_RECT=100,260,1720,720   retângulo (x,y,w,h) do quadrado no card, em 1920x1080.
  ROTEIRO_UM_BG=black            cor da tarja do letterbox.

Templates (1º que existir): projects/<slug>/ultimo_minuto/ > materiais/<canal>/ultimo_minuto/
> assets/ultimo_minuto/ (default global do repo). Nomes: part2.* / comments.* (+ apelidos).

Falha graciosa: sem nenhum template -> None (o chamador só não adiciona o segmento).
Sem clipes de teaser: o card ainda entra (só sem os clipes no quadrado).
"""
import os
from pathlib import Path

import common
from common import achar_ffmpeg
import montagem_vertical as MV
from resumo_cta import _plano_cortes


_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "ultimo_minuto"
_ALIASES = {
    "part2": ("part2", "parte2", "card", "part_2"),
    "comments": ("comments", "coments", "comentarios", "comentários", "link"),
}


def _f(k, d):
    try:
        return float(os.environ.get(k, d))
    except ValueError:
        return float(d)


def _ligado():
    return os.environ.get("ROTEIRO_ULTIMO_MINUTO", "1").strip().lower() not in (
        "0", "off", "none", "nao", "não", "no", "false")


def _rect():
    try:
        x, y, w, h = (int(float(v)) for v in os.environ.get(
            "ROTEIRO_UM_RECT", "100,260,1720,720").split(","))
        return x, y, w, h
    except (ValueError, TypeError):
        return 100, 260, 1720, 720


def _tpl(proj, base_mat, nome):
    """Acha o template `nome` (por-vídeo > por-canal > herdado > global). None se não houver."""
    dirs = ([proj.dir / "ultimo_minuto"]
            + [d / "ultimo_minuto" for d in common.materiais_dirs(base_mat.name)]
            + [_ASSETS])
    for d in dirs:
        if not d.is_dir():
            continue
        for apelido in _ALIASES.get(nome, (nome,)):
            for p in sorted(d.glob(apelido + ".*")):
                if p.is_file() and p.suffix.lower() in MV.VIDEO_EXTS:
                    return p
    return None


def _letterbox(w, h, bg):
    """Normaliza a fonte em 1920x1080 (cover) e a encaixa CENTRALIZADA em w×h (letterbox)."""
    return ("scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,"
            "scale=%d:-2,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=%s,setsar=1" % (w, w, h, bg))


def _bloco_comments(ff, tpl, dur, w, h, fps, bg, out, log):
    """Tela de comentários loopada até `dur`, letterboxed em w×h (mudo)."""
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-stream_loop", "-1", "-t", "%.3f" % max(0.3, dur), "-i", str(tpl),
           "-vf", _letterbox(w, h, bg), *MV._venc_args(fps), "-an", str(out)]
    MV._run(cmd, log, "um-comments")
    return out.exists() and out.stat().st_size > 0


def _bloco_card(ff, tpl, clips, dur, w, h, fps, bg, tmp, log):
    """Card Part 2 loopado até `dur` com os clipes do teaser tocando no quadrado, letterboxed
    em w×h (mudo). Devolve o Path do bloco (ou None)."""
    out = tmp / "_v_um_card.mp4"
    rx, ry, rw, rh = _rect()
    strip = None
    if clips:
        seq, durs = _plano_cortes(clips, dur)
        if seq:
            strip = tmp / "_v_um_clips.mp4"
            if not MV._concat_videos_trim(ff, seq, durs, rw, rh, fps, strip, log):
                strip = None
    if strip is not None:
        fc = ("[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
              "setsar=1,fps=%d[bg];[bg][1:v]overlay=%d:%d[cmp];"
              "[cmp]scale=%d:-2,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=%s,setsar=1[v]"
              % (fps, rx, ry, w, w, h, bg))
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
               "-stream_loop", "-1", "-t", "%.3f" % max(0.3, dur), "-i", str(tpl),
               "-i", str(strip), "-filter_complex", fc, "-map", "[v]",
               *MV._venc_args(fps), "-an", str(out)]
        MV._run(cmd, log, "um-card")
        strip.unlink(missing_ok=True)
    else:
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
               "-stream_loop", "-1", "-t", "%.3f" % max(0.3, dur), "-i", str(tpl),
               "-vf", _letterbox(w, h, bg), *MV._venc_args(fps), "-an", str(out)]
        MV._run(cmd, log, "um-card-sem-clipes")
    return out if (out.exists() and out.stat().st_size > 0) else None


def _concat_mudo(ff, partes, fps, out, log):
    """Concatena os blocos mudos (-c copy; re-encoda se os formatos divergirem)."""
    if len(partes) == 1:
        import shutil
        shutil.copyfile(partes[0], out)
        return out.exists() and out.stat().st_size > 0
    lista = out.parent / "_um_concat.txt"
    lista.write_text("".join("file '%s'\n" % p.name for p in partes), encoding="utf-8")
    MV._run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(lista), "-c", "copy", str(out)], log, "um-concat")
    if not (out.exists() and out.stat().st_size > 0):
        MV._run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                 "-i", str(lista), *MV._venc_args(fps), "-an", str(out)], log, "um-concat-reenc")
    lista.unlink(missing_ok=True)
    return out.exists() and out.stat().st_size > 0


def construir_ultimo_minuto(proj, base_mat, teaser_clips, w, h, fps, tmp, log, cancel=None):
    """Gera out/seg_92_ultimo.mp4 = comments + card(Part 2 c/ clipes do teaser), ~1 min.
    Devolve o Path do segmento, ou None (nada a adicionar)."""
    ff = achar_ffmpeg()
    seg = tmp / "seg_92_ultimo.mp4"
    if seg.exists() and seg.stat().st_size > 0:
        return seg
    if not _ligado():
        return None

    tpl_comments = _tpl(proj, base_mat, "comments")
    tpl_card = _tpl(proj, base_mat, "part2")
    if not tpl_comments and not tpl_card:
        return None

    total = max(2.0, _f("ROTEIRO_ULTIMO_MINUTO_DUR", 60))
    bg = os.environ.get("ROTEIRO_UM_BG", "black").strip() or "black"
    dur_c = min(_f("ROTEIRO_UM_COMMENTS_DUR", 8), total) if tpl_comments else 0.0
    dur_card = (total - dur_c) if tpl_card else 0.0
    if tpl_card and dur_card < 1.0:            # card existe mas sobrou pouco -> divide melhor
        dur_c, dur_card = total * 0.15, total * 0.85
        if not tpl_comments:
            dur_c, dur_card = 0.0, total
    if not tpl_card:                            # só comments -> ele leva o minuto todo
        dur_c = total

    partes, feito = {}, []
    if cancel is not None and cancel.is_set():
        raise MV.ErroPipeline("Cancelado pelo usuário.")
    if tpl_comments and dur_c > 0.2:
        b = tmp / "_v_um_comments.mp4"
        if _bloco_comments(ff, tpl_comments, dur_c, w, h, fps, bg, b, log):
            partes["comments"] = b
            feito.append("comments %.0fs" % dur_c)
    if tpl_card and dur_card > 0.2:
        b = _bloco_card(ff, tpl_card, teaser_clips, dur_card, w, h, fps, bg, tmp, log)
        if b:
            partes["card"] = b
            feito.append("card %.0fs (%d clipe(s))" % (dur_card, len(teaser_clips or [])))
    if not partes:
        return None

    ordem = [x.strip() for x in os.environ.get("ROTEIRO_UM_ORDER", "comments,card").split(",")]
    seq = [partes[k] for k in ordem if k in partes] or list(partes.values())

    vmudo = tmp / "_v_um.mp4"
    ok = _concat_mudo(ff, seq, fps, vmudo, log)
    for p in partes.values():
        p.unlink(missing_ok=True)
    if not ok:
        return None
    dur = MV._dur(ff, vmudo)
    aud = tmp / "_a_um.m4a"
    MV._silencio(ff, dur, aud, log)
    ok = MV._mux(ff, vmudo, aud, seg, log)
    vmudo.unlink(missing_ok=True)
    aud.unlink(missing_ok=True)
    if ok and seg.exists() and seg.stat().st_size > 0:
        log("    ✓ último minuto gerado (%.0fs: %s)." % (dur, " + ".join(feito)))
        return seg
    return None
