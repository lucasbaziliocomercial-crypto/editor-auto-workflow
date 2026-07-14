# -*- coding: utf-8 -*-
"""build-mapping.py — gera mapping.json (timeline do Remotion) a partir de narration.srt
e das imagens em images/. Adapta a lógica de agrupamento do mapear-srt.py do TINAGO,
mas com saída JSON pronta para o Remotion (16:9).

Uso:
    py -3 build-mapping.py "<pasta_do_projeto>"

Cada IMAGEM cobre um trecho contínuo da narração (cues do SRT agrupados). Como no long-form
cada imagem fica MUITO tempo na tela (~9 imagens p/ ~35 min => ~3,7 min cada), um único ciclo
de Ken Burns espalhado por 3-4 min é IMPERCEPTÍVEL (a câmera anda <0,1% por segundo). Por isso
cada imagem é então FATIADA em SUB-TAKES de ~LONGFORM_TAKE_SEC segundos (default 30): cada
sub-take roda um ciclo COMPLETO de Ken Burns (zoom in/out alternado + pan próprio), com fade
entre eles, então o movimento reinicia e fica visível o vídeo inteiro. Calibrável por env
LONGFORM_TAKE_SEC (segundos por sub-take). A imagem é a mesma dentro dos sub-takes de um take.
"""

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import parse_srt  # noqa: E402

# 30 fps (default): a montagem é renderizada POR FRAME, então o fps multiplica DIRETO o tempo de
# render E o TAMANHO do arquivo. O 60 fps (testado) ficou liso mas deixou o MP4 >2 GB (~2,47 GB no
# vídeo 13) — pesado demais p/ a usuária. Voltamos p/ 30 fps (MP4 ~1,4 GB, ~metade) e atacamos a
# tremedeira pela AMPLITUDE: upscale 4x (~0,25px de pulo) + tmix 3 (ver ffmpeg_montagem.py). Quem
# quiser o 60 fps liso volta pra LONGFORM_FPS=60 (arquivo ~2x). Timing/sync NÃO muda com o fps:
# fromFrame/durationInFrames vêm de segundos × FPS, então a narração segue casada.
def _fps():
    try:
        v = int(os.environ.get("LONGFORM_FPS", "30"))
        return v if v > 0 else 30
    except (TypeError, ValueError):
        return 30


FPS = _fps()
WIDTH = 1920
HEIGHT = 1080
# pan determinístico por índice (sem random — reprodutível)
PANS = [(0.0, 0.0), (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0), (1.0, 1.0), (-1.0, -1.0)]


def _take_sec():
    """Segundos-alvo por sub-take (intervalo entre cada movimento novo de câmera). Env override."""
    try:
        v = float(os.environ.get("LONGFORM_TAKE_SEC", "30"))
        return v if v > 0 else 30.0
    except (TypeError, ValueError):
        return 30.0


def _subdividir(ini, fim, alvo_sec):
    """Divide [ini, fim] em N fatias ~iguais de no máx ~alvo_sec (>= 1 fatia).

    As bordas são contíguas (o fim de uma fatia == início da próxima), então ao converter
    para frames a soma fecha exatamente com o total — o áudio continua sincronizado com a SRT.
    """
    dur = max(0.0, fim - ini)
    n = max(1, int(round(dur / alvo_sec))) if dur > 0 else 1
    # evita fatia muito maior que o alvo quando o arredondamento puxa n pra baixo
    if n >= 1 and dur / n > alvo_sec * 1.5:
        n += 1
    passo = dur / n
    bordas = [ini + passo * k for k in range(n)] + [fim]
    return [(bordas[k], bordas[k + 1]) for k in range(n)]


def construir(pasta):
    pasta = Path(pasta).resolve()
    srt = pasta / "narration.srt"
    if not srt.is_file():
        raise SystemExit("ERRO: narration.srt não encontrado em %s" % pasta)
    cues = parse_srt(srt)
    if not cues:
        raise SystemExit("ERRO: narration.srt sem cues.")

    imagens = sorted((pasta / "images").glob("img_*.png"))
    if not imagens:
        raise SystemExit("ERRO: nenhuma imagem em images/ (rode a Etapa 7).")
    n_img = len(imagens)

    total = cues[-1][2]
    alvo_seg = total / n_img  # duração-alvo por imagem

    # Agrupa cues em n_img segmentos, fechando quando passa do alvo acumulado.
    segmentos = []
    buf, buf_ini, buf_txt = [], cues[0][1], []
    idx_img = 0
    for (_, ini, fim, texto) in cues:
        buf.append((ini, fim))
        buf_txt.append(texto)
        dur_acc = fim - buf_ini
        faltam_img = n_img - len(segmentos)
        # fecha se atingiu o alvo E ainda há imagens de sobra para os cues restantes
        if dur_acc >= alvo_seg and faltam_img > 1:
            segmentos.append((buf_ini, fim, " ".join(buf_txt).strip()))
            buf, buf_txt, buf_ini = [], [], fim
    if buf_txt or buf:
        segmentos.append((buf_ini, cues[-1][2], " ".join(buf_txt).strip()))

    # Se geramos menos segmentos que imagens (cues longos), tudo bem: usamos as 1ªs imagens.
    # Se geramos mais (raro), juntamos o excedente no último.
    while len(segmentos) > n_img:
        a = segmentos.pop()
        b = segmentos.pop()
        segmentos.append((b[0], a[1], (b[2] + " " + a[2]).strip()))

    saida = {
        "fps": FPS, "width": WIDTH, "height": HEIGHT,
        "audio": "narration.mp3",
        "durationInFrames": int(round(total * FPS)),
        "totalSeconds": round(total, 3),
        "segments": [],
    }
    # Cada imagem (take) é fatiada em sub-takes de ~take_sec; cada sub-take recebe um ciclo
    # completo de Ken Burns (efeito + pan), variando pelo índice GLOBAL para que movimentos
    # vizinhos sempre difiram. Frames contíguos (fim de uma fatia = fromFrame da próxima) =>
    # a soma fecha com o total e o áudio segue sincronizado.
    take_sec = _take_sec()
    sub = 0
    for img_i, (ini, fim, texto) in enumerate(segmentos):
        img = imagens[img_i].name if img_i < n_img else imagens[-1].name
        fatias = _subdividir(ini, fim, take_sec)
        for k, (s, e) in enumerate(fatias):
            dx, dy = PANS[sub % len(PANS)]
            f_s = int(round(s * FPS))
            f_e = int(round(e * FPS))
            saida["segments"].append({
                "index": sub,
                "image": "images/" + img,
                "start": round(s, 3),
                "end": round(e, 3),
                "fromFrame": f_s,
                "durationInFrames": max(1, f_e - f_s),
                "text": texto if k == 0 else "",  # texto da imagem só na 1ª fatia (evita repetir)
                "effect": "zoomIn" if sub % 2 == 0 else "zoomOut",
                "pan": [dx, dy],
            })
            sub += 1

    out = pasta / "mapping.json"
    out.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    media = (total / sub) if sub else 0.0
    print("mapping.json: %d sub-takes (%d imagens, ~%.0fs/sub-take), %.1f s (%.1f min)."
          % (sub, n_img, media, total, total / 60.0))
    print("Salvo em: %s" % out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('uso: py -3 build-mapping.py "<pasta_do_projeto>"')
        raise SystemExit(1)
    construir(sys.argv[1])
