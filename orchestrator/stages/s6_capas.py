# -*- coding: utf-8 -*-
"""Etapa 6 — Capas de capítulo (troca de capítulo).

Porta o `covers.py` do Romance Maker (vendorizado em orchestrator/covers.py): para cada
capítulo gera covers/capa_NN.mp4 — um clipe com zoom-in lento (Ken Burns) no fundo + o título
do capítulo com fade-in. É o cartão que entra na montagem (Etapa 7) no fim de cada capítulo.

Título de cada capa = prompts_capas.txt (Etapa 4) — um título por capítulo, na ordem, no
padrão "Chapter NN — <Título>".

REFERÊNCIA DO CANAL (modelos.json -> capa_ref): UM vídeo de troca de capítulo já pronto que
o editor anexa 1x. A automação REPLICA o mesmo formato a cada capítulo — mesma animação
(Ken Burns + fade-in do covers.py), mesma fonte (The Seasons embutida) e, automaticamente,
a mesma DURAÇÃO e FPS (lidos da referência via ffprobe). Só muda o título e o fundo.

Fundo de cada capa = a 1ª imagem daquele capítulo (images/img_NNN.png) com overlay escuro;
sem imagem, cor sólida. (Não há fundo fixo por canal — o fundo é sempre dinâmico.)

Demais specs (zoom, fonte, tamanho, overlay) vêm das envs ROTEIRO_COVER_* e ROTEIRO_W/H.

Contrato: run(proj, log, cancel, **kw). Idempotente (âncora = covers/*.mp4).
"""

import os
import re
import json

import covers
from common import ErroPipeline, materiais_canal
import roteiro_estrutura


def _cfg():
    """Monta o dict cfg do covers.py a partir das envs ROTEIRO_*."""
    def _f(k, d):
        try:
            return float(os.environ.get(k, d))
        except ValueError:
            return float(d)
    def _i(k, d):
        try:
            return int(float(os.environ.get(k, d)))
        except ValueError:
            return int(d)
    w = _i("ROTEIRO_W", 1920)
    h = _i("ROTEIRO_H", 1080)
    # Tamanho-base do título. É um TETO: títulos longos encolhem sozinhos (auto-fit do covers).
    # No formato HORIZONTAL o título fica proporcionalmente maior (~5,2% da largura ≈ 100px em
    # 1920, batendo com a referência da Lena); no vertical mantém o padrão.
    font_size = _i("ROTEIRO_COVER_FONT_SIZE", 84)
    if w > h:
        font_size = max(font_size, round(w * 0.052))
    return {
        "width":        w,
        "height":       h,
        "fps":          _i("ROTEIRO_COVER_FPS", 30),
        "duration_s":   _f("ROTEIRO_COVER_DURACAO_S", 5),
        "fade_in_s":    _f("ROTEIRO_COVER_FADE_IN_S", 0.6),
        "zoom":         _f("ROTEIRO_COVER_ZOOM", 1.1),
        "font_size":    font_size,
        # Espaçamento entre linhas do título. Ajustado p/ bater com o vídeo-referência
        # (linhas mais juntas). Afinável por env sem mexer no código.
        "line_spacing": _f("ROTEIRO_COVER_LINE_SPACING", 1.05),
        "overlay_alpha": _f("ROTEIRO_COVER_OVERLAY_ALPHA", 0.45),
        "font_path":    os.environ.get("ROTEIRO_COVER_FONT", "").strip(),
        "use_chapter_image_as_bg": True,
    }


def _titulos(proj):
    if proj.existe(proj.prompts_capas):
        ts = [l.strip() for l in proj.prompts_capas.read_text(encoding="utf-8", errors="replace").splitlines()
              if l.strip()]
        if ts:
            return ts
    return roteiro_estrutura.titulos_capas(proj.roteiro.read_text(encoding="utf-8", errors="replace"))


def _titulo_capa(i, titulo):
    """Texto da capa no padrão da referência do canal: 'Chapter NN — <Título>'.

    Se o título já vier rotulado como capítulo (fallback 'Chapter N' quando o roteiro
    não trouxe título), não duplica o prefixo."""
    t = (titulo or "").strip()
    if t.lower().startswith("chapter"):
        return t
    return "Chapter %02d — %s" % (i, t)


def _canal(proj):
    if proj.existe(proj.source):
        try:
            return (json.loads(proj.source.read_text(encoding="utf-8")).get("canal") or "").strip()
        except (OSError, ValueError):
            pass
    return ""


def _envf(k, d):
    try:
        return float(os.environ.get(k, d))
    except (TypeError, ValueError):
        return float(d)


def _cover_narrar_on():
    v = os.environ.get("ROTEIRO_COVER_NARRAR_TITULO", "1").strip().lower()
    return v not in ("0", "off", "nao", "não", "no", "false")


def _titulo_durs(proj):
    """{n: dur_s} das narrações de título (capitulos.json 'titulo_dur', gravado pela Etapa 3).
    Vazio se ausente/desligado. A duração da capa do capítulo N passa a ser essa fala +
    respiro (com piso/teto), pra a capa durar exatamente o tempo do 'Chapter N — Título'."""
    p = proj.dir / "capitulos.json"
    if not proj.existe(p):
        return {}
    try:
        caps = json.loads(p.read_text(encoding="utf-8")).get("capitulos", [])
    except (OSError, ValueError):
        return {}
    out = {}
    for c in caps:
        d = c.get("titulo_dur")
        if isinstance(d, (int, float)) and d > 0:
            try:
                out[int(c.get("n"))] = float(d)
            except (TypeError, ValueError):
                pass
    return out


def _ref_duracao_fps(ref):
    """(duração_s, fps) do vídeo de referência da troca de capítulo, via ffprobe. Assim a capa
    gerada sai com o MESMO comprimento/cadência da referência. (None, None) se não der."""
    try:
        import subprocess
        from pathlib import Path
        from common import achar_ffmpeg, SUBPROCESS_FLAGS
        ffprobe = str(Path(achar_ffmpeg()).with_name("ffprobe" + Path(achar_ffmpeg()).suffix))
        r = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate:format=duration",
             "-of", "default=noprint_wrappers=1", str(ref)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **SUBPROCESS_FLAGS)
        dur = fps = None
        for linha in (r.stdout or b"").decode("utf-8", "replace").splitlines():
            k, _, v = linha.partition("=")
            k, v = k.strip(), v.strip()
            if k == "duration":
                try: dur = float(v)
                except ValueError: pass
            elif k == "r_frame_rate" and "/" in v:
                a, b = v.split("/", 1)
                try:
                    fps = int(round(float(a) / float(b))) if float(b) else None
                except (ValueError, ZeroDivisionError):
                    pass
        return dur, fps
    except Exception:
        return None, None


def _primeira_imagem_por_cap(proj, n_caps):
    """Mapeia capítulo -> caminho da 1ª imagem daquele capítulo (via prefixo C<n>| dos prompts).

    Devolve dict {cap: Path|None}. Usa a NUMERAÇÃO por ordem (img_001 = 1ª linha)."""
    mapa = {c: None for c in range(1, n_caps + 1)}
    if not proj.existe(proj.prompts_imagens):
        return mapa
    linhas = [l for l in proj.prompts_imagens.read_text(encoding="utf-8", errors="replace").splitlines()
              if l.strip()]
    for idx, l in enumerate(linhas, 1):
        m = re.match(r"\s*C(\d+)\s*\|", l)
        if not m:
            continue
        cap = int(m.group(1))
        if cap in mapa and mapa[cap] is None:
            img = proj.images_dir / ("img_%03d.png" % idx)
            mapa[cap] = img if img.exists() else None
    return mapa


def run(proj, log, cancel=None, **_):
    if not proj.existe(proj.roteiro):
        raise ErroPipeline("Falta roteiro.txt para os títulos das capas.")
    titulos = _titulos(proj)
    if not titulos:
        raise ErroPipeline("Nenhum título de capa (rode a Etapa 4 ou confira o roteiro).")

    cfg = _cfg()
    proj.covers_dir.mkdir(parents=True, exist_ok=True)

    # Vídeo de referência do canal (capa_ref): replica DURAÇÃO + FPS a cada capa.
    canal = _canal(proj)
    ref = ""
    try:
        modelos = json.loads((materiais_canal(canal) / "modelos.json").read_text(encoding="utf-8"))
        ref = (modelos.get("capa_ref") or "").strip()
    except (OSError, ValueError):
        pass
    if ref and os.path.isfile(ref):
        dur, fps = _ref_duracao_fps(ref)
        if dur and dur > 0.5:
            cfg["duration_s"] = round(dur, 2)
        if fps and fps > 0:
            cfg["fps"] = fps
        log("  referência de troca de capítulo: %s → replicando formato (%.1fs, %dfps)."
            % (os.path.basename(ref), cfg["duration_s"], cfg["fps"]))
    elif ref:
        log("  ⚠ capa_ref aponta p/ arquivo inexistente (%s) — usando specs padrão." % ref)

    imgs_cap = _primeira_imagem_por_cap(proj, len(titulos))

    # Duração da capa = tempo da fala "Chapter N — Título" (Etapa 3) + respiro, entre um piso
    # e um teto (título curto não fica instantâneo; título longo não estica demais). Sem a
    # narração do título (desligada / TTS falhou), a capa mantém a duração da referência.
    # PISO = 5s (2026-07-10, pedido da editora: 'intro de capítulo 5s na tela'). Títulos curtos
    # ("Chapter 5. Betrayal.") narram em ~2s e a capa sumia antes de dar pra ler — agora a intro
    # fica pelo menos 5s na tela (o respiro de silêncio segura o card). Título longo cresce até o
    # teto sem cortar a voz. Vale P1 e P2 (esta etapa é a mesma). Env ROTEIRO_COVER_DUR_MIN.
    narrar = _cover_narrar_on()
    durs = _titulo_durs(proj) if narrar else {}
    respiro = _envf("ROTEIRO_COVER_RESPIRO_S", 0.5)
    dmin = _envf("ROTEIRO_COVER_DUR_MIN", 5.0)
    dmax = _envf("ROTEIRO_COVER_DUR_MAX", 8.0)

    log("▶ Etapa 6 — capas de capítulo (%d, %dx%d, %s, zoom %.2f, fonte %s)..."
        % (len(titulos), cfg["width"], cfg["height"],
           ("sincronizadas à narração do título" if durs else "%.1fs" % cfg["duration_s"]),
           cfg["zoom"],
           os.path.basename(cfg["font_path"]) if cfg["font_path"] else "The Seasons (embutida)"))

    gerados = 0
    for i, titulo in enumerate(titulos, 1):
        if cancel is not None and cancel.is_set():
            raise ErroPipeline("Cancelado pelo usuário.")
        out = proj.covers_dir / ("capa_%02d.mp4" % i)
        if out.exists() and out.stat().st_size > 0:
            log("    capa_%02d.mp4 já existe — pulada." % i)
            continue
        bg = str(imgs_cap.get(i)) if imgs_cap.get(i) else None
        texto = _titulo_capa(i, titulo)
        cfg_i = cfg
        sync = ""
        if durs.get(i):
            dur_capa = max(dmin, min(dmax, durs[i] + respiro))
            cfg_i = dict(cfg, duration_s=round(dur_capa, 2))
            sync = ", %.1fs sync fala" % dur_capa
        covers.generate_cover_video(texto, cfg_i, out, bg_image=bg)
        origem = ("img do cap" if bg else "cor sólida")
        log("    ✓ capa_%02d.mp4 — \"%s\" (fundo: %s%s)." % (i, texto[:48], origem, sync))
        gerados += 1

    n = len(list(proj.covers_dir.glob("capa_*.mp4")))
    log("    ✓ %d capa(s) em covers/ (%d nova(s))." % (n, gerados))


# --- teste standalone: py -3 stages/s6_capas.py <slug> --------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    slug = sys.argv[1] if len(sys.argv) > 1 else "82-faxineira-bilionario"
    run(projeto_por_slug(slug), print, None)
