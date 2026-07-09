# -*- coding: utf-8 -*-
"""montagem_vertical.py — engine de montagem do vídeo de romance (vertical), 100% FFmpeg.

Reproduz a ESTRUTURA que o Romance Maker montava no CapCut, mas RENDERIZA um MP4 headless
(o pipeline exige out/final.mp4 — sem passo manual no CapCut). Timeline:

    TEASER  →  [capa_1] corpo_1  →  [capa_2] corpo_2  →  …  →  RESUMO P2  →  CTA
    └ clipes drop-in mudos, cortados p/ durar o GANCHO falado; narração do gancho por baixo
                    └ corpo = imagens do capítulo (Ken Burns) sincronizadas à narração daquele cap.
                       a capa (Etapa 6) anuncia o capítulo; áudio da capa = silêncio (a "pausa")
    música de fundo (-10 dB) por baixo de tudo.

Como sincroniza: a narração é a espinha. Os limites de cada capítulo na narration.mp3 são
achados casando a `primeira_frase` de cada capítulo (capitulos.json) com as cues da
narration.srt. A partir daí cada segmento recebe a fatia de áudio correspondente; onde entra a
capa (que não tem narração), abre-se um silêncio — então a narração "pausa" na troca, igual RM.

Estratégia de render: cada segmento vira um MP4 uniforme (mesma resolução/fps/formato de áudio),
e todos são concatenados (concat demuxer). Por fim, a música é mixada por cima num único passo
(-c:v copy). É idempotente por segmento (reaproveita out/seg_*.mp4 já prontos).

Robusto a peças faltando: sem teaser → gancho vira Ken Burns da 1ª imagem; sem resumo/CTA →
pulados com aviso; sem música → sem trilha; sem capa de um capítulo → sem cartão naquele cap.
"""

import os
import re
import json
import shutil
import subprocess
from pathlib import Path

from common import (ErroPipeline, achar_ffmpeg, SUBPROCESS_FLAGS, parse_srt,
                    materiais_canal, ler_modelos, IMG_EXTS)

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


# ---------------------------------------------------------------------------
# Parâmetros (env)
# ---------------------------------------------------------------------------

def _dim():
    def _i(k, d):
        try:
            return int(float(os.environ.get(k, d)))
        except ValueError:
            return int(d)
    return _i("ROTEIRO_W", 1080), _i("ROTEIRO_H", 1920)


def _fps():
    try:
        return int(float(os.environ.get("ROTEIRO_COVER_FPS", "30")))
    except ValueError:
        return 30


def _encoder():
    """Encoder de vídeo. Default libx264 (CPU, portável). Troque por ROTEIRO_ENCODER
    (ex.: h264_nvenc) se a máquina tiver a GPU e o driver certos."""
    return os.environ.get("ROTEIRO_ENCODER", "libx264").strip() or "libx264"


def _capa_pos():
    """Onde a capa do capítulo entra: 'antes' (anuncia o capítulo, padrão do Romance Maker)
    ou 'depois' (fecha o capítulo). Sobrescrevível por ROTEIRO_CAPA_POS."""
    return os.environ.get("ROTEIRO_CAPA_POS", "antes").strip().lower()


# ---------------------------------------------------------------------------
# Helpers FFmpeg
# ---------------------------------------------------------------------------

def _run(cmd, log, desc, cwd=None):
    proc = subprocess.run(cmd, cwd=(str(cwd) if cwd else None),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, **SUBPROCESS_FLAGS)
    # No Windows o FFmpeg às vezes devolve returncode != 0 mesmo gerando o arquivo — o
    # chamador confere o arquivo de saída; aqui só logamos o stderr se parecer falha real.
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        log("    [ffmpeg %s rc=%s] %s" % (desc, proc.returncode, " | ".join(err[-4:])))
    return proc


def _dur(ff, arq):
    ffprobe = str(Path(ff).with_name("ffprobe" + Path(ff).suffix))
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", str(arq)],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **SUBPROCESS_FLAGS)
        return float((r.stdout or b"").decode("utf-8", "replace").strip() or 0)
    except Exception:
        return 0.0


def _tem_audio(ff, arq):
    ffprobe = str(Path(ff).with_name("ffprobe" + Path(ff).suffix))
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
                            "stream=index", "-of", "csv=p=0", str(arq)],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **SUBPROCESS_FLAGS)
        return bool((r.stdout or b"").decode("utf-8", "replace").strip())
    except Exception:
        return False


# saída de vídeo/áudio uniforme p/ todo segmento (concat exige formato igual)
def _venc_args(fps):
    enc = _encoder()
    args = ["-c:v", enc, "-pix_fmt", "yuv420p", "-r", str(fps)]
    if enc == "libx264":
        # CRF 18 + preset fast = a spec de qualidade da esteira-modelo (visualmente transparente).
        args += ["-preset", os.environ.get("ROTEIRO_X264_PRESET", "fast"),
                 "-crf", os.environ.get("ROTEIRO_CRF", "18")]
    return args


_AENC = ["-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2"]


# Tratamento de áudio da narração (cleanup + loudnorm), aplicado SÓ no mix final — porta a
# cadeia da esteira-modelo (seção 8.4): limpeza primeiro, loudnorm SEMPRE por último (define o
# volume). Níveis por ROTEIRO_AUDIO_NIVEL (leve|media|forte|off). loudnorm I=-14 (padrão da casa).
_AUDIO_LIMPEZA = {
    "leve":  "highpass=f=80,afftdn=nf=-25:nr=10",
    "media": "highpass=f=85,afftdn=nf=-30:nr=18:tn=1,deesser=i=0.4",
    "forte": "highpass=f=85,anlmdn=s=0.0005,afftdn=nf=-35:nr=24:tn=1,deesser=i=0.5,lowpass=f=14000",
}


def _audio_filtro():
    """Cadeia -af p/ a NARRAÇÃO no mix final (ou '' se desligado). Não inclui a música."""
    nivel = os.environ.get("ROTEIRO_AUDIO_NIVEL", "media").strip().lower()
    if nivel in ("off", "0", "none", "nao", "não"):
        return ""
    limpeza = _AUDIO_LIMPEZA.get(nivel, _AUDIO_LIMPEZA["media"])
    lufs = os.environ.get("ROTEIRO_AUDIO_LUFS", "-14")
    tp = os.environ.get("ROTEIRO_AUDIO_TP", "-1.5")
    return "%s,loudnorm=I=%s:TP=%s:LRA=11" % (limpeza, lufs, tp)


def _legenda_on():
    """Legenda queimada LIGADA por padrão. Desligue com ROTEIRO_LEGENDA=0/off/none/nao."""
    v = os.environ.get("ROTEIRO_LEGENDA", "1").strip().lower()
    return v not in ("0", "off", "none", "nao", "não", "no", "false")


def _legenda_style(w, h):
    """force_style libass adaptado ao VERTICAL. Fontsize/MarginV em px do vídeo (SRT usa a
    resolução do vídeo como PlayRes). MarginV maior = a legenda sobe pro terço inferior,
    fora da área de UI do app. Sobrescrevível por env."""
    fs = os.environ.get("ROTEIRO_CAPTION_FONTSIZE", str(max(40, int(h * 0.033))))  # ~63 em 1920
    mv = os.environ.get("ROTEIRO_CAPTION_MARGINV", str(int(h * 0.16)))             # ~307 em 1920
    return ("FontName=Arial,Fontsize=%s,Bold=1,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,BackColour=&H64000000,BorderStyle=1,Outline=3,"
            "Shadow=1.5,Alignment=2,MarginV=%s" % (fs, mv))


def _caption_max_chars():
    """Máx de caracteres por linha de legenda (UMA linha só). Segue o PADRÃO da long form
    (fatiamento de cues longos), mas com default menor (32) porque o frame vertical tem só
    ~1080px de largura — 48 (o default 16:9) estouraria em 2 linhas. Env: ROTEIRO_CAPTION_MAX_CHARS."""
    try:
        return max(16, int(os.environ.get("ROTEIRO_CAPTION_MAX_CHARS", "32")))
    except ValueError:
        return 32


def _preparar_ass(ff, srt, maximo, w, h):
    """Converte .srt -> .ass com legenda de UMA LINHA SÓ (máx `maximo` chars), IGUAL à long form.

    Reusa o algoritmo de fatiamento da long form (`ffmpeg_montagem._dividir_legenda` etc.) — a ÚNICA
    diferença é o PlayRes, fixado na resolução VERTICAL (w×h) em vez de 1920×1080, p/ o Fontsize do
    force_style valer em pixels reais do vídeo 9:16. Cues > `maximo` viram vários Dialogues curtos
    e sequenciais (nunca 2 linhas), repartindo o tempo do cue proporcional ao tamanho de cada pedaço."""
    from ffmpeg_montagem import _dividir_legenda, _forcar_playres, _t2s, _s2t
    ass = srt.with_name("_legenda_tmp.ass")
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(srt), str(ass)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                   **SUBPROCESS_FLAGS)
    txt = ass.read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?mi)^WrapStyle:", txt):
        txt = re.sub(r"(?mi)^WrapStyle:.*$", "WrapStyle: 2", txt)  # 2 => libass não quebra sozinho
    else:
        txt = re.sub(r"(?mi)^\[Script Info\][ \t]*$", "[Script Info]\nWrapStyle: 2", txt, count=1)
    txt = _forcar_playres(txt, w, h)
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
                        campos[9] = pedacos[0]
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


def _fit(w, h):
    """Filtro de vídeo que enquadra QUALQUER fonte em w×h (cover: preenche e corta)."""
    return ("scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1"
            % (w, h, w, h))


# ---------------------------------------------------------------------------
# Fatias de áudio da narração
# ---------------------------------------------------------------------------

def _norm(s):
    # Troca pontuação por ESPAÇO e colapsa espaços em UM só. A SRT do Whisper vem com espaços
    # DUPLOS ("I  was  left"); sem o collapse, a frase-âncora (single-space) nunca casava e TODA
    # fronteira de capítulo caía no fallback proporcional (hook errado, ~6s em vez dos ~35s reais).
    # Pontuação->espaço (não ""): tolera em-dash e ponto sem espaço; ambos os lados usam o mesmo
    # _norm, então fica consistente (ex.: "cheek—warm" vira "cheek warm" dos dois lados).
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()


def _boundaries(proj, ff, log):
    """Devolve (total, [b1, b2, ...]) — os tempos (s) em que cada CAPÍTULO começa na narração.

    b_c = início do capítulo c na narration.mp3. hook = [0, b1). Acha casando a primeira_frase
    de cada capítulo (capitulos.json) com as cues da narration.srt. Cai em split proporcional
    (por nº de chars da prosa) quando o casamento falha."""
    total = _dur(ff, proj.narration_mp3)
    caps = []
    p = proj.dir / "capitulos.json"
    if proj.existe(p):
        try:
            caps = json.loads(p.read_text(encoding="utf-8")).get("capitulos", [])
        except (OSError, ValueError):
            caps = []
    if not caps:
        raise ErroPipeline("capitulos.json ausente/vazio — rode a Etapa 3 antes da montagem.")

    cues = parse_srt(proj.narration_srt) if proj.existe(proj.narration_srt) else []
    # Casa a primeira_frase de cada capítulo contra a narração p/ achar onde ele COMEÇA — é ali que
    # a capa entra. Concatena as cues normalizadas num "blob" com índice char->tempo e busca a
    # frase-âncora por JANELA DESLIZANTE. Robusto a: (a) espaço duplo do Whisper (tratado no _norm),
    # (b) a frase ATRAVESSAR duas cues (a busca cue-a-cue antiga falhava), e (c) o Whisper errar a
    # 1ª palavra — nome próprio "Caracciolo"->"Caracolo", "Vecchia"->"Vichia" — por isso tentamos
    # janelas a partir de vários offsets, não só do prefixo. Monotônico: cada cap depois do anterior.
    blob_parts, span, _pos = [], [], 0
    for (_i, ini, _fim, txt) in cues:
        nt = _norm(txt)
        if not nt:
            continue
        span.append((_pos, ini))
        blob_parts.append(nt)
        _pos += len(nt) + 1
    blob = " ".join(blob_parts)

    def _tempo_da_pos(p):
        t = span[0][1] if span else 0.0
        for off, ini in span:
            if off <= p:
                t = ini
            else:
                break
        return t

    def _casar(frase, desde):
        pal = _norm(frase).split()
        if not pal or not blob:
            return None
        for win in (6, 5, 4):
            melhor = None
            for off in range(0, min(len(pal), 6)):
                if off + win > len(pal):
                    continue
                chave = " ".join(pal[off:off + win])
                if len(chave) < 8:
                    continue
                p = blob.find(chave, desde)
                if p >= 0 and (melhor is None or p < melhor):
                    melhor = p
            if melhor is not None:
                return melhor
        return None

    starts, _desde = {}, 0
    for c in caps:
        p = _casar(c.get("primeira_frase", ""), _desde)
        if p is not None:
            starts[c["n"]] = _tempo_da_pos(p)
            _desde = p + 1

    bnds = []
    ncaps = len(caps)
    for idx, c in enumerate(caps):
        if c["n"] in starts:
            bnds.append(starts[c["n"]])
        else:
            # fallback proporcional: distribui o total pelos capítulos igualmente a partir daqui
            if idx == 0:
                bnds.append(min(6.0, total * 0.05))  # hook ~ pequeno
            else:
                bnds.append(bnds[-1] + (total - bnds[-1]) / max(1, ncaps - idx))
            log("    ⚠ capítulo %d: âncora não casou na SRT — usando estimativa (%.1fs)." % (c["n"], bnds[-1]))
    # garante monotonicidade
    for i in range(1, len(bnds)):
        if bnds[i] <= bnds[i - 1]:
            bnds[i] = min(total, bnds[i - 1] + 1.0)
    return total, bnds


def _slice_audio(ff, src, ini, fim, out, log):
    """Corta [ini, fim) de src -> out (AAC uniforme). fim=None => até o fim."""
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % ini]
    if fim is not None:
        cmd += ["-to", "%.3f" % fim]
    cmd += ["-i", str(src), *_AENC, str(out)]
    _run(cmd, log, "slice-audio")
    return out.exists() and out.stat().st_size > 0


def _silencio(ff, dur, out, log):
    """Gera `dur` s de silêncio AAC uniforme."""
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
           "-t", "%.3f" % max(0.05, dur), *_AENC, str(out)]
    _run(cmd, log, "silencio")
    return out.exists() and out.stat().st_size > 0


# ---------------------------------------------------------------------------
# Blocos de vídeo (mudos) por segmento
# ---------------------------------------------------------------------------

# Presets de Ken Burns (z0,z1, fx0,fx1, fy0,fy1) — movimento CONTÍNUO por toda a duração da
# imagem (nunca congela) + variedade por imagem. fx/fy ∈ [-1,1] = fração do espaço livre do crop
# (0=centro, ±1=borda): x/y escalam pelo próprio (1-1/zoom), então o pan NUNCA abre borda preta,
# em QUALQUER zoom. Substitui o micro-zoom antigo (`+0.0006`, teto 1.12) que congelava a imagem
# após ~6,7 s e não tinha pan/variedade — ver decisoes-changelog 2026-07-09.
_KB_MODES = (
    (1.06, 1.18,  0.0,  0.0,  0.0,  0.0),   # zoom-in central calmo
    (1.06, 1.20, -0.5,  0.5,  0.0,  0.0),   # zoom-in + pan esquerda -> direita
    (1.20, 1.06,  0.5, -0.5,  0.0,  0.0),   # zoom-out + pan direita -> esquerda
    (1.06, 1.18,  0.0,  0.0, -0.5,  0.5),   # zoom-in + pan cima -> baixo
    (1.08, 1.20, -0.4,  0.4,  0.4, -0.4),   # zoom-in diagonal
)


def _kb_expr(mode, frames):
    """Expressões zoompan (z, x, y) p/ um preset, interpoladas LINEARMENTE pelo frame de saída
    `on` ao longo de `frames` frames (progressão 0->1). x/y ficam sempre dentro dos limites."""
    z0, z1, fx0, fx1, fy0, fy1 = mode
    d = max(1, frames - 1)
    z = "%.4f+(%.4f)*on/%d" % (z0, z1 - z0, d)
    fx = "(%.3f+(%.3f)*on/%d)" % (fx0, fx1 - fx0, d)
    fy = "(%.3f+(%.3f)*on/%d)" % (fy0, fy1 - fy0, d)
    # centro do crop = iw/2 - iw/zoom/2; deslocamento = fração fx do espaço livre (= o próprio centro).
    x = "(1+%s)*(iw/2-iw/zoom/2)" % fx
    y = "(1+%s)*(ih/2-ih/zoom/2)" % fy
    return z, x, y


def _kenburns_imagens(ff, imgs, dur, w, h, fps, out, log):
    """Slideshow com Ken Burns DINÂMICO: N imagens dividindo `dur` igualmente; cada imagem faz um
    movimento CONTÍNUO (zoom-in/-out + pan) que dura a cena inteira e varia por imagem (presets
    _KB_MODES, ciclados por índice). Vídeo MUDO. Concatena via filter_complex (uma passada)."""
    imgs = [i for i in imgs if Path(i).exists()]
    if not imgs:
        return _tela_cor(ff, dur, w, h, fps, out, log)
    n = len(imgs)
    seg = max(0.5, dur / n)
    frames = max(2, int(round(seg * fps)))
    inputs, filtros, labels = [], [], []
    for k, img in enumerate(imgs):
        # -framerate fps + -t seg alimenta EXATAMENTE seg*fps frames; zoompan d=1 emite 1 frame de
        # saída por frame de entrada, e `on` (0..frames-1) dá a progressão. O movimento é função de
        # `on` (NÃO acumulador com teto), então nunca congela. Pré-escala 2x p/ não serrilhar.
        z, x, y = _kb_expr(_KB_MODES[k % len(_KB_MODES)], frames)
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", "%.3f" % seg, "-i", str(img)]
        filtros.append(
            "[%d:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,"
            "zoompan=z='%s':x='%s':y='%s':d=1:s=%dx%d:fps=%d,setsar=1[v%d]"
            % (k, w * 2, h * 2, w * 2, h * 2, z, x, y, w, h, fps, k))
        labels.append("[v%d]" % k)
    fc = ";".join(filtros) + ";" + "".join(labels) + "concat=n=%d:v=1:a=0[v]" % n
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", fc, "-map", "[v]", *_venc_args(fps), "-an", str(out)]
    _run(cmd, log, "kenburns")
    return out.exists() and out.stat().st_size > 0


def _tela_cor(ff, dur, w, h, fps, out, log, cor="black"):
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "color=c=%s:s=%dx%d:r=%d" % (cor, w, h, fps), "-t", "%.3f" % max(0.1, dur),
           *_venc_args(fps), "-an", str(out)]
    _run(cmd, log, "tela-cor")
    return out.exists() and out.stat().st_size > 0


def _video_ajustado(ff, src, dur, w, h, fps, out, log, mudo=True):
    """Enquadra um vídeo drop-in em w×h e (opcional) corta em `dur`. mudo=True remove o áudio."""
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if dur is not None:
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-t", "%.3f" % dur, "-i", str(src)]
    cmd += ["-vf", _fit(w, h), *_venc_args(fps)]
    cmd += (["-an"] if mudo else [*_AENC])
    cmd += [str(out)]
    _run(cmd, log, "video-ajustado")
    return out.exists() and out.stat().st_size > 0


# ---------------------------------------------------------------------------
# TEASER sincronizado às FRASES do gancho (não mais divisão igual)
# ---------------------------------------------------------------------------
# O gancho (texto antes de "Chapter 1" no roteiro.txt) é narrado nos primeiros `hook_dur`
# segundos. Em vez de dividir esse tempo em N fatias iguais (o que fazia os cortes de clipe
# caírem NO MEIO das frases faladas), casamos as frases do gancho com as cues REAIS da
# narration.srt e agrupamos as frases uniformemente entre os N clipes drop-in — cada corte de
# clipe cai SEMPRE numa fronteira de frase falada. Escolha do editor (2026-07-09).
# Desliga com ROTEIRO_TEASER_SYNC=0 (volta à divisão igual).

def _lev1(a, b):
    """Distância de edição, mas só interessa saber se é <=1 (retorna 2 pra 'maior')."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return 2
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[lb]


def _palavra_igual(a, b):
    """Casamento tolerante a erros do Whisper (fiancé→fiance, Caracciolo→Caracolo)."""
    if not a or not b:
        return False
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    return min(len(a), len(b)) >= 4 and _lev1(a, b) <= 1


def _hook_sentencas(hook):
    """Quebra o gancho em frases (beats). Split em . ! ? … (a fala do TTS respeita esses breaks)."""
    txt = re.sub(r"\s+", " ", (hook or "").replace("\n", " ")).strip()
    if not txt:
        return []
    partes = re.split(r"(?<=[.!?…])\s+", txt)
    return [p.strip() for p in partes if p.strip()]


def _word_timeline(cues, ate):
    """[(palavra_normalizada, t_inicio_estimado), ...] até `ate` s. Distribui a duração de cada
    cue entre suas palavras proporcional ao nº de chars (dá precisão sub-cue às fronteiras)."""
    tl = []
    for (_i, ini, fim, txt) in cues:
        if ini >= ate:
            break
        palavras = txt.split()
        if not palavras:
            continue
        dur = max(0.001, fim - ini)
        total = sum(len(p) for p in palavras) or len(palavras)
        acc = ini
        for p in palavras:
            wn = re.sub(r"[^a-z0-9]+", "", p.lower())
            if wn:
                tl.append((wn, acc))
            acc += dur * (len(p) / total)
    return tl


def _hook_beats(proj, ff, hook_dur, log):
    """Fronteiras (s) de cada FRASE do gancho dentro de [0, hook_dur), casando o texto do gancho
    (roteiro.txt) com as cues reais de narration.srt. Devolve [(ini, fim), ...] por frase, ou
    None se não der pra sincronizar (o chamador cai na divisão igual)."""
    if hook_dur <= 0.5 or not proj.existe(proj.roteiro) or not proj.existe(proj.narration_srt):
        return None
    try:
        import roteiro_estrutura
        hook = roteiro_estrutura.parse_roteiro(
            proj.roteiro.read_text(encoding="utf-8", errors="replace")).get("hook", "")
    except Exception:
        return None
    sents = _hook_sentencas(hook)
    if len(sents) < 2:
        return None
    tl = _word_timeline(parse_srt(proj.narration_srt), hook_dur + 1.5)
    if len(tl) < 4:
        return None

    M = len(sents)
    B = [None] * (M + 1)
    B[0], B[M] = 0.0, float(hook_dur)
    prev = 0
    for i in range(1, M):
        chave = re.sub(r"[^a-z0-9]+", " ", sents[i].lower()).split()[:6]
        if not chave:
            continue
        alvo = max(2, (len(chave) + 1) // 2)
        melhor_j, melhor_sc = None, 0
        for j in range(prev, len(tl)):
            sc = sum(1 for p in range(len(chave))
                     if j + p < len(tl) and _palavra_igual(chave[p], tl[j + p][0]))
            if sc > melhor_sc:
                melhor_sc, melhor_j = sc, j
                if sc == len(chave):
                    break
        if melhor_j is not None and melhor_sc >= alvo and 0 < tl[melhor_j][1] < hook_dur:
            B[i] = tl[melhor_j][1]
            prev = melhor_j + 1

    # interpola frases não-casadas por posição de char cumulativa entre as vizinhas conhecidas
    cum = [0]
    for s in sents:
        cum.append(cum[-1] + max(1, len(s)))
    for i in range(1, M):
        if B[i] is None:
            lo, hi = i, i
            while B[lo] is None:
                lo -= 1
            while B[hi] is None:
                hi += 1
            frac = ((cum[i] - cum[lo]) / (cum[hi] - cum[lo])) if cum[hi] > cum[lo] else (i - lo) / (hi - lo)
            B[i] = B[lo] + (B[hi] - B[lo]) * frac
    for i in range(1, M + 1):
        if B[i] <= B[i - 1]:
            B[i] = min(hook_dur, B[i - 1] + 0.2)
    B[M] = float(hook_dur)
    if B[M] <= B[M - 1]:
        return None
    return [(B[i], B[i + 1]) for i in range(M)]


def _agrupar_beats(beats, n):
    """Reparte M beats (frases) em n grupos contíguos o mais uniforme possível (por nº de beats).
    Devolve [(ini, fim), ...] por grupo. Requer n <= M (garantido pelo chamador)."""
    M = len(beats)
    fron = sorted(set(min(M, max(0, round(i * M / n))) for i in range(n + 1)))
    if fron[0] != 0:
        fron.insert(0, 0)
    if fron[-1] != M:
        fron.append(M)
    grupos = []
    for k in range(len(fron) - 1):
        a, b = fron[k], fron[k + 1]
        if b > a:
            grupos.append((beats[a][0], beats[b - 1][1]))
    return grupos


def _teaser_sync_off():
    v = os.environ.get("ROTEIRO_TEASER_SYNC", "1").strip().lower()
    return v in ("0", "off", "none", "nao", "não", "no", "false")


def _teaser_duracoes(proj, ff, n_clips, hook_dur, log):
    """Duração-alvo de cada clipe do teaser (uma por clipe, somando hook_dur). Sincroniza às
    frases do gancho; cai na divisão igual se o sync estiver off ou não for possível."""
    igual = [hook_dur / n_clips] * n_clips
    if _teaser_sync_off():
        return igual, "divisão igual (ROTEIRO_TEASER_SYNC=0)"
    beats = None
    try:
        beats = _hook_beats(proj, ff, hook_dur, log)
    except Exception as e:
        log("    ⚠ teaser: falha ao mapear as frases do gancho (%s) — divisão igual." % e)
    if not beats:
        return igual, "divisão igual (gancho não mapeado na SRT)"
    if len(beats) < n_clips:
        return igual, ("divisão igual (%d clipes > %d frases do gancho — largue até %d clipes p/ sincronizar)"
                       % (n_clips, len(beats), len(beats)))
    grupos = _agrupar_beats(beats, n_clips)
    if len(grupos) != n_clips:
        return igual, "divisão igual (agrupamento inconsistente)"
    durs = [max(0.2, b - a) for (a, b) in grupos]
    resumo = ", ".join("c%d=%.1fs" % (k + 1, d) for k, d in enumerate(durs))
    return durs, "sincronizado às frases do gancho (%d frases → %d clipes: %s)" % (len(beats), n_clips, resumo)


def _clip_para_dur(ff, src, dur, w, h, fps, out, log):
    """Enquadra um clipe drop-in em w×h e o entrega com EXATAMENTE `dur` s (mudo): corta se for
    mais longo, repete (stream_loop) se for mais curto — garante que o teaser cubra o gancho todo."""
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-stream_loop", "-1", "-t", "%.3f" % max(0.2, dur), "-i", str(src),
           "-vf", _fit(w, h), *_venc_args(fps), "-an", str(out)]
    _run(cmd, log, "teaser-clip")
    return out.exists() and out.stat().st_size > 0


def _concat_videos_trim(ff, clips, duracoes, w, h, fps, out, log):
    """Concatena os clipes de teaser (mudos), cada um entregue na sua duração-alvo `duracoes[k]`
    (cortes alinhados aos beats do gancho — ver _teaser_duracoes). Cada clipe é enquadrado em w×h."""
    if not clips:
        return _tela_cor(ff, sum(duracoes) or 1.0, w, h, fps, out, log)
    partes = []
    tmp = out.parent
    for k, c in enumerate(clips):
        dur = duracoes[k] if k < len(duracoes) else (duracoes[-1] if duracoes else 1.0)
        pc = tmp / ("_teaser_%02d.mp4" % k)
        _clip_para_dur(ff, c, dur, w, h, fps, pc, log)
        if pc.exists() and pc.stat().st_size > 0:
            partes.append(pc)
    if not partes:
        return _tela_cor(ff, sum(duracoes) or 1.0, w, h, fps, out, log)
    lista = tmp / "_teaser_concat.txt"
    lista.write_text("".join("file '%s'\n" % p.name for p in partes), encoding="utf-8")
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(lista), "-c", "copy", str(out)], log, "teaser-concat")
    lista.unlink(missing_ok=True)
    for p in partes:
        p.unlink(missing_ok=True)
    return out.exists() and out.stat().st_size > 0


def _mux(ff, video_mudo, audio, out, log):
    """Junta um vídeo mudo + uma faixa de áudio (duração = a do vídeo)."""
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_mudo), "-i", str(audio),
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *_AENC, "-shortest", str(out)]
    _run(cmd, log, "mux")
    return out.exists() and out.stat().st_size > 0


# ---------------------------------------------------------------------------
# Drop-in por canal
# ---------------------------------------------------------------------------

def _listar(pasta, exts):
    d = Path(pasta)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts)


def _drop(base, sub, exts=VIDEO_EXTS):
    return _listar(base / sub, exts)


# ---------------------------------------------------------------------------
# Concat + legenda (re-transcrita) do vídeo montado
# ---------------------------------------------------------------------------

def _concat_segmentos(ff, segmentos, tmp, fps, log):
    """Concatena os MP4 dos segmentos -> _concat.mp4. Tenta -c copy; se falhar (formato
    divergente), re-encoda. Devolve o Path do concat."""
    concat = tmp / "_concat.mp4"
    lista = tmp / "_segmentos.txt"
    lista.write_text("".join("file '%s'\n" % s.name for s in segmentos), encoding="utf-8")
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(lista), "-c", "copy", str(concat)], log, "concat-final")
    if not (concat.exists() and concat.stat().st_size > 0):
        log("    concat -c copy falhou — re-encodando os segmentos (mais lento).")
        _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
              "-i", str(lista), *_venc_args(fps), *_AENC, str(concat)], log, "concat-reencode")
    lista.unlink(missing_ok=True)
    if not (concat.exists() and concat.stat().st_size > 0):
        raise ErroPipeline("Falha ao concatenar os segmentos.")
    return concat


def _whisper_srt(proj, audio, tmp, log):
    """Transcreve `audio` (a narração JÁ MONTADA) via gerar-srt-en.py -> .srt no MESMO nome.

    Roda com cwd=tmp p/ o .srt cair ali. Devolve o Path do .srt ou None (não-fatal)."""
    from common import WHISPER_SCRIPT, achar_python, idioma
    if not WHISPER_SCRIPT.is_file():
        log("    ⚠ Whisper não encontrado (%s) — vídeo sem legenda." % WHISPER_SCRIPT)
        return None
    env = dict(os.environ)
    env["WHISPER_LANG"] = idioma()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = achar_python() + [str(WHISPER_SCRIPT), audio.name]
    subprocess.run(cmd, cwd=str(tmp), env=env, stdout=subprocess.PIPE,
                   stderr=subprocess.STDOUT, **SUBPROCESS_FLAGS)
    srt = tmp / (audio.stem + ".srt")
    return srt if (srt.exists() and srt.stat().st_size > 0) else None


def _legendar(proj, ff, video_montado, tmp, w, h, fps, log):
    """Queima a legenda no vídeo MONTADO. Como a montagem insere capas (silêncio) entre os
    capítulos, a narration.srt original NÃO casa — então RE-TRANSCREVE o áudio já montado
    (Whisper) e a legenda fica perfeitamente sincronizada. Não-fatal (devolve None em falha).

    Roda o filtro `subtitles` com cwd=tmp e nomes RELATIVOS p/ evitar o escaping do `:` do
    Windows nos caminhos."""
    narr = tmp / "_legenda_audio.mp3"
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_montado),
          "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(narr)], log, "extrai-audio")
    if not (narr.exists() and narr.stat().st_size > 0):
        return None
    log("    legenda: re-transcrevendo o áudio montado (Whisper) p/ sincronia exata...")
    srt = _whisper_srt(proj, narr, tmp, log)
    if not srt:
        return None
    out = tmp / "_capd.mp4"
    style = _legenda_style(w, h)
    maximo = _caption_max_chars()
    ass = _preparar_ass(ff, srt, maximo, w, h)  # UMA linha só (≤ maximo), padrão long form
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", video_montado.name,
          "-vf", "subtitles=%s:force_style='%s'" % (ass.name, style),
          *_venc_args(fps), "-c:a", "copy", out.name], log, "legenda", cwd=tmp)
    narr.unlink(missing_ok=True)
    if out.exists() and out.stat().st_size > 0:
        log("    ✓ legenda queimada (uma linha ≤%d, estilo vertical, MarginV alto)." % maximo)
        return out
    log("    ⚠ falha ao queimar a legenda — seguindo sem legenda.")
    return None


# ---------------------------------------------------------------------------
# Montagem principal
# ---------------------------------------------------------------------------

def construir(proj, log, cancel=None, parte=None):
    ff = achar_ffmpeg()
    w, h = _dim()
    fps = _fps()
    if not proj.existe(proj.narration_mp3):
        raise ErroPipeline("Falta narration.mp3 (Etapa 3) para montar.")

    total, bnds = _boundaries(proj, ff, log)
    caps = json.loads((proj.dir / "capitulos.json").read_text(encoding="utf-8")).get("capitulos", [])
    ncaps = len(caps)
    log("  narração %.1fs, %d capítulos; limites: %s"
        % (total, ncaps, ", ".join("%.1f" % b for b in bnds)))

    canal = ""
    if parte is None:
        parte = ""
    if proj.existe(proj.source):
        try:
            _src = json.loads(proj.source.read_text(encoding="utf-8"))
            canal = (_src.get("canal") or "").strip()
            if not parte:
                parte = (_src.get("parte") or "").strip()
        except (OSError, ValueError):
            pass
    # Perfil de montagem: P2/extensão = SÓ capas + corpo (sem teaser-isca, resumo P2 ou CTA).
    # A isca (P1) mantém a estrutura completa. Sinal: `parte` (passado pela Etapa 7) ou o campo
    # "parte" do source.json (semeado pelo pipeline na pasta da P2).
    extensao = str(parte).lower() == "p2"
    base_mat = materiais_canal(canal)
    modelos = ler_modelos(canal)

    tmp = proj.dir / "out"
    tmp.mkdir(parents=True, exist_ok=True)

    # mapa capítulo -> imagens (via prefixo C<n>| dos prompts, na ordem = img_NNN)
    imgs_por_cap = {c["n"]: [] for c in caps}
    if proj.existe(proj.prompts_imagens):
        linhas = [l for l in proj.prompts_imagens.read_text(encoding="utf-8", errors="replace").splitlines()
                  if l.strip()]
        for idx, l in enumerate(linhas, 1):
            m = re.match(r"\s*C(\d+)\s*\|", l)
            if m and int(m.group(1)) in imgs_por_cap:
                img = proj.images_dir / ("img_%03d.png" % idx)
                if img.exists():
                    imgs_por_cap[int(m.group(1))].append(str(img))
    # fallback: se nenhum prompt mapeou, distribui todas as imagens igualmente
    todas_imgs = sorted(str(p) for p in proj.images_dir.glob("img_*.png"))
    if todas_imgs and not any(imgs_por_cap.values()):
        for i, img in enumerate(todas_imgs):
            imgs_por_cap[caps[i % ncaps]["n"]].append(img)

    segmentos = []  # lista de MP4 finais (vídeo+áudio) na ordem

    def _seg_path(nome):
        return tmp / ("seg_%s.mp4" % nome)

    # --- 1) TEASER (gancho) — SÓ na isca (P1). A extensão (P2) começa direto no cap 1. -----
    if extensao:
        log("    P2 (extensão): sem teaser-isca — o capítulo 1 começa do 0.")
    else:
        hook_dur = max(1.0, bnds[0]) if bnds else min(8.0, total)
        # Teaser é POR VÍDEO: lê de projects/<slug>/teaser/ (isolado deste card). Só cai no
        # drop-in por canal (materiais/<canal>/teaser/) como fallback legado, se o projeto não tiver.
        teaser_clips = _drop(proj.dir, "teaser") or _drop(base_mat, "teaser")
        seg = _seg_path("00_teaser")
        if not (seg.exists() and seg.stat().st_size > 0):
            vmudo = tmp / "_v_teaser.mp4"
            if teaser_clips:
                durs, motivo = _teaser_duracoes(proj, ff, len(teaser_clips), hook_dur, log)
                _concat_videos_trim(ff, teaser_clips, durs, w, h, fps, vmudo, log)
                log("    teaser: %d clipe(s) drop-in cobrindo %.1fs do gancho — %s."
                    % (len(teaser_clips), hook_dur, motivo))
            else:
                primeira = imgs_por_cap.get(caps[0]["n"], []) if caps else []
                _kenburns_imagens(ff, primeira[:1] or todas_imgs[:1], hook_dur, w, h, fps, vmudo, log)
                log("    ⚠ sem clipes de teaser (projects/<slug>/teaser/ nem materiais/%s/teaser/)"
                    " — gancho vira Ken Burns da 1ª imagem." % base_mat.name)
            aud = tmp / "_a_teaser.m4a"
            _slice_audio(ff, proj.narration_mp3, 0.0, hook_dur, aud, log)
            _mux(ff, vmudo, aud, seg, log)
            vmudo.unlink(missing_ok=True); aud.unlink(missing_ok=True)
        segmentos.append(seg)

    # --- 2) CAPÍTULOS ---------------------------------------------------------
    capa_pos = _capa_pos()
    for idx, c in enumerate(caps):
        if cancel is not None and cancel.is_set():
            raise ErroPipeline("Cancelado pelo usuário.")
        n = c["n"]
        # Na extensão (P2) não há teaser, então o 1º capítulo começa em 0 (não perde a narração
        # do trecho que, na isca, iria por baixo do teaser).
        ini = 0.0 if (extensao and idx == 0) else bnds[idx]
        fim = bnds[idx + 1] if idx + 1 < len(bnds) else None
        capa = proj.covers_dir / ("capa_%02d.mp4" % (idx + 1))

        def _seg_capa():
            s = _seg_path("%02d_capa" % n)
            if s.exists() and s.stat().st_size > 0:
                return s
            if not (capa.exists() and capa.stat().st_size > 0):
                return None
            vmudo = tmp / ("_v_capa_%02d.mp4" % n)
            _video_ajustado(ff, capa, None, w, h, fps, vmudo, log, mudo=True)
            dur = _dur(ff, vmudo)
            aud = tmp / ("_a_capa_%02d.m4a" % n)
            _silencio(ff, dur, aud, log)          # a capa é a "pausa": narração em silêncio
            _mux(ff, vmudo, aud, s, log)
            vmudo.unlink(missing_ok=True); aud.unlink(missing_ok=True)
            return s

        def _seg_corpo():
            s = _seg_path("%02d_corpo" % n)
            if s.exists() and s.stat().st_size > 0:
                return s
            dur = (fim - ini) if fim is not None else (total - ini)
            dur = max(1.0, dur)
            vmudo = tmp / ("_v_corpo_%02d.mp4" % n)
            _kenburns_imagens(ff, imgs_por_cap.get(n, []) or todas_imgs, dur, w, h, fps, vmudo, log)
            # o vídeo pode sair um tico maior/menor que dur (arredondamento de frames);
            # a fatia de áudio manda — usamos -shortest no mux.
            aud = tmp / ("_a_corpo_%02d.m4a" % n)
            _slice_audio(ff, proj.narration_mp3, ini, fim, aud, log)
            _mux(ff, vmudo, aud, s, log)
            vmudo.unlink(missing_ok=True); aud.unlink(missing_ok=True)
            return s

        capa_seg = _seg_capa()
        corpo_seg = _seg_corpo()
        ordem = [capa_seg, corpo_seg] if capa_pos == "antes" else [corpo_seg, capa_seg]
        for s in ordem:
            if s is not None:
                segmentos.append(s)
        log("    capítulo %d: %.1fs de corpo%s." % (
            n, ((fim or total) - ini), ", com capa" if capa_seg else " (sem capa)"))

    # --- 3) RESUMO P2 + CTA (drop-in) ----------------------------------------
    def _drop_seg(nome, subpasta, modelo_key, proj_stem=None):
        s = _seg_path(nome)
        if s.exists() and s.stat().st_size > 0:
            return s
        alvo = None
        # POR-VÍDEO (prioridade): um clipe <proj_stem>.<ext> largado na PASTA DO PROJETO.
        # Usado pelo resumo da Parte 2, que muda a cada vídeo (não dá pra fixar por canal).
        if proj_stem:
            for p in sorted(proj.dir.glob(proj_stem + ".*")):
                if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                    alvo = p
                    break
        if alvo is None:
            clipes = _drop(base_mat, subpasta)
            if clipes:
                alvo = clipes[0]
            elif modelos.get(modelo_key) and Path(modelos[modelo_key]).is_file():
                alvo = Path(modelos[modelo_key])
        if not alvo:
            return None
        # mantém o áudio próprio do clipe se tiver; senão silêncio
        if _tem_audio(ff, alvo):
            _video_ajustado(ff, alvo, None, w, h, fps, s, log, mudo=False)
        else:
            vmudo = tmp / ("_v_%s.mp4" % nome)
            _video_ajustado(ff, alvo, None, w, h, fps, vmudo, log, mudo=True)
            aud = tmp / ("_a_%s.m4a" % nome)
            _silencio(ff, _dur(ff, vmudo), aud, log)
            _mux(ff, vmudo, aud, s, log)
            vmudo.unlink(missing_ok=True); aud.unlink(missing_ok=True)
        return s

    if extensao:
        log("    P2 (extensão): sem resumo P2 nem CTA (só capas + corpo).")
    else:
        resumo = _drop_seg("90_resumo", "book2", "resumo_p2", proj_stem="resumo_p2")
        if resumo:
            segmentos.append(resumo); log("    resumo P2 adicionado (%s)." % resumo.name)
        else:
            log("    (sem resumo P2 — largue resumo_p2.mp4 na pasta do projeto, ou em materiais/%s/book2/)." % base_mat.name)
        cta = _drop_seg("91_cta", "cta", "cta_final")
        if cta:
            segmentos.append(cta); log("    CTA final (drop-in) adicionada.")
        else:
            log("    (sem CTA — materiais/%s/cta/ vazio e sem modelo cta_final)." % base_mat.name)

    # --- 4) CONCAT dos segmentos ---------------------------------------------
    segmentos = [s for s in segmentos if s and s.exists() and s.stat().st_size > 0]
    if not segmentos:
        raise ErroPipeline("Nenhum segmento montado — verifique narração/imagens.")
    concat = _concat_segmentos(ff, segmentos, tmp, fps, log)

    # --- 5) LEGENDA (opcional, re-transcrita p/ casar com cortes/pausas) ------
    video_src = concat
    if _legenda_on():
        try:
            capd = _legendar(proj, ff, concat, tmp, w, h, fps, log)
            if capd:
                video_src = capd
        except Exception as e:
            log("    ⚠ legenda não aplicada (%s) — seguindo sem legenda." % e)

    # --- 6) ÁUDIO tratado (cleanup+loudnorm) + MÚSICA -> final ----------------
    musica = _musica(base_mat, modelos)
    final = proj.final_mp4
    af = _audio_filtro()
    if musica:
        db = os.environ.get("ROTEIRO_MUSICA_DB", "-10")
        narr = "[0:a]%s[n]" % af if af else "[0:a]anull[n]"
        fc = ("%s;[1:a]volume=%sdB[m];[n][m]amix=inputs=2:duration=first:dropout_transition=0[a]"
              % (narr, db))
        _run([ff, "-y", "-hide_banner", "-loglevel", "error",
              "-i", str(video_src), "-stream_loop", "-1", "-i", str(musica),
              "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
              "-c:v", "copy", *_AENC, str(final)], log, "musica")
        if final.exists() and final.stat().st_size > 0:
            log("    música de fundo (%sdB) mixada%s." % (db, " + áudio tratado" if af else ""))
        else:
            log("    ⚠ falha ao mixar música — usando o vídeo sem trilha.")
            shutil.copyfile(video_src, final)
    elif af:
        _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_src),
              "-af", af, "-map", "0:v", "-map", "0:a", "-c:v", "copy", *_AENC, str(final)],
             log, "audio-trata")
        if final.exists() and final.stat().st_size > 0:
            log("    áudio tratado (cleanup + loudnorm).")
        else:
            shutil.copyfile(video_src, final)
    else:
        log("    (sem música — materiais/%s/padronizados/ sem faixa e sem modelo)." % base_mat.name)
        shutil.copyfile(video_src, final)
    concat.unlink(missing_ok=True)
    (tmp / "_capd.mp4").unlink(missing_ok=True)

    if not (final.exists() and final.stat().st_size > 0):
        raise ErroPipeline("Montagem não produziu out/final.mp4.")
    dur_final = _dur(ff, final)
    log("    ✓ out/final.mp4 pronto (%.1fs, %d segmentos, %dx%d @ %dfps)."
        % (dur_final, len(segmentos), w, h, fps))
    return final


def _musica(base_mat, modelos):
    """Faixa de música de fundo: 1ª faixa em materiais/<canal>/padronizados/ (áudio) ou o
    modelo (nenhuma chave dedicada — reusa 'padronizados'). None se não houver."""
    from common import AUDIO_EXTS
    faixas = _listar(base_mat / "padronizados", AUDIO_EXTS)
    if faixas:
        return faixas[0]
    return None
