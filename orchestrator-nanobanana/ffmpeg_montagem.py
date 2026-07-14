# -*- coding: utf-8 -*-
"""ffmpeg_montagem.py — monta o vídeo-base (Ken Burns + fade + áudio) via FFmpeg.

Lê o mapping.json (gerado por build-mapping.py) e produz out/base.mp4 SEM passar por
navegador: o FFmpeg desenha o Ken Burns (zoom in/out + pan determinístico) com `zoompan`,
aplica fade-através-do-preto em cada take, concatena os takes na ordem da narração e faz
o mux com narration.mp3. É a parte "braçal" da Etapa 8 — muito mais rápida que renderizar
cada frame no Chromium do Remotion.

Fidelidade de timing: cada take ocupa exatamente `durationInFrames` do mapping (back-to-back,
somando o total), igual ao que o Remotion fazia via Sequence. Por isso o áudio continua
sincronizado com a SRT.

O Remotion entra DEPOIS, e só quando há overlays (legendas/títulos), compondo sobre este
base.mp4 (ver s8_montagem.py / Overlay.tsx). Sem overlays, base.mp4 já é o vídeo final.

Uso:
    py -3 ffmpeg_montagem.py "<pasta_do_projeto>" [--out <arquivo.mp4>] [--no-audio]
"""

import os
import re
import sys
import time
import json
import shlex
import shutil
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import achar_ffmpeg, ErroPipeline, SUBPROCESS_FLAGS  # noqa: E402

FADE = 12        # frames de fade-in/out por take (espelha o FADE do KenBurnsImage.tsx)
UPSCALE = 4      # fator de upscale antes do zoompan — define a AMPLITUDE da tremedeira (anti-judder).
                 # O zoompan arredonda a posição do pan/zoom p/ pixel inteiro do quadro INTERNO; o
                 # "pulo" residual em pixels de SAÍDA = 1/upscale (4x => ~0,25px; 3x => ~0,33px;
                 # 2x => ~0,5px). A AMPLITUDE do tremor depende SÓ do upscale (o fps muda só a cadência).
                 # CONTEXTO (2026-06-19): a usuária voltou p/ 30 fps (o 60 fps deixou o MP4 >2 GB, pesado
                 # demais) e pediu p/ subir o upscale e NÃO ter a tremedeira que aparecia a 30 fps. A 3x
                 # ela ainda via tremor; subimos p/ **4x** (~0,25px, o menor pulo prático) + tmix 3 (ver
                 # _motionblur) p/ fundir o resto. BENCHMARK (RTX 2060): 4x=44s/take, 3x≈25s, 2x=14s — o
                 # 4x é o quadro 8K (zoompan single-thread), o ponto + lento; aceitável na GPU p/ matar o
                 # tremor. Em CPU o _upscale() CAPA em 2x (4x seria lentíssimo sem GPU). Baixe via
                 # LONGFORM_FFMPEG_UPSCALE (=3/2 + rápido, pulo maior; =1 velocidade máxima).
def _envf(nome, default):
    """Lê um float de env var com fallback seguro (usado p/ calibrar movimento/legenda)."""
    try:
        return float(os.environ.get(nome, default))
    except (TypeError, ValueError):
        return float(default)


# Amplitude do movimento. O ciclo de Ken Burns roda uma vez POR SUB-TAKE — o build-mapping.py
# fatia cada imagem em sub-takes de ~LONGFORM_TAKE_SEC s (default 30), então a amplitude abaixo
# é percorrida nesses ~30 s (não mais espalhada pelos ~3,7 min da imagem inteira, onde sumia).
# Defaults MAIS AGRESSIVOS (zoom 1.0->1.32, pan 0.92 da margem): a usuária pediu para SENTIR mais
# a presença da animação nos takes que ficam no vídeo, então a câmera anda visivelmente mais —
# o passo maior por frame é compensado pelo fps 60 (default em build-mapping.py), que mantém o
# movimento liso (sem o "tremor"). Preserva o ease-in-out. Calibrável por env
# (LONGFORM_KENBURNS_ZOOM/_PAN; ritmo por LONGFORM_TAKE_SEC).
ZOOM_AMP = _envf("LONGFORM_KENBURNS_ZOOM", 0.32)  # 1.0 -> 1.0+amp
PAN_FRAC = _envf("LONGFORM_KENBURNS_PAN", 0.92)   # fração da margem usada no pan (fica dentro da imagem)
# Qualidade/velocidade do encode CPU (libx264). CRF = qualidade constante (18 ≈ visualmente
# transparente, melhor que bitrate fixo p/ Ken Burns sobre imagem estática). PRESET = "fast":
# em pan/zoom lento o conteúdo é "fácil" de comprimir, então o ganho de qualidade de presets
# mais lentos (medium/slow) é marginal e não paga o tempo extra — "fast" é o ponto ótimo
# velocidade×qualidade. (Só vale p/ a CPU; máquinas com GPU usam nvenc/qsv/amf, ver abaixo.)
CRF = 18
PRESET = "fast"

# ── Encoder de vídeo (autodetecção de GPU, com fallback p/ CPU) ───────────────
# Esta esteira vai pra OUTRAS MÁQUINAS, que podem ter GPU NVIDIA, AMD, Intel ou nenhuma.
# Por isso o default é "auto": no início, faz um PROBE real (encode de 0.1s) de cada
# encoder de hardware na ordem [NVIDIA, Intel QSV, AMD AMF]; o primeiro que ENCODAR de
# verdade vence; se nenhum funcionar, cai pra CPU (libx264) sozinho — sem ninguém precisar
# configurar nada. Dá pra forçar por env LONGFORM_FFMPEG_ENCODER = auto|nvenc|qsv|amf|cpu.
# Qualidade: NVENC/QSV/AMF usam ~CQ/QP equivalente ao CRF do x264 (menor = melhor).
NVENC_CQ = int(os.environ.get("LONGFORM_FFMPEG_NVENC_CQ", "19"))
NVENC_PRESET = os.environ.get("LONGFORM_FFMPEG_NVENC_PRESET", "p4")  # p1(rápido)..p7(lento/melhor).
# p4 (era p5): o conteúdo do Ken Burns é gradiente lento sobre imagem estática — MUITO fácil de
# comprimir — então o ganho de qualidade de presets mais lentos é imperceptível e não paga o tempo.

# Args de qualidade de cada encoder (codec H.264). Todos saem yuv420p p/ compatibilidade.
_ENCODERS = {
    "nvenc": ["-c:v", "h264_nvenc", "-preset", NVENC_PRESET, "-rc", "vbr",
              "-cq", str(NVENC_CQ), "-b:v", "0", "-pix_fmt", "yuv420p"],
    "qsv":   ["-c:v", "h264_qsv", "-global_quality", str(NVENC_CQ + 2), "-pix_fmt", "yuv420p"],
    "amf":   ["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", str(NVENC_CQ + 1),
              "-qp_p", str(NVENC_CQ + 1), "-quality", "balanced", "-pix_fmt", "yuv420p"],
    "cpu":   ["-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF), "-pix_fmt", "yuv420p"],
}
# Resolvido UMA vez por execução (o probe é caro p/ repetir a cada take).
_ENCODER_RESOLVIDO = None


def _upscale(fps=None):
    """Fator de upscale do zoompan. `LONGFORM_FFMPEG_UPSCALE` (override do usuário) vence sempre.

    O upscale define a GRADE em que o zoompan arredonda o pan/zoom p/ pixel inteiro: 1 px da
    grade upscalada = 1/up px de saída. A AMPLITUDE do "pulo" da tremedeira = 1/upscale px de saída
    — depende SÓ do upscale, NÃO do fps (o fps muda só a cadência). Default 4x (ver UPSCALE): ~0,25 px
    de pulo, o menor prático, p/ matar a tremedeira a 30 fps. `fps` é aceito só por compatibilidade.
    EXCEÇÃO CPU: sem GPU o 4x (quadro 8K, zoompan single-thread) seria lentíssimo, então CAPA em 2x —
    o override LONGFORM_FFMPEG_UPSCALE vence até isso. Setar a env força (=3/2 + rápido/pulo maior)."""
    env = os.environ.get("LONGFORM_FFMPEG_UPSCALE")
    if env is not None:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    if _encoder() == "cpu":
        return min(UPSCALE, 2)
    return UPSCALE


def _probe_encoder(nome):
    """Testa se um encoder ENCODA de fato (0.1s 720p -> null). True/False.

    Estar listado em `-encoders` não garante que o device existe/funciona (ex.: nvenc
    listado num PC sem GPU NVIDIA). Só um encode real confirma."""
    try:
        ffmpeg = achar_ffmpeg()
    except Exception:
        return False
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=0.1",
           *_ENCODERS[nome], "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           **SUBPROCESS_FLAGS)
        return r.returncode == 0
    except Exception:
        return False


def _encoder():
    """Resolve o encoder a usar. Env LONGFORM_FFMPEG_ENCODER força (auto|nvenc|qsv|amf|cpu).

    'auto' (default) faz probe real na ordem GPU-primeiro e cai pra CPU. Cacheado por run."""
    global _ENCODER_RESOLVIDO
    if _ENCODER_RESOLVIDO:
        return _ENCODER_RESOLVIDO

    escolha = os.environ.get("LONGFORM_FFMPEG_ENCODER", "auto").strip().lower()
    aliases = {"x264": "cpu", "libx264": "cpu", "nvidia": "nvenc",
               "intel": "qsv", "amd": "amf"}
    escolha = aliases.get(escolha, escolha)

    if escolha in ("nvenc", "qsv", "amf", "cpu"):
        # Forçado: respeita, mas se o HW não encodar, protege caindo pra CPU.
        if escolha != "cpu" and not _probe_encoder(escolha):
            print("AVISO: encoder '%s' forçado não encodou aqui — usando CPU (libx264)." % escolha,
                  flush=True)
            escolha = "cpu"
        _ENCODER_RESOLVIDO = escolha
    else:
        # auto: primeiro encoder de GPU que funcionar; senão CPU.
        for cand in ("nvenc", "qsv", "amf"):
            if _probe_encoder(cand):
                _ENCODER_RESOLVIDO = cand
                break
        else:
            _ENCODER_RESOLVIDO = "cpu"

    rotulo = {"nvenc": "GPU NVIDIA (h264_nvenc)", "qsv": "GPU Intel (h264_qsv)",
              "amf": "GPU AMD (h264_amf)", "cpu": "CPU (libx264)"}[_ENCODER_RESOLVIDO]
    print(">> Encoder de vídeo: %s" % rotulo, flush=True)
    return _ENCODER_RESOLVIDO


def _args_video():
    """Args de codec de vídeo (lista) do encoder resolvido. yuv420p em todos."""
    return list(_ENCODERS[_encoder()])


# ── Limpeza + volume do áudio (tudo via FFmpeg, NÃO-destrutivo) ──
# A narração de TTS sai com ruído de banda larga (hiss) + artefatos de MP3 de bitrate baixo.
# No mux montamos uma cadeia FFmpeg em 2 BLOCOS, nesta ordem:
#
#   1) LIMPEZA   (LONGFORM_AUDIO_NIVEL = media|leve|forte|off): corta sub-graves (highpass),
#      remove o hiss (afftdn — FFT denoise) e suaviza sibilância (deesser).
#   2) VOLUME    (loudnorm, alvo LONGFORM_AUDIO_LUFS, default -14 LUFS): normaliza pro padrão
#      do YouTube. -14 é ~2 dB MAIS ALTO que o antigo -16 (o "pouquinho a mais" de volume),
#      e segue limitado a -1.5 dBTP (LONGFORM_AUDIO_TP) — mais alto SEM clipar. É o ÚLTIMO
#      bloco: é ele quem define o volume final.
#
# É NÃO-destrutivo: narration.mp3 fica intacto — só o áudio embutido no MP4 sai tratado.
# Os níveis de limpeza espelham os trechos comparados em _comparacao_audio/ (MEDIA aprovado).
AUDIO_NIVEIS = {
    "leve":  "highpass=f=80,afftdn=nf=-25:nr=10",
    "media": "highpass=f=85,afftdn=nf=-30:nr=18:tn=1,deesser=i=0.4",
    "forte": "highpass=f=85,anlmdn=s=0.0005,afftdn=nf=-35:nr=24:tn=1,deesser=i=0.5,"
             "lowpass=f=14000",
}
AUDIO_LUFS = os.environ.get("LONGFORM_AUDIO_LUFS", "-14")  # alvo de loudness (mais alto que -16)
AUDIO_TP = os.environ.get("LONGFORM_AUDIO_TP", "-1.5")     # teto de true-peak (anti-clip)
AUDIO_BITRATE = os.environ.get("LONGFORM_AUDIO_BITRATE", "256k")  # AAC de saída (mais folgado que 192k)
AUDIO_SR = os.environ.get("LONGFORM_AUDIO_SR", "48000")           # 48kHz = padrão do YouTube


def _audio_nivel():
    return os.environ.get("LONGFORM_AUDIO_NIVEL", "media").strip().lower()


def _audio_filtro():
    """Cadeia `-af` completa: limpeza + loudnorm. 'off'/'none'/'0' desliga tudo.

    Monta os blocos na ordem certa (loudnorm SEMPRE por último, p/ definir o volume final).
    LONGFORM_AUDIO_NIVEL=off devolve "" (áudio 100% cru, sem nenhum tratamento).
    """
    nivel = _audio_nivel()
    if nivel in ("off", "none", "0", "raw", "nenhum"):
        return ""
    blocos = [AUDIO_NIVEIS.get(nivel, AUDIO_NIVEIS["media"])]
    blocos.append("loudnorm=I=%s:TP=%s:LRA=11" % (AUDIO_LUFS, AUDIO_TP))
    return ",".join(blocos)


def _args_audio_saida():
    """Args de codec do áudio de saída (AAC, bitrate + sample-rate configuráveis)."""
    return ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", AUDIO_SR]


# Estilo da legenda queimada (libass force_style). Elegante e legível p/ long-form 16:9:
# branco, contorno preto + sombra (sem caixa), centralizado na faixa de baixo.
# A legenda fica em UMA LINHA SÓ, no máx ~42-48 caracteres (ver _dividir_legenda): cues longos
# são FATIADOS em várias legendas curtas sequenciais (nunca 2 linhas). O .ass tem PlayRes fixado
# em 1920x1080 (_forcar_playres) — então o Fontsize abaixo vale ~PIXELS REAIS do vídeo (52px ≈
# 4,8% da altura), previsível e sem estourar a largura. Calibrável por env
# (LONGFORM_CAPTION_FONTSIZE / _MARGINV / LONGFORM_CAPTION_MAX_CHARS, default 48).
CAPTION_FONTSIZE = os.environ.get("LONGFORM_CAPTION_FONTSIZE", "52")
CAPTION_MARGINV = os.environ.get("LONGFORM_CAPTION_MARGINV", "90")
try:
    CAPTION_MAX_CHARS = max(20, int(os.environ.get("LONGFORM_CAPTION_MAX_CHARS", "48")))
except ValueError:
    CAPTION_MAX_CHARS = 48
LEGENDA_STYLE = (
    "FontName=Arial,Fontsize=%s,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H64000000,"
    "BorderStyle=1,Outline=3,Shadow=1.5,Alignment=2,MarginV=%s"
) % (CAPTION_FONTSIZE, CAPTION_MARGINV)


def _run(cmd, descricao, cwd=None):
    """Roda um comando FFmpeg, transmitindo stderr; levanta em falha.

    `cwd` opcional — usado na queima de legenda p/ referenciar o .srt por nome relativo
    (evita o inferno de escaping de path do filtro `subtitles` no Windows).
    """
    print(">> %s" % descricao, flush=True)
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True, encoding="utf-8", errors="replace",
                            **SUBPROCESS_FLAGS)
    for linha in proc.stdout:
        linha = linha.rstrip("\n")
        if linha.strip():
            print("   " + linha[:200], flush=True)
    proc.wait()
    if proc.returncode != 0:
        raise SystemExit("ERRO: ffmpeg falhou (código %d) em: %s" % (proc.returncode, descricao))


def _run_silencioso(cmd, cwd=None):
    """Roda ffmpeg SEM transmitir (p/ o modo paralelo, onde os logs de N takes se
    embaralhariam). Retorna (returncode, saída_completa) p/ quem chamou logar só o fim.

    `cwd` opcional — usado quando o take queima legenda in-line (o filtro `subtitles` referencia
    o .ass por nome relativo, evitando o inferno de escaping de path absoluto no Windows)."""
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, encoding="utf-8", errors="replace",
                       **SUBPROCESS_FLAGS)
    return r.returncode, (r.stdout or "")


def _paralelo(total):
    """Quantos takes renderizar EM PARALELO. O `zoompan` é single-thread: a esteira rodava
    um take por vez e deixava ~N-1 núcleos (e a GPU) ociosos — por isso o NVENC ficava a
    ~1,4x. Renderizar vários takes ao mesmo tempo usa a CPU ociosa SEM tocar no filtergraph
    (a saída é byte-equivalente, só muda o agendamento → ZERO perda de qualidade).

    Default na GPU: ~metade dos núcleos lógicos (cap 5). O gargalo real é o zoompan na CPU
    (single-thread), então com o upscale 2x atual rodar mais takes em paralelo escala bem —
    BENCHMARK (RTX 2060, upscale 2x): 3 paralelos=133 f/s agregado, 5=160, 6=167 (+25%). O
    driver atual aceita >3 sessões NVENC (o limite de 3 era de drivers antigos). Calibrável por
    LONGFORM_FFMPEG_PARALELO; clamp em [1, nº de CPUs, nº de takes]. Se a NVENC recusar sessões
    numa placa/driver antigo, force LONGFORM_FFMPEG_PARALELO=3.

    AUTO-AJUSTE p/ máquinas SEM GPU: em CPU não há limite de sessão NVENC e cada take roda
    SINGLE-THREAD (`-threads 1`, ver _montar_take), então usar ~todos os núcleos (deixando 1
    livre p/ o SO) escala quase linear SEM oversubscrição. A env força e desliga o auto-ajuste."""
    env = os.environ.get("LONGFORM_FFMPEG_PARALELO")
    if env is not None:
        try:
            n = int(env)
        except (TypeError, ValueError):
            n = 3
    elif _encoder() == "cpu":
        n = max(3, (os.cpu_count() or 4) - 1)
    else:
        n = min(5, max(3, (os.cpu_count() or 4) // 2))
    return max(1, min(n, os.cpu_count() or 1, max(1, total)))


def _motionblur(fps):
    """Frames de motion-blur temporal (tmix) p/ matar a "tremida" (judder) do zoompan em fps baixo.

    O zoompan arredonda a posição do pan p/ PIXEL INTEIRO do quadro interno; a fps baixa o passo
    por frame fica menor que essa grade e o movimento "segura e pula" (judder visível no pan/zoom
    lento). Um tmix curto (média deslizante de N frames de SAÍDA) injeta um motion-blur sutil que
    funde esses degraus — é o blur que um vídeo a 24/30fps tem naturalmente. MEDIDO (modelo do
    arredondamento): a 30fps/upscale 2x, tmix=2 deixa o movimento TÃO LISO quanto upscale 4x
    (desvio-passo 0,13 vs 0,30 sem blur), a custo ~zero (a média é no quadro 1080p de saída, não
    no quadro upscalado). Default: 3 em fps<=30 (passo por frame maior → precisa de mais blur p/
    fundir o pulo); 2 em fps>30 (inclusive 60fps) — um blur leve de SEGURANÇA contra micro-judder
    residual, custo ~zero (a média é no quadro 1080p de saída) e imperceptível (no 60fps 2 frames
    cobrem só ~0,18px de movimento). A usuária é sensível à tremedeira, então mantemos o tmix ligado
    mesmo a 60fps. Override por LONGFORM_MOTIONBLUR (0/1 desliga; 2/3 = intensidade)."""
    env = os.environ.get("LONGFORM_MOTIONBLUR")
    if env is not None:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return 3 if fps <= 30 else 2


def _ease(n):
    """Progresso 0->1 do take com ease-in-out (smoothstep: p²(3-2p)).

    Substitui a rampa LINEAR (`on/(n-1)`) por uma curva que acelera e desacelera de
    forma suave nas pontas — é o que dá a sensação cinematográfica/elegante no movimento
    (a câmera "respira" em vez de deslizar em velocidade constante).
    """
    p = "(on/%d)" % (n - 1)
    return "(%s*%s*(3-2*%s))" % (p, p, p)


def _expr_zoom(effect, n):
    """Expressão de zoom para o zoompan. `on` é o índice (0-based) do frame de saída."""
    if n <= 1:
        return "1.0"
    e = _ease(n)
    if effect == "zoomOut":
        return "1.0+%(amp)s-%(amp)s*%(e)s" % {"amp": ZOOM_AMP, "e": e}
    return "1.0+%(amp)s*%(e)s" % {"amp": ZOOM_AMP, "e": e}


def _expr_pan(px, n, dim_iw, half_margin_expr):
    """x ou y do recorte: centro + deriva proporcional à margem disponível (fica sempre dentro).

    `half_margin_expr` é a meia-margem em função do zoom: (iw - iw/zoom)/2 (idem para ih).
    Como a margem é 0 quando zoom=1, o pan some nas pontas sem zoom — nunca estoura a borda.
    A deriva também usa o ease-in-out (mesma curva do zoom), p/ um movimento coeso.
    """
    centro = "%s/2-(%s/zoom/2)" % (dim_iw, dim_iw)
    if px == 0 or n <= 1:
        return centro
    sinal = "+" if px > 0 else "-"
    return "%s%s%s*%s*%s" % (centro, sinal, half_margin_expr, PAN_FRAC, _ease(n))


def _filtro_segmento(seg, fps, w, h, ass_name=None):
    """Filtergraph de um take: upscale -> zoompan (Ken Burns) -> fade in/out [-> legenda] -> yuv420p.

    `ass_name` (opcional): nome RELATIVO de um .ass já recortado/deslocado para este take — quando
    presente, a legenda é queimada AQUI mesmo (sobre o frame de saída 1920x1080, depois do zoompan),
    eliminando o 2º encode do vídeo inteiro. O comando precisa rodar com cwd na pasta do .ass.
    """
    n = max(1, int(seg["durationInFrames"]))
    effect = seg.get("effect", "zoomIn")
    px, py = (seg.get("pan") or [0, 0])[:2]

    up = _upscale(fps)
    up_w, up_h = w * up, h * up
    z = _expr_zoom(effect, n)
    x = _expr_pan(px, n, "iw", "(iw-iw/zoom)/2")
    y = _expr_pan(py, n, "ih", "(ih-ih/zoom)/2")

    partes = [
        "scale=%d:%d:force_original_aspect_ratio=increase:flags=lanczos" % (up_w, up_h),
        "crop=%d:%d" % (up_w, up_h),
        "zoompan=z='%s':x='%s':y='%s':d=%d:s=%dx%d:fps=%d" % (z, x, y, n, w, h, fps),
    ]
    if n > FADE:
        st_out = (n - FADE) / float(fps)
        partes.append("fade=t=in:st=0:d=%.4f:color=black" % (FADE / float(fps)))
        partes.append("fade=t=out:st=%.4f:d=%.4f:color=black" % (st_out, FADE / float(fps)))
    # Motion-blur temporal ANTES da legenda (a legenda é estática → fica nítida; só o pan/zoom
    # ganha o blur que funde a tremida do arredondamento do zoompan a fps baixo).
    mb = _motionblur(fps)
    if mb > 1:
        partes.append("tmix=frames=%d" % mb)
    if ass_name:
        partes.append("subtitles=%s:force_style='%s'" % (ass_name, LEGENDA_STYLE))
    partes.append("setsar=1")
    partes.append("format=yuv420p")
    return ",".join(partes)


def _t2s(t):
    """'H:MM:SS.cc' (tempo do .ass) -> segundos (float)."""
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _s2t(x):
    """segundos -> 'H:MM:SS.cc' (formato de tempo do .ass, centésimos)."""
    if x < 0:
        x = 0.0
    h = int(x // 3600); x -= h * 3600
    m = int(x // 60); x -= m * 60
    return "%d:%02d:%05.2f" % (h, m, x)


def _ass_take_text(full_text, start, dur):
    """Recorta o .ass GLOBAL para a janela [start, start+dur] de um take e desloca os tempos
    para a base 0 do clipe. Mantém todo o cabeçalho ([Script Info]/[V4+ Styles]/Format) e só
    filtra/desloca as linhas `Dialogue:`. Cues fora da janela são descartadas; cues que cruzam
    a borda são aparadas (clamp em [0, dur]). É o que permite queimar a legenda por take sem o
    2º encode — cada clipe já sai legendado e no tempo certo do vídeo concatenado.
    """
    out = []
    for ln in full_text.splitlines():
        if ln.startswith("Dialogue:"):
            campos = ln.split(",", 9)
            if len(campos) == 10:
                try:
                    s = _t2s(campos[1]) - start
                    e = _t2s(campos[2]) - start
                except (ValueError, IndexError):
                    out.append(ln); continue
                if e <= 0 or s >= dur:   # totalmente fora deste take
                    continue
                campos[1] = _s2t(max(0.0, s))
                campos[2] = _s2t(min(dur, e))
                out.append(",".join(campos))
            else:
                out.append(ln)
        else:
            out.append(ln)
    return "\n".join(out)


def construir(pasta, out=None, com_audio=True, srt=None):
    """Monta o vídeo. Se `srt` for dado, a legenda é QUEIMADA POR TAKE (dentro do mesmo encode
    do Ken Burns), e `out` já sai legendado — sem o 2º encode do vídeo inteiro. Sem `srt`,
    produz o base.mp4 cru (legenda fica para uma passada separada / `--burn-subs`)."""
    pasta = Path(pasta).resolve()
    mp = pasta / "mapping.json"
    if not mp.is_file():
        raise SystemExit("ERRO: mapping.json não encontrado em %s (rode build-mapping.py)." % pasta)
    m = json.loads(mp.read_text(encoding="utf-8"))
    fps, w, h = int(m["fps"]), int(m["width"]), int(m["height"])
    segs = m.get("segments") or []
    if not segs:
        raise SystemExit("ERRO: mapping.json sem segments.")

    ffmpeg = achar_ffmpeg()
    out = Path(out) if out else (pasta / "out" / "base.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = pasta / "out" / "_ffmpeg"
    tmp.mkdir(parents=True, exist_ok=True)

    base_cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-stats"]

    # Legenda in-line: prepara o .ass GLOBAL uma vez (mesmo tratamento da queima clássica —
    # quebra de linhas + PlayRes), guarda o TEXTO; cada take recebe um recorte deslocado dele.
    cap_txt = None
    if srt is not None:
        srt = Path(srt)
        if not srt.is_file():
            raise SystemExit("ERRO: legenda (.srt) não existe: %s" % srt)
        ass_glob = _preparar_ass(ffmpeg, srt, CAPTION_MAX_CHARS)
        cap_txt = ass_glob.read_text(encoding="utf-8", errors="replace")
        try:
            ass_glob.unlink()
        except OSError:
            pass
        print(">> Legenda embutida no Ken Burns (sem 2º encode): %s" % srt.name, flush=True)

    # 1) Um clipe Ken Burns por take. O `zoompan` é single-thread; renderizar vários takes
    #    em paralelo aproveita os núcleos (e a GPU) ociosos sem alterar o filtergraph — a
    #    saída de cada take é idêntica à do modo sequencial, só muda a ordem de execução.
    def _montar_take(seg):
        """Valida a imagem e monta o comando ffmpeg do take. Roda no thread principal
        (antes de paralelizar) — assim erros de imagem e o probe de encoder não correm risco
        de corrida e levantam de forma limpa. Retorna (clip, cmd, desc, cwd)."""
        i = seg["index"]
        img = pasta / seg["image"]
        if not img.is_file():
            raise ErroPipeline("imagem do segmento %d não existe: %s" % (i, img))
        n = max(1, int(seg["durationInFrames"]))
        clip = tmp / ("seg_%03d.mp4" % i)
        # Legenda do take: recorta o .ass global pela janela do take e grava em tmp/ (nome
        # relativo + cwd=tmp ⇒ o filtro `subtitles` não sofre com escaping de path no Windows).
        ass_name, cwd = None, None
        if cap_txt is not None:
            ass_name = "seg_%03d.ass" % i
            start_sec = seg["fromFrame"] / float(fps)
            (tmp / ass_name).write_text(_ass_take_text(cap_txt, start_sec, n / float(fps)),
                                        encoding="utf-8")
            cwd = str(tmp)
        filtro = _filtro_segmento(seg, fps, w, h, ass_name)
        # Em CPU rodamos MUITOS takes em paralelo (ver _paralelo); pra não oversubscrever,
        # cada take fica preso a 1 thread de filtro+encode → 1 take ≈ 1 núcleo. Em GPU não
        # mexemos (o zoompan é single-thread e o NVENC encoda na placa; deixar livre é melhor).
        cpu = _encoder() == "cpu"
        thr_glob = ["-filter_threads", "1"] if cpu else []
        thr_out = ["-threads", "1"] if cpu else []
        cmd = base_cmd + thr_glob + [
            "-i", str(img),
            "-filter_complex", "[0:v]" + filtro + "[v]",
            "-map", "[v]", "-frames:v", str(n),
            "-r", str(fps), *thr_out, *_args_video(), str(clip),
        ]
        desc = "Ken Burns take %d/%d (%d frames, %s)" % (i + 1, len(segs), n, seg.get("effect"))
        return clip, cmd, desc, cwd

    clips = [None] * len(segs)
    # _montar_take roda aqui (thread principal): resolve o encoder uma vez e valida imagens.
    trabalhos = [(k,) + _montar_take(seg) for k, seg in enumerate(segs)]
    par = _paralelo(len(segs))
    # Quando não há GPU, avisa que o auto-ajuste entrou (upscale menor + mais paralelismo).
    if _encoder() == "cpu":
        print(">> Auto-ajuste CPU (sem GPU detectada): upscale=%dx, %d takes em paralelo, "
              "1 thread/take." % (_upscale(fps), par), flush=True)
    t_takes = time.perf_counter()

    if par <= 1:
        for k, clip, cmd, desc, cwd in trabalhos:
            _run(cmd, desc, cwd=cwd)
            clips[k] = clip
    else:
        print(">> Ken Burns: %d takes, %d em paralelo (zoompan é single-thread)"
              % (len(segs), par), flush=True)
        erros = []

        def _exec(t):
            k, clip, cmd, desc, cwd = t
            rc, saida = _run_silencioso(cmd, cwd=cwd)
            return k, clip, desc, rc, saida

        with ThreadPoolExecutor(max_workers=par) as ex:
            feitos = 0
            for fut in as_completed([ex.submit(_exec, t) for t in trabalhos]):
                k, clip, desc, rc, saida = fut.result()
                feitos += 1
                if rc != 0:
                    erros.append((desc, saida))
                    print("   [FALHOU] %s (codigo %d)" % (desc, rc), flush=True)
                else:
                    clips[k] = clip
                    print("   [ok %d/%d] %s" % (feitos, len(segs), desc), flush=True)
        if erros:
            d, s = erros[0]
            cauda = "\n".join((s or "").splitlines()[-5:])
            raise ErroPipeline("ffmpeg falhou em '%s':\n%s" % (d, cauda))

    dt_takes = time.perf_counter() - t_takes
    print(">> Ken Burns: %d takes em %.0f s (%.1f min, %d em paralelo)"
          % (len(segs), dt_takes, dt_takes / 60.0, par), flush=True)

    # 2) Concatena os takes (mesmos parâmetros -> concat por cópia, rápido).
    lista = tmp / "concat.txt"
    lista.write_text("".join("file '%s'\n" % c.as_posix() for c in clips), encoding="utf-8")
    video_mudo = tmp / "video.mp4"
    _run(base_cmd + ["-f", "concat", "-safe", "0", "-i", str(lista),
                     "-c", "copy", str(video_mudo)],
         "Concatenando %d takes" % len(clips))

    # 3) Mux com a narração — aplicando a limpeza de áudio (afftdn/deesser/loudnorm)
    #    direto aqui (não-destrutivo: narration.mp3 fica intacto). Nível via LONGFORM_AUDIO_NIVEL.
    audio = pasta / m.get("audio", "narration.mp3")
    if com_audio and audio.is_file():
        af = _audio_filtro()
        af_args = ["-af", af] if af else []
        nivel = _audio_nivel()
        rotulo = ("limpeza=%s, vol=%s LUFS" % (nivel, AUDIO_LUFS)) if af else "off (áudio cru)"
        _run(base_cmd + ["-i", str(video_mudo), "-i", str(audio),
                         "-map", "0:v:0", "-map", "1:a:0",
                         "-c:v", "copy", *af_args, *_args_audio_saida(),
                         "-shortest", str(out)],
             "Mux do áudio (%s, %s)" % (audio.name, rotulo))
    else:
        if com_audio:
            print("AVISO: áudio %s não encontrado — gerando base sem trilha." % audio, flush=True)
        shutil.copyfile(video_mudo, out)

    total = m.get("totalSeconds", 0)
    print("base.mp4: %d takes, %.1f s (%.1f min). Salvo em: %s"
          % (len(clips), total, total / 60.0, out), flush=True)


def _dividir_legenda(texto, maximo):
    """Divide `texto` em pedaços de no máx `maximo` caracteres, cada um em UMA LINHA SÓ.

    A usuária quer a legenda em **uma única linha** com **42–48 caracteres no máximo** — então,
    em vez de quebrar a frase em 2 linhas (`\\N`), nós a FATIAMOS em vários pedaços curtos e
    sequenciais (o tempo é repartido entre eles em `_preparar_ass`). Quebra só em espaços (nunca
    corta palavra); palavra solta maior que `maximo` (raríssimo) é cortada no osso p/ não estourar
    a tela. Retorna a lista de pedaços (>= 1), sem `\\N`.
    """
    texto = " ".join(texto.split())  # normaliza espaços
    if not texto:
        return [""]
    if len(texto) <= maximo:
        return [texto]
    palavras = texto.split(" ")
    pedacos, atual = [], ""
    for p in palavras:
        cand = (atual + " " + p) if atual else p
        if atual and len(cand) > maximo:
            pedacos.append(atual)
            atual = p
        else:
            atual = cand
    if atual:
        pedacos.append(atual)
    # palavra única > maximo: corta no osso (evita uma linha estourando a largura da tela)
    saida = []
    for ped in pedacos:
        while len(ped) > maximo:
            saida.append(ped[:maximo])
            ped = ped[maximo:]
        if ped:
            saida.append(ped)
    return saida or [""]


def _forcar_playres(txt, w, h):
    """Fixa PlayResX/PlayResY no [Script Info] do .ass (cria a linha se faltar).

    Com a resolução do script == resolução do vídeo, o Fontsize do force_style passa a valer
    ~pixels reais do vídeo (em vez do ~288 default), tornando o tamanho da legenda previsível.
    """
    for nome, val in (("PlayResX", w), ("PlayResY", h)):
        if re.search(r"(?mi)^%s\s*:" % nome, txt):
            txt = re.sub(r"(?mi)^%s\s*:.*$" % nome, "%s: %d" % (nome, val), txt)
        else:
            txt = re.sub(r"(?mi)^\[Script Info\][ \t]*$",
                         "[Script Info]\n%s: %d" % (nome, val), txt, count=1)
    return txt


def _preparar_ass(ffmpeg, srt, maximo):
    """Converte o .srt -> .ass com legenda de UMA LINHA SÓ, no máx `maximo` (~42-48) caracteres.

    Passos: (1) ffmpeg converte o .srt em .ass; (2) WrapStyle: 2 => libass NÃO quebra automático;
    (3) PlayRes fixado em 1920x1080 p/ o Fontsize valer em pixels reais; (4) cada Dialogue cuja fala
    passa de `maximo` é FATIADO em vários Dialogues curtos e sequenciais (1 linha cada), repartindo
    o tempo do cue proporcional ao tamanho de cada pedaço — assim a tela nunca mostra 2 linhas nem
    uma linha estourando a largura. Retorna o Path do .ass temporário (na pasta do .srt, p/
    referência por nome relativo na queima — evita o inferno de escaping de path no Windows).
    """
    ass = srt.with_name("_legenda_tmp.ass")
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(srt), str(ass)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                   **SUBPROCESS_FLAGS)
    txt = ass.read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?mi)^WrapStyle:", txt):
        txt = re.sub(r"(?mi)^WrapStyle:.*$", "WrapStyle: 2", txt)
    else:
        txt = re.sub(r"(?mi)^\[Script Info\][ \t]*$", "[Script Info]\nWrapStyle: 2", txt, count=1)
    txt = _forcar_playres(txt, 1920, 1080)
    # Cada Dialogue (campo 10, após 9 vírgulas) vira 1+ Dialogues de UMA LINHA <= maximo, repartindo
    # a janela de tempo do cue (campos[1]=ini, campos[2]=fim) proporcional ao tamanho de cada pedaço.
    linhas_out = []
    for linha in txt.splitlines():
        if linha.startswith("Dialogue:"):
            campos = linha.split(",", 9)
            if len(campos) == 10:
                texto = campos[9].replace("\\N", " ").replace("\\n", " ")
                pedacos = _dividir_legenda(texto, maximo)
                if len(pedacos) <= 1:
                    campos[9] = pedacos[0]
                    linhas_out.append(",".join(campos))
                else:
                    try:
                        ini, fim = _t2s(campos[1]), _t2s(campos[2])
                    except (ValueError, IndexError):
                        campos[9] = pedacos[0]  # tempo ilegível: cai pro 1º pedaço, sem quebrar
                        linhas_out.append(",".join(campos))
                        continue
                    total = sum(len(x) for x in pedacos) or 1
                    acc = ini
                    for k, ped in enumerate(pedacos):
                        seg_fim = fim if k == len(pedacos) - 1 else min(fim, acc + (fim - ini) * (len(ped) / total))
                        if seg_fim <= acc:
                            seg_fim = min(fim, acc + 0.05)
                        novo = list(campos)
                        novo[1] = _s2t(acc)
                        novo[2] = _s2t(seg_fim)
                        novo[9] = ped
                        linhas_out.append(",".join(novo))
                        acc = seg_fim
                continue
        linhas_out.append(linha)
    ass.write_text("\n".join(linhas_out), encoding="utf-8")
    return ass


def queimar_legendas(entrada, srt, out):
    """Queima um .srt sobre um MP4 já montado -> out, quebrado em linhas de ~42-48 caracteres.

    Re-encoda SÓ o vídeo (encoder resolvido por `_args_video()` — GPU se houver) e COPIA o
    áudio — não mexe no timing, então a narração continua sincronizada. A legenda é quebrada
    em linhas de ~42-48 caracteres via um .ass intermediário (ver `_preparar_ass`), com
    PlayRes 1920x1080 p/ não estourar a largura da tela. Roda com cwd na pasta do .srt para
    referenciar o arquivo por nome relativo (o filtro `subtitles` quebra com `:` e `\\` de
    paths absolutos no Windows). Mantém o caminho 100% FFmpeg.
    """
    entrada, srt, out = Path(entrada).resolve(), Path(srt).resolve(), Path(out).resolve()
    if not entrada.is_file():
        raise SystemExit("ERRO: vídeo de entrada não existe: %s" % entrada)
    if not srt.is_file():
        raise SystemExit("ERRO: legenda (.srt) não existe: %s" % srt)
    ffmpeg = achar_ffmpeg()
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = _preparar_ass(ffmpeg, srt, CAPTION_MAX_CHARS)
    try:
        vf = "subtitles=%s:force_style='%s'" % (ass.name, LEGENDA_STYLE)
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-stats",
               "-i", str(entrada), "-vf", vf,
               *_args_video(), "-c:a", "copy", str(out)]
        _run(cmd, "Queimando legendas (~%d car./linha, %s)" % (CAPTION_MAX_CHARS, srt.name),
             cwd=str(srt.parent))
    finally:
        try:
            ass.unlink()
        except OSError:
            pass
    print("Legendas queimadas em: %s" % out, flush=True)


def finalizar_video(video, audio, out, srt=None):
    """Finaliza o engine 'dynamic' (v2): muxa o VÍDEO MUDO (renderizado pelo Remotion: galeria
    com ordem/movimento/transição aleatórios) com a NARRAÇÃO tratada (denoise/loudnorm, igual ao
    Ken Burns clássico) e, se `srt` for dado, QUEIMA a legenda — TUDO num encode só.

    Com legenda: re-encoda o vídeo (encoder resolvido por `_args_video`, GPU se houver) aplicando
    o filtro `subtitles`/libass (mesmo .ass quebrado/PlayRes do caminho clássico) e regravando o
    áudio tratado. Sem legenda: COPIA o vídeo (-c:v copy) e só insere a faixa de áudio tratada —
    barato. O timing não muda (o vídeo mudo já tem a duração do áudio), então a narração casa.
    """
    video, audio, out = Path(video).resolve(), Path(audio).resolve(), Path(out).resolve()
    if not video.is_file():
        raise SystemExit("ERRO: vídeo mudo não existe: %s" % video)
    if not audio.is_file():
        raise SystemExit("ERRO: narração (.mp3) não existe: %s" % audio)
    ffmpeg = achar_ffmpeg()
    out.parent.mkdir(parents=True, exist_ok=True)
    af = _audio_filtro()
    af_args = ["-af", af] if af else []
    nivel = _audio_nivel()
    rotulo_audio = ("limpeza=%s, vol=%s LUFS" % (nivel, AUDIO_LUFS)) if af else "off (áudio cru)"
    base_cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-stats"]

    if srt is not None:
        srt = Path(srt).resolve()
        if not srt.is_file():
            raise SystemExit("ERRO: legenda (.srt) não existe: %s" % srt)
        ass = _preparar_ass(ffmpeg, srt, CAPTION_MAX_CHARS)
        try:
            vf = "subtitles=%s:force_style='%s'" % (ass.name, LEGENDA_STYLE)
            cmd = base_cmd + ["-i", str(video), "-i", str(audio),
                              "-map", "0:v:0", "-map", "1:a:0",
                              "-vf", vf, *_args_video(), *af_args, *_args_audio_saida(),
                              "-shortest", str(out)]
            _run(cmd, "Finalizar dynamic (mux narração [%s] + legenda %s)" % (rotulo_audio, srt.name),
                 cwd=str(srt.parent))
        finally:
            try:
                ass.unlink()
            except OSError:
                pass
    else:
        cmd = base_cmd + ["-i", str(video), "-i", str(audio),
                          "-map", "0:v:0", "-map", "1:a:0",
                          "-c:v", "copy", *af_args, *_args_audio_saida(),
                          "-shortest", str(out)]
        _run(cmd, "Finalizar dynamic (mux narração [%s], vídeo copiado)" % rotulo_audio)
    print("final.mp4 (dynamic) gravado em: %s" % out, flush=True)


def relimpar_audio(entrada, out=None, audio_src=None):
    """Reaplica a limpeza de áudio num MP4 JÁ renderizado, SEM re-renderizar o vídeo.

    Copia o stream de vídeo (-c:v copy) e regrava só o áudio com a cadeia de limpeza
    (LONGFORM_AUDIO_NIVEL). É o caminho pra "consertar" o chiado de vídeos antigos: troca a
    faixa de áudio em segundos, sem refazer o Ken Burns nem as legendas (já estão no vídeo).

    Fonte do áudio: se `audio_src` (ex.: o narration.mp3 ORIGINAL) existir, usa-o — é melhor
    que reprocessar o AAC já transcodado do MP4. Senão, trata a própria faixa do MP4.
    Timing intacto: o denoise não altera duração, então legendas/narração seguem sincronizadas.
    Se `out` for o mesmo arquivo de `entrada`, grava num temporário e substitui no fim.
    """
    entrada = Path(entrada).resolve()
    out = Path(out).resolve() if out else entrada
    if not entrada.is_file():
        raise SystemExit("ERRO: vídeo de entrada não existe: %s" % entrada)
    af = _audio_filtro()
    if not af:
        raise SystemExit("ERRO: LONGFORM_AUDIO_NIVEL=off — nada a limpar. Use media/leve/forte.")
    ffmpeg = achar_ffmpeg()
    out.parent.mkdir(parents=True, exist_ok=True)
    inplace = (out == entrada)
    alvo = (out.with_suffix(out.suffix + ".tmp.mp4")) if inplace else out

    base_cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-stats"]
    src = Path(audio_src).resolve() if audio_src else None
    if src and src.is_file():
        cmd = base_cmd + ["-i", str(entrada), "-i", str(src),
                          "-map", "0:v:0", "-map", "1:a:0",
                          "-c:v", "copy", "-af", af, *_args_audio_saida(),
                          "-shortest", str(alvo)]
        fonte = src.name
    else:
        cmd = base_cmd + ["-i", str(entrada),
                          "-map", "0:v:0", "-map", "0:a:0?",
                          "-c:v", "copy", "-af", af, *_args_audio_saida(), str(alvo)]
        fonte = "faixa do próprio MP4"
    _run(cmd, "Limpando áudio de %s (nível=%s, fonte=%s)" % (entrada.name, _audio_nivel(), fonte))
    if inplace:
        os.replace(str(alvo), str(out))
    print("Áudio limpo gravado em: %s" % out, flush=True)


def main():
    args = sys.argv[1:]
    if not args:
        print('uso: py -3 ffmpeg_montagem.py "<pasta_do_projeto>" [--out <arquivo.mp4>] [--no-audio]')
        print('  ou: py -3 ffmpeg_montagem.py "<pasta>" --com-legenda [--srt <legenda.srt>] [--out <final.mp4>]  (Ken Burns + legenda num encode só)')
        print('  ou: py -3 ffmpeg_montagem.py "<pasta>" --burn-subs --in <video.mp4> --out <final.mp4> [--srt <legenda.srt>]')
        print('  ou: py -3 ffmpeg_montagem.py "<pasta>" --finalizar --in <video_mudo.mp4> --audio <narration.mp3> [--srt <legenda.srt>] [--out <final.mp4>]  (engine dynamic: mux áudio tratado + legenda)')
        print('  ou: py -3 ffmpeg_montagem.py "<pasta>" --limpar-audio [--in <video.mp4>] [--out <saida.mp4>] [--audio <narration.mp3>]')
        raise SystemExit(1)

    # Modo legenda IN-LINE: monta o Ken Burns JÁ com a legenda queimada por take, num único
    # encode (sem o 2º passe sobre o vídeo inteiro). Saída padrão = out/final.mp4.
    if "--com-legenda" in args:
        args = [a for a in args if a != "--com-legenda"]
        pasta = Path(args[0]).resolve()
        srt = out = None
        for flag in ("--srt", "--out"):
            if flag in args:
                i = args.index(flag); val = args[i + 1]; del args[i:i + 2]
                if flag == "--srt": srt = val
                else: out = val
        srt = Path(srt) if srt else (pasta / "narration.srt")
        out = Path(out) if out else (pasta / "out" / "final.mp4")
        t0 = time.perf_counter()
        construir(pasta, out=out, com_audio=True, srt=srt)
        dt = time.perf_counter() - t0
        print(">> Tempo da render (final.mp4 com legenda): %.0f s (%.1f min)" % (dt, dt / 60.0), flush=True)
        return

    # Modo FINALIZAR (engine dynamic): muxa o vídeo MUDO do Remotion com a narração tratada e,
    # se houver --srt, queima a legenda — tudo num encode só. Saída padrão = out/final.mp4.
    if "--finalizar" in args:
        args = [a for a in args if a != "--finalizar"]
        pasta = Path(args[0]).resolve()
        entrada = audio = srt = out = None
        for flag in ("--in", "--out", "--srt", "--audio"):
            if flag in args:
                i = args.index(flag); val = args[i + 1]; del args[i:i + 2]
                if flag == "--in": entrada = val
                elif flag == "--out": out = val
                elif flag == "--srt": srt = val
                else: audio = val
        entrada = Path(entrada) if entrada else (pasta / "out" / "video_mudo.mp4")
        out = Path(out) if out else (pasta / "out" / "final.mp4")
        audio = Path(audio) if audio else (pasta / "narration.mp3")
        srt = Path(srt) if srt else None
        t0 = time.perf_counter()
        finalizar_video(entrada, audio, out, srt)
        dt = time.perf_counter() - t0
        print(">> Tempo do finalizar (mux + legenda): %.0f s (%.1f min)" % (dt, dt / 60.0), flush=True)
        return

    # Modo limpeza de áudio: regrava só a faixa de áudio (denoise/loudnorm) num MP4 pronto.
    if "--limpar-audio" in args:
        args = [a for a in args if a != "--limpar-audio"]
        pasta = Path(args[0]).resolve()
        entrada = out = audio_src = None
        for flag in ("--in", "--out", "--audio"):
            if flag in args:
                i = args.index(flag); val = args[i + 1]; del args[i:i + 2]
                if flag == "--in": entrada = val
                elif flag == "--out": out = val
                else: audio_src = val
        entrada = Path(entrada) if entrada else (pasta / "out" / "final.mp4")
        # Default = limpar a PRÓPRIA faixa do MP4 (sync garantido com aquele vídeo).
        # narration.mp3 só entra se passado em --audio (pode divergir em renders antigos).
        relimpar_audio(entrada, out, audio_src)
        return

    # Modo legenda: queima um .srt sobre um MP4 pronto (não remonta o Ken Burns).
    if "--burn-subs" in args:
        args = [a for a in args if a != "--burn-subs"]
        pasta = Path(args[0]).resolve()
        entrada = srt = out = None
        for flag in ("--in", "--out", "--srt"):
            if flag in args:
                i = args.index(flag); val = args[i + 1]; del args[i:i + 2]
                if flag == "--in": entrada = val
                elif flag == "--out": out = val
                else: srt = val
        entrada = Path(entrada) if entrada else (pasta / "out" / "base.mp4")
        out = Path(out) if out else (pasta / "out" / "final.mp4")
        srt = Path(srt) if srt else (pasta / "narration.srt")
        queimar_legendas(entrada, srt, out)
        return

    com_audio = "--no-audio" not in args
    args = [a for a in args if a != "--no-audio"]
    out = None
    if "--out" in args:
        i = args.index("--out"); out = args[i + 1]; del args[i:i + 2]
    t0 = time.perf_counter()
    construir(args[0], out=out, com_audio=com_audio)
    dt = time.perf_counter() - t0
    print(">> Tempo da render (base.mp4): %.0f s (%.1f min)" % (dt, dt / 60.0), flush=True)


if __name__ == "__main__":
    main()
