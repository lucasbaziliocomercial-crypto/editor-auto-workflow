# -*- coding: utf-8 -*-
"""qr_overlay.py — sobreposição do QR Code fixo da ISCA (Parte 1).

Todo vídeo Parte 1 leva um QR Code "na posição certinha" (imagem já enquadrada no frame,
fundo transparente) queimado por cima do vídeo INTEIRO. É um asset FIXO por canal: o editor
larga o arquivo uma vez na pasta do canal e a montagem o aplica em toda P1 daquele canal.

Só entra na ISCA (P1). A extensão (P2) NÃO recebe QR (o chamador só invoca quando not extensao).

Resolução do arquivo (1º que existir, "próprio vence"):
    projects/<slug>/qr/  >  materiais/<canal>/qr/ (+ herdada: Rowan/Kay ← Lena)  >  assets/qr/
Nomes aceitos (qualquer imagem; preferência por esses stems): qr, qr_code, qrcode, qr_p1, link.

Envs:
    ROTEIRO_QR=0            desliga (default ligado).
    ROTEIRO_QR_FIT=1        escala o QR pro frame inteiro (WxH) e sobrepõe em 0:0 — é o caso do
                            PNG full-frame "já na posição certinha" (default). Com FIT=0 o QR
                            entra no tamanho nativo, posicionado por ROTEIRO_QR_POS.
    ROTEIRO_QR_POS=0:0      posição x:y (só quando FIT=0; aceita expressões do overlay, ex.
                            "W-w-40:H-h-40" pro canto inferior direito com margem de 40px).
    ROTEIRO_QR_OPACITY=1.0  opacidade do QR (0..1).

Falha graciosa: sem arquivo de QR -> None (o chamador segue sem QR, com aviso). Sem quebrar a
montagem em nenhum caso.
"""
import os
from pathlib import Path

from common import IMG_EXTS, materiais_dirs, SUBPROCESS_FLAGS
import subprocess


_ASSETS_QR = Path(__file__).resolve().parent.parent / "assets" / "qr"
# stems preferidos (na ordem); qualquer outra imagem na pasta serve como fallback.
_PREFERIDOS = ("qr", "qr_code", "qrcode", "qr_p1", "qr-p1", "link")


def _ligado():
    return os.environ.get("ROTEIRO_QR", "1").strip().lower() not in (
        "0", "off", "none", "nao", "não", "no", "false")


def _fit():
    return os.environ.get("ROTEIRO_QR_FIT", "1").strip().lower() not in (
        "0", "off", "none", "nao", "não", "no", "false")


def _pos():
    v = (os.environ.get("ROTEIRO_QR_POS", "0:0") or "0:0").strip()
    if ":" in v:
        x, y = v.split(":", 1)
        return x.strip() or "0", y.strip() or "0"
    return v or "0", "0"


def _opacidade():
    try:
        return max(0.0, min(1.0, float(os.environ.get("ROTEIRO_QR_OPACITY", "1.0"))))
    except (TypeError, ValueError):
        return 1.0


def _imagem_qr(proj, canal):
    """1º QR encontrado na cadeia projects/<slug>/qr > materiais/<canal>/qr (+herdada) > assets/qr.
    Dentro de cada pasta, prioriza os stems preferidos; senão pega a 1ª imagem em ordem."""
    pastas = [proj.dir / "qr"]
    for d in materiais_dirs(canal):
        pastas.append(d / "qr")
    pastas.append(_ASSETS_QR)
    for pasta in pastas:
        try:
            if not pasta.is_dir():
                continue
            imgs = [p for p in sorted(pasta.iterdir())
                    if p.is_file() and p.suffix.lower() in IMG_EXTS]
        except OSError:
            continue
        if not imgs:
            continue
        for stem in _PREFERIDOS:
            for p in imgs:
                if p.stem.lower() == stem:
                    return p
        return imgs[0]
    return None


def imagem(proj, canal):
    """QR resolvido (Path) ou None — mesma cadeia de _imagem_qr, mas respeitando ROTEIRO_QR.
    Exposto p/ a montagem FUNDIR a sobreposição do QR com a passada de legenda (economiza um
    reencode do vídeo inteiro). None = QR desligado ou nenhum arquivo encontrado."""
    if not _ligado():
        return None
    return _imagem_qr(proj, canal)


def fragmento_filtro(idx, w, h):
    """(prep_str, pos_x, pos_y) p/ compor o QR num filter_complex EXTERNO (ex.: junto da legenda).
    `idx` = índice do input ffmpeg do QR; `prep_str` produz o label [qr]. Espelha a lógica de
    aplicar() (FIT escala pro frame em 0:0; senão tamanho nativo em ROTEIRO_QR_POS)."""
    op = _opacidade()
    if _fit():
        return ("[%d:v]format=rgba,scale=%d:%d,colorchannelmixer=aa=%.3f[qr]" % (idx, w, h, op),
                "0", "0")
    x, y = _pos()
    return ("[%d:v]format=rgba,colorchannelmixer=aa=%.3f[qr]" % (idx, op), x, y)


def aplicar(proj, canal, ff, video_in, saida, w, h, fps, log, venc_args):
    """Sobrepõe o QR fixo sobre `video_in` -> `saida`. Devolve `saida` em sucesso, senão None
    (montagem segue com o vídeo sem QR). `venc_args` vem do engine (encoder/CRF uniformes); o
    áudio é copiado (`-c:a copy`) — só o stream de vídeo é re-encodado p/ queimar o QR."""
    if not _ligado():
        return None
    qr = _imagem_qr(proj, canal)
    if not qr:
        log("    (sem QR — nenhuma imagem em projects/<slug>/qr/, materiais/%s/qr/ nem assets/qr/.)"
            % (canal or "sem-canal"))
        return None

    op = _opacidade()
    # pré-tratamento do overlay: garante alpha; escala pro frame (FIT) ou mantém nativo (POS).
    if _fit():
        prep = ("[1:v]format=rgba,scale=%d:%d,colorchannelmixer=aa=%.3f[qr]" % (w, h, op))
        pos = ("0", "0")
    else:
        prep = ("[1:v]format=rgba,colorchannelmixer=aa=%.3f[qr]" % op)
        pos = _pos()
    # -loop 1 no PNG do QR + shortest=1 no overlay: a imagem estática vira um stream que dura o
    # vídeo INTEIRO (sem -loop ela é 1 frame só e o QR sumia após o teaser). O vídeo (input 0)
    # manda na duração; o QR looped é cortado no fim dele.
    fc = "%s;[0:v][qr]overlay=%s:%s:format=auto:shortest=1[v]" % (prep, pos[0], pos[1])

    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(video_in), "-loop", "1", "-i", str(qr),
           "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
           *venc_args, "-c:a", "copy", str(saida)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **SUBPROCESS_FLAGS)
    if saida.exists() and saida.stat().st_size > 0:
        onde = "frame inteiro" if _fit() else ("%s:%s" % pos)
        log("    ✓ QR sobreposto na isca (%s, %s, opacidade %.2f)." % (qr.name, onde, op))
        return saida
    err = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    log("    ⚠ falha ao sobrepor o QR (%s) — seguindo sem QR. %s"
        % (qr.name, " | ".join(err[-3:])))
    return None
