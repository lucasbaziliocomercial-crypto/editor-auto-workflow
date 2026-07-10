# -*- coding: utf-8 -*-
"""montagem_vertical.py — engine de montagem do vídeo de romance (vertical), 100% FFmpeg.

Reproduz a ESTRUTURA que o Romance Maker montava no CapCut, mas RENDERIZA um MP4 headless
(o pipeline exige out/final.mp4 — sem passo manual no CapCut). Timeline:

    TEASER → [INTRO PÓS TEASER] → [capa_1] corpo_1 → … → RABO DA ISCA
    └ clipes drop-in mudos, cortados p/ durar o GANCHO falado; narração do gancho por baixo
                    └ corpo = imagens do capítulo (Ken Burns) sincronizadas à narração daquele cap.
                       a capa (Etapa 6) anuncia o capítulo; a narradora DITA "Chapter N — Título"
                       por baixo (covers/titulo_NN.mp3, Etapa 3), com respiro no fim

    RABO DA ISCA (P1), ordem fixa da editora (2026-07-09):
        INTRO BOOK 02 → RESUMO → CTA → AVISO DE CLONE → TUTORIAL PLATAFORMA → MINUTO FINAL
    ├ FIXAS por canal (clipes prontos com áudio próprio E LEGENDA PRÓPRIA; pulam se faltarem):
    │   INTRO PÓS TEASER, INTRO BOOK 02, AVISO DE CLONE, TUTORIAL PLATAFORMA — ver detalhes.py.
    │   A legenda re-transcrita NÃO é queimada por cima delas (já trazem a própria); a música de
    │   fundo continua por baixo. Decisão da editora (2026-07-09).
    └ GERADAS (template fixo, takes trocados pelos do teaser): RESUMO/CTA (resumo_cta.py),
        MINUTO FINAL (ultimo_minuto.py).

    música de fundo (-10 dB) por baixo de tudo.

Como sincroniza: a narração é a espinha. Os limites de cada capítulo na narration.mp3 são
achados casando a `primeira_frase` de cada capítulo (capitulos.json) com as cues da
narration.srt. A partir daí cada segmento recebe a fatia de áudio correspondente. A capa toca a
narração do título (`covers/titulo_NN.mp3`) — sua duração (Etapa 6) já é o tempo dessa fala +
respiro, então casa por construção, igual RM. Sem o áudio do título (kill-switch/TTS falhou), a
capa cai no silêncio legado (a narração "pausa" na troca).

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
from concurrent.futures import ThreadPoolExecutor

from common import (ErroPipeline, achar_ffmpeg, SUBPROCESS_FLAGS, parse_srt,
                    materiais_canal, materiais_dirs, ler_modelos, IMG_EXTS)

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


# Encoders de HARDWARE tentados (em ordem de preferência) quando ROTEIRO_ENCODER=auto.
# nvenc (NVIDIA) > qsv (Intel Quick Sync) > amf (AMD) > libx264 (CPU, sempre funciona).
# Cada máquina resolve pro que ELA tem: PC do RTX 2060 → nvenc; notebook Intel → qsv;
# notebook AMD → amf. Sem NVIDIA/Intel/AMD utilizável → cai no libx264.
_HW_PRIORIDADE = ("h264_nvenc", "h264_qsv", "h264_amf")
_ENCODER_RESOLVIDO = None  # cache por processo (a detecção — que encoda — só roda 1x)


def _encoders_listados(ff):
    """Nomes de encoder que o build do ffmpeg CONHECE (segunda coluna de `-encoders`)."""
    try:
        r = subprocess.run([ff, "-hide_banner", "-encoders"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **SUBPROCESS_FLAGS)
        out = (r.stdout or b"").decode("utf-8", "replace")
    except Exception:
        return set()
    nomes = set()
    for linha in out.splitlines():
        m = re.match(r"\s*[VAS][.\w]{5}\s+(\S+)", linha)
        if m:
            nomes.add(m.group(1))
    return nomes


def _venc_extra(enc):
    """Args de qualidade/velocidade do encoder (SEM -c:v / -pix_fmt / -r). Separado de
    _venc_args pra ser reusado no smoke-test da detecção sem recursão."""
    if enc == "libx264":
        return ["-preset", os.environ.get("ROTEIRO_X264_PRESET", "fast"),
                "-crf", os.environ.get("ROTEIRO_CRF", "18")]
    if "nvenc" in enc:
        return ["-preset", os.environ.get("ROTEIRO_NVENC_PRESET", "p4"),
                "-rc", "vbr", "-cq", os.environ.get("ROTEIRO_NVENC_CQ", "21"), "-b:v", "0"]
    if "qsv" in enc:  # Intel Quick Sync — ICQ via -global_quality (menor = melhor)
        return ["-preset", os.environ.get("ROTEIRO_QSV_PRESET", "veryfast"),
                "-global_quality", os.environ.get("ROTEIRO_QSV_GQ", "23")]
    if "amf" in enc:  # AMD AMF — CQP (qp constante; menor = melhor)
        qp = os.environ.get("ROTEIRO_AMF_QP", "22")
        return ["-quality", os.environ.get("ROTEIRO_AMF_QUALITY", "balanced"),
                "-rc", "cqp", "-qp_i", qp, "-qp_p", qp, "-qp_b", qp]
    return []


def _encoder_funciona(ff, enc):
    """Smoke-test REAL: 'listado em -encoders' NÃO garante que roda. Ex.: o build tem
    h264_qsv compilado, mas numa máquina sem GPU Intel ele estoura 'MFX session -9'.
    Encoda 8 frames de teste; True só se gerar saída válida sem erro."""
    import tempfile
    pix = "nv12" if "qsv" in enc else "yuv420p"
    saida = Path(tempfile.gettempdir()) / ("_enc_probe_%s.mp4" % enc.replace("/", "_"))
    try:
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
               "-i", "testsrc2=s=256x256:r=30", "-frames:v", "8",
               "-c:v", enc, "-pix_fmt", pix, *_venc_extra(enc), str(saida)]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **SUBPROCESS_FLAGS)
        ok = r.returncode == 0 and saida.exists() and saida.stat().st_size > 0
    except Exception:
        ok = False
    try:
        saida.unlink(missing_ok=True)
    except Exception:
        pass
    return ok


def _encoder():
    """Encoder de vídeo. ROTEIRO_ENCODER='auto' (default) = detecta o melhor de HARDWARE
    que ESTA máquina realmente suporta (nvenc→qsv→amf→libx264), via smoke-test, cacheado
    por processo. Um valor explícito (ex.: 'h264_nvenc', 'libx264') é respeitado sem detectar."""
    val = (os.environ.get("ROTEIRO_ENCODER", "auto").strip() or "auto")
    if val.lower() != "auto":
        return val
    global _ENCODER_RESOLVIDO
    if _ENCODER_RESOLVIDO is not None:
        return _ENCODER_RESOLVIDO
    from common import achar_ffmpeg as _aff
    ff = _aff()
    listados = _encoders_listados(ff)
    escolhido = "libx264"
    for enc in _HW_PRIORIDADE:
        if enc in listados and _encoder_funciona(ff, enc):
            escolhido = enc
            break
    _ENCODER_RESOLVIDO = escolhido
    return escolhido


def _capa_pos():
    """Onde a capa do capítulo entra: 'antes' (anuncia o capítulo, padrão do Romance Maker)
    ou 'depois' (fecha o capítulo). Sobrescrevível por ROTEIRO_CAPA_POS."""
    return os.environ.get("ROTEIRO_CAPA_POS", "antes").strip().lower()


def _cover_narrar_on():
    """Se a capa deve tocar a narração do título ('Chapter N — Título', covers/titulo_NN.mp3,
    gerado na Etapa 3) em vez de silêncio. Desliga com ROTEIRO_COVER_NARRAR_TITULO=0."""
    v = os.environ.get("ROTEIRO_COVER_NARRAR_TITULO", "1").strip().lower()
    return v not in ("0", "off", "nao", "não", "no", "false")


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
    """Args de encode de vídeo, uniformes p/ todo segmento (o concat exige formato igual).

    O encoder vem de _encoder() (default 'auto' = detecta o melhor de HARDWARE da máquina):
    NVENC (NVIDIA), QSV (Intel Quick Sync) ou AMF (AMD) encodam 1080p a centenas de fps —
    3-8x mais rápido que a CPU (libx264) — e é o maior ganho da Etapa 7, que reencoda o vídeo
    inteiro várias vezes (Ken Burns, legenda, QR). Os parâmetros por encoder ficam em _venc_extra."""
    enc = _encoder()
    pix = "nv12" if "qsv" in enc else "yuv420p"  # QSV entrega nv12; o resto, yuv420p
    return ["-c:v", enc, "-pix_fmt", pix, "-r", str(fps)] + _venc_extra(enc)


_AENC = ["-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2"]


# Tratamento de áudio da narração (cleanup + loudnorm), aplicado SÓ no mix final — porta a
# cadeia da esteira-modelo (seção 8.4): limpeza primeiro, loudnorm SEMPRE por último (define o
# volume). Níveis por ROTEIRO_AUDIO_NIVEL (leve|media|forte|off). loudnorm I=-14 (padrão da casa).
_AUDIO_LIMPEZA = {
    "leve":  "highpass=f=80,afftdn=nf=-25:nr=10",
    "media": "highpass=f=85,afftdn=nf=-30:nr=18:tn=1,deesser=i=0.4",
    "forte": "highpass=f=85,anlmdn=s=0.0005,afftdn=nf=-35:nr=24:tn=1,deesser=i=0.5,lowpass=f=14000",
}


def _audio_limpeza():
    """Só o estágio de LIMPEZA da narração (highpass/denoise/deesser), SEM o loudnorm.
    '' se ROTEIRO_AUDIO_NIVEL=off. O loudnorm sai separado (_audio_loudnorm) porque tem de ser
    SEMPRE o ÚLTIMO elo — inclusive DEPOIS de mixar a música, pra o volume final valer no bus todo."""
    nivel = os.environ.get("ROTEIRO_AUDIO_NIVEL", "media").strip().lower()
    if nivel in ("off", "0", "none", "nao", "não"):
        return ""
    return _AUDIO_LIMPEZA.get(nivel, _AUDIO_LIMPEZA["media"])


def _audio_loudnorm():
    """Estágio final de VOLUME (loudnorm I=-14:TP=-1.5:LRA=11) — o "define o volume final".
    '' quando NIVEL=off (off desliga TODO o tratamento: limpeza + volume, como antes)."""
    if not _audio_limpeza():
        return ""
    lufs = os.environ.get("ROTEIRO_AUDIO_LUFS", "-14")
    tp = os.environ.get("ROTEIRO_AUDIO_TP", "-1.5")
    return "loudnorm=I=%s:TP=%s:LRA=11" % (lufs, tp)


def _audio_filtro():
    """Cadeia -af COMPLETA da narração (limpeza + loudnorm, nessa ordem) para o caminho SEM
    música. No caminho COM música o loudnorm é aplicado à parte, DEPOIS do amix (_audio_loudnorm),
    pra o volume final valer no bus mixado. '' se desligado."""
    return ",".join(x for x in (_audio_limpeza(), _audio_loudnorm()) if x)


def _legenda_on():
    """Legenda queimada LIGADA por padrão. Desligue com ROTEIRO_LEGENDA=0/off/none/nao."""
    v = os.environ.get("ROTEIRO_LEGENDA", "1").strip().lower()
    return v not in ("0", "off", "none", "nao", "não", "no", "false")


def _legenda_cor():
    """Cor primária da legenda no formato ASS &HAABBGGRR. Default AMARELO (&H0000FFFF) — casa
    com os vídeos OFICIAIS do canal (legenda serifada amarela, não branca). Aceita apelido
    ('amarelo'/'branco'/'yellow'/'white') ou o valor ASS cru. Env: ROTEIRO_CAPTION_COLOR."""
    v = (os.environ.get("ROTEIRO_CAPTION_COLOR", "amarelo") or "amarelo").strip()
    apelidos = {"amarelo": "&H0000FFFF", "yellow": "&H0000FFFF",
                "branco": "&H00FFFFFF", "white": "&H00FFFFFF"}
    return apelidos.get(v.lower(), v)


def _legenda_fonte():
    """Família da fonte da legenda. Default SERIFADA ('Times New Roman') p/ casar com os vídeos
    oficiais (legenda em serifa de livro, não em Arial). Env: ROTEIRO_CAPTION_FONT."""
    return (os.environ.get("ROTEIRO_CAPTION_FONT", "Times New Roman") or "Times New Roman").strip()


def _fonte_legenda_arquivo(nome):
    """Acha o .ttf/.otf da família `nome` p/ copiar pro fontsdir do libass — garante que a fonte
    RESOLVA mesmo em builds de ffmpeg sem fontconfig do sistema. Cadeia: ROTEIRO_CAPTION_FONT_FILE
    explícito > assets/fonts (fontes da casa) > %WINDIR%/Fonts, casando pelo nome normalizado.
    None se não achar (aí a legenda conta com o fontconfig do sistema, como era antes)."""
    exp = (os.environ.get("ROTEIRO_CAPTION_FONT_FILE", "") or "").strip()
    if exp and Path(exp).is_file():
        return Path(exp)
    alvo = re.sub(r"[^a-z0-9]", "", (nome or "").lower())
    if not alvo:
        return None
    base = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    cands = []
    for d in (base, win):
        try:
            cands += [p for p in d.iterdir() if p.suffix.lower() in (".ttf", ".otf")]
        except OSError:
            pass
    for p in cands:  # match exato do stem normalizado
        if re.sub(r"[^a-z0-9]", "", p.stem.lower()) == alvo:
            return p
    for p in cands:  # match por prefixo ("Times New Roman" -> times.ttf)
        st = re.sub(r"[^a-z0-9]", "", p.stem.lower())
        if st and (alvo.startswith(st) or st.startswith(alvo)):
            return p
    return None


def _legenda_style(w, h):
    """force_style libass. Fontsize/MarginV em px do vídeo (o .ass usa a resolução do vídeo como
    PlayRes — ver _preparar_ass/_forcar_playres). MarginV alto = a legenda no terço inferior.
    Default = SERIFA AMARELA (padrão dos vídeos oficiais); tudo sobrescrevível por env."""
    # Coefs calibrados na legenda dos vídeos OFICIAIS (medida em 1280x720: x-height ~26px,
    # centro em ~0.86 da altura → fontsize ~44/720 ≈ 0.060·h; margem ~0.14·h coloca o centro em ~0.86).
    fs = os.environ.get("ROTEIRO_CAPTION_FONTSIZE", str(max(40, int(h * 0.060))))  # ~65 em 1080
    mv = os.environ.get("ROTEIRO_CAPTION_MARGINV", str(int(h * 0.14)))             # ~151 em 1080
    outline = os.environ.get("ROTEIRO_CAPTION_OUTLINE", "2")
    shadow = os.environ.get("ROTEIRO_CAPTION_SHADOW", "1")
    return ("FontName=%s,Fontsize=%s,Bold=1,PrimaryColour=%s,"
            "OutlineColour=&H00000000,BackColour=&H64000000,BorderStyle=1,Outline=%s,"
            "Shadow=%s,Alignment=2,MarginV=%s"
            % (_legenda_fonte(), fs, _legenda_cor(), outline, shadow, mv))


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


def _drop_dialogos_em_ranges(ass_path, ranges, log):
    """Remove do .ass os eventos Dialogue cujo INÍCIO cai dentro de qualquer (ini, fim) de
    `ranges` (em s, na timeline do vídeo MONTADO). Usado p/ NÃO queimar a legenda re-transcrita
    por cima das PEÇAS FIXAS de detalhe (tutorial, intros, aviso de clone), que já trazem legenda
    própria embutida — decisão da editora (2026-07-09). Devolve quantos blocos foram removidos."""
    if not ranges:
        return 0
    from ffmpeg_montagem import _t2s
    txt = ass_path.read_text(encoding="utf-8", errors="replace")
    out, removidos = [], 0
    for linha in txt.splitlines():
        if linha.startswith("Dialogue:"):
            campos = linha.split(",", 9)
            if len(campos) == 10:
                try:
                    ini = _t2s(campos[1])
                except (ValueError, IndexError):
                    ini = None
                # tolerância de 50ms na fronteira (arredondamento de frames no concat)
                if ini is not None and any(a - 0.05 <= ini < b for (a, b) in ranges):
                    removidos += 1
                    continue
        out.append(linha)
    if removidos:
        ass_path.write_text("\n".join(out), encoding="utf-8")
    return removidos


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


def _sync_guard_on():
    """Trava anti-dessincronia: falha alto quando NENHUMA âncora casa (áudio de outro draft).
    ROTEIRO_SYNC_GUARD=0 desliga (renderiza mesmo descasado)."""
    v = os.environ.get("ROTEIRO_SYNC_GUARD", "1").strip().lower()
    return v not in ("0", "off", "nao", "não", "no", "false")


def _seg_fresco(seg, *fontes):
    """True se `seg` existe, não está vazio E é MAIS NOVO que todas as `fontes` (arquivos de
    áudio de que ele foi fatiado/muxado). Fecha o furo do card 256: a montagem é idempotente por
    segmento (reusa out/seg_*.mp4 pelo tamanho), então quando o narration.mp3 é regerado em INGLÊS
    mas os seg_*_corpo/teaser/capa foram assados do áudio PT antigo, a remontagem reusava o PT.
    Comparar mtime força o rebuild do segmento cuja fonte de áudio ficou mais nova. Fontes
    inexistentes são ignoradas (ex.: capa sem titulo narrado). ROTEIRO_SEG_STALE_TOL = folga (s)."""
    try:
        if not (seg.exists() and seg.stat().st_size > 0):
            return False
        tol = float(os.environ.get("ROTEIRO_SEG_STALE_TOL", "2"))
        smt = seg.stat().st_mtime
        for f in fontes:
            if f is not None and f.exists() and smt + tol < f.stat().st_mtime:
                return False
        return True
    except (OSError, ValueError):
        return False


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
    # a capa entra. Usa a MESMA timeline palavra->tempo do teaser (`_word_timeline`, precisão
    # sub-cue) + casamento tolerante a erro do Whisper (`_palavra_igual`: prefixo + Levenshtein<=1),
    # NÃO mais `blob.find` exato. Robusto a: (a) espaço duplo do Whisper, (b) a frase ATRAVESSAR
    # duas cues, e (c) o Whisper errar palavras no meio ("Caracciolo"->"Caracolo", "fiancé"->
    # "fiance", "Vecchia"->"Vichia") — casa com >=~60% das 7 primeiras palavras batendo, onde o
    # `blob.find` (que exige um trecho contíguo PERFEITO de 4-6 palavras) perdia a âncora e o
    # capítulo caía no fallback proporcional. Monotônico: cada cap casa só DEPOIS do anterior
    # (`_desde` avança) -> a fronteira nunca "volta no tempo".
    tl = _word_timeline(cues, total + 5.0) if cues else []

    def _casar(frase, desde):
        """(índice_p/_avançar, tempo_de_início_do_capítulo) da frase-âncora buscando a partir de
        `desde`, ou None. DESLIZA uma janela de até 7 palavras sobre a âncora INTEIRA (não só as 7
        primeiras) e casa a 1ª janela que bater >=~60% (tolerante a erro do Whisper via
        `_palavra_igual`). A abertura (offset 0) é preferida; se ela for curta/genérica e não casar,
        uma janela mais adiante — o trecho DISTINTIVO da frase (ex.: "...library since before dawn")
        — ancora, e o offset dela é DESCONTADO p/ voltar ao início real do capítulo. Fecha o furo do
        card 250 P2 cap 4 ("I knew she'd come down." não ancorava sozinha → capa entrava ~5 min tarde)."""
        palavras = re.sub(r"[^a-z0-9]+", " ", (frase or "").lower()).split()[:16]
        if not palavras or not tl:
            return None
        for off in range(0, max(1, len(palavras) - 3)):
            chave = palavras[off:off + 7]
            if len(chave) < 4:
                break
            alvo = max(3, (len(chave) * 3 + 2) // 5)  # ~60% das palavras
            # a janela aparece `off` palavras DEPOIS do início do capítulo -> busca desde+off.
            melhor_j, melhor_sc = None, 0
            for j in range(desde + off, len(tl)):
                sc = sum(1 for p in range(len(chave))
                         if j + p < len(tl) and _palavra_igual(chave[p], tl[j + p][0]))
                if sc > melhor_sc:
                    melhor_sc, melhor_j = sc, j
                    if sc == len(chave):
                        break
            if melhor_j is not None and melhor_sc >= alvo:
                j_cap = max(desde, melhor_j - off)   # volta ao INÍCIO do capítulo
                # avança a busca do PRÓXIMO capítulo p/ depois da janela casada (não re-casa aqui).
                return melhor_j + len(chave), tl[j_cap][1]
        return None

    starts, _desde = {}, 0
    for c in caps:
        r = _casar(c.get("primeira_frase", ""), _desde)
        if r is not None:
            j, t = r
            starts[c["n"]] = t
            _desde = j + 1

    # GUARD anti-dessincronia: se NENHUM capítulo (de 2+) casou com a narração, quase sempre o
    # narration.mp3 é de OUTRO draft do roteiro — a Etapa 3 é idempotente e reusou o áudio velho
    # (o roteiro foi regerado DEPOIS do TTS). Renderizar assim = 78 min de vídeo descasado, com
    # todas as fatias de áudio caindo no fallback proporcional. Falha alto em vez de gerar calado.
    if _sync_guard_on() and len(caps) >= 2 and not starts:
        raise ErroPipeline(
            "Sincronia: NENHUM dos %d capítulos casou com a narração (narration.srt). Quase sempre "
            "o narration.mp3 é de OUTRO draft do roteiro (a Etapa 3 pula o TTS quando o mp3 já "
            "existe e reusa o áudio velho). CORREÇÃO: apague na pasta do projeto o narration.mp3, "
            "narration_raw.mp3, .pausas_otimizadas e narration.srt, e rode a Etapa 3 de novo (gera "
            "o TTS do texto atual). Para renderizar mesmo descasado: ROTEIRO_SYNC_GUARD=0." % len(caps))

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


def _audio_titulo(ff, src, dur, out, log):
    """Áudio da capa = a fala do título (src, covers/titulo_NN.mp3) no COMEÇO + silêncio até
    `dur` (AAC uniforme). A narradora anuncia 'Chapter N — Título' enquanto a capa está na
    tela; o respiro no fim (dur > fala) vira a pausa dramática antes do capítulo. `-t dur`
    fixa a duração no comprimento do vídeo da capa (mux casa exato)."""
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
           "-af", "apad", "-t", "%.3f" % max(0.05, dur), *_AENC, str(out)]
    _run(cmd, log, "audio-titulo")
    return out.exists() and out.stat().st_size > 0


# ---------------------------------------------------------------------------
# Blocos de vídeo (mudos) por segmento
# ---------------------------------------------------------------------------

# Presets de Ken Burns (zdir, fx0,fx1, fy0,fy1) — movimento CONTÍNUO por toda a duração da imagem
# (nunca congela) + variedade por imagem. zdir: +1 zoom-in / -1 zoom-out. fx/fy ∈ [-1,1] = fração
# do espaço livre do crop (0=centro, ±1=borda), depois escalada por PAN_FRAC (_kb_amp): x/y escalam
# pelo próprio (1-1/zoom), então o pan NUNCA abre borda preta, em QUALQUER zoom. A AMPLITUDE do zoom
# e o ALCANCE do pan vêm de _kb_amp() (env-tunáveis). Port da esteira long-form (ffmpeg_montagem.py):
# ease-in-out + amplitude forte + tmix, que MATA a tremedeira do zoompan — ver decisoes-changelog 2026-07-09.
_KB_MODES = (
    ( 1,  0.0,  0.0,  0.0,  0.0),   # zoom-in central
    ( 1, -1.0,  1.0,  0.0,  0.0),   # zoom-in + pan esquerda -> direita
    (-1,  1.0, -1.0,  0.0,  0.0),   # zoom-out + pan direita -> esquerda
    ( 1,  0.0,  0.0, -1.0,  1.0),   # zoom-in + pan cima -> baixo
    ( 1, -0.8,  0.8,  0.8, -0.8),   # zoom-in diagonal
)


def _kb_amp():
    """Amplitude do Ken Burns: (ZOOM_AMP, PAN_FRAC). Espelha os defaults da long-form
    (zoom 1.0->1.28, pan 0.90 da margem livre) — a usuária pediu p/ SENTIR o movimento.
    Env: ROTEIRO_KENBURNS_ZOOM/_PAN (aceita tb os nomes LONGFORM_KENBURNS_ZOOM/_PAN)."""
    def _f(k, alt, d):
        try:
            return float(os.environ.get(k, os.environ.get(alt, d)))
        except (TypeError, ValueError):
            return d
    z = _f("ROTEIRO_KENBURNS_ZOOM", "LONGFORM_KENBURNS_ZOOM", 0.28)
    p = _f("ROTEIRO_KENBURNS_PAN", "LONGFORM_KENBURNS_PAN", 0.90)
    return max(0.0, z), max(0.0, min(1.0, p))


def _kb_expr(mode, frames):
    """Expressões zoompan (z, x, y) p/ um preset, com movimento CONTÍNUO e EASE-IN-OUT
    (smoothstep p²(3-2p)) — acelera/desacelera nas pontas, dando a sensação cinematográfica da
    long-form (a câmera "respira" em vez de deslizar linear). Amplitude/alcance via _kb_amp().
    x/y ficam sempre dentro dos limites (o pan escala pela margem do próprio zoom → sem borda preta)."""
    zdir, fx0, fx1, fy0, fy1 = mode
    zamp, pfrac = _kb_amp()
    d = max(1, frames - 1)
    p = "(on/%d)" % d
    e = "(%s*%s*(3-2*%s))" % (p, p, p)                       # smoothstep 0->1
    if zdir >= 0:
        z = "1.0+(%.4f)*%s" % (zamp, e)                      # zoom-in  1.0 -> 1+amp
    else:
        z = "1.0+(%.4f)-(%.4f)*%s" % (zamp, zamp, e)         # zoom-out 1+amp -> 1.0
    # pan fx/fy: de fx0->fx1 (fração [-1,1] da margem livre) com a MESMA curva ease, escalado por PAN_FRAC.
    fx = "((%.3f)+(%.3f)*%s)*%.3f" % (fx0, fx1 - fx0, e, pfrac)
    fy = "((%.3f)+(%.3f)*%s)*%.3f" % (fy0, fy1 - fy0, e, pfrac)
    # centro do crop = iw/2 - iw/zoom/2; deslocamento = fração fx do espaço livre (= o próprio centro).
    x = "(1+%s)*(iw/2-iw/zoom/2)" % fx
    y = "(1+%s)*(ih/2-ih/zoom/2)" % fy
    return z, x, y


def _kb_supersample(w, h):
    """Dimensões do canvas que o zoompan lê ANTES do zoom. É o ANTI-TREMIDO nº1: o zoompan arredonda
    o pan/zoom p/ pixel INTEIRO do quadro interno; o "pulo" residual em px de saída = 1/supersample
    (1.25x→~0,8px = tremido VISÍVEL; 2x→0,5px; 4x→0,25px). Port da long-form: default 2x +, junto do
    tmix (_kb_motionblur), fica TÃO liso quanto 4x a ~metade do custo. Sobe até 4x se a máquina aguentar.
    Env: ROTEIRO_KB_SUPERSAMPLE (aceita tb LONGFORM_FFMPEG_UPSCALE; 1.0 = sem, o mais rápido/trêmulo)."""
    try:
        ss = float(os.environ.get("ROTEIRO_KB_SUPERSAMPLE",
                                  os.environ.get("LONGFORM_FFMPEG_UPSCALE", "2.0")))
    except (TypeError, ValueError):
        ss = 2.0
    ss = max(1.0, min(4.0, ss))
    return (int(round(w * ss)) // 2) * 2, (int(round(h * ss)) // 2) * 2  # pares (yuv420p)


def _kb_motionblur(fps):
    """Frames de motion-blur temporal (tmix) que FUNDEM a tremedeira (judder) do arredondamento do
    zoompan a fps baixo — port do _motionblur da long-form. A média é no quadro de SAÍDA (w×h), então
    o custo é ~zero. Default: 3 em fps<=30 (passo/frame maior → precisa fundir mais), 2 acima.
    Desliga com ROTEIRO_MOTIONBLUR=0/1 (aceita tb LONGFORM_MOTIONBLUR); 2/3 = intensidade."""
    env = os.environ.get("ROTEIRO_MOTIONBLUR", os.environ.get("LONGFORM_MOTIONBLUR"))
    if env is not None:
        try:
            return max(1, int(float(env)))
        except (TypeError, ValueError):
            pass
    return 3 if fps <= 30 else 2


def _kb_fade_frames():
    """Fade-através-do-preto (frames) em cada troca de imagem do corpo. Default 0 (OFF): no curto as
    imagens trocam a cada ~3-4s e o fade a cada troca piscaria demais. Ligue com ROTEIRO_KB_FADE=12
    (o FADE da long-form) se quiser a transição cinematográfica entre as imagens do corpo."""
    try:
        return max(0, int(float(os.environ.get("ROTEIRO_KB_FADE", "0"))))
    except (TypeError, ValueError):
        return 0


def _corpo_paralelo():
    """Quantos CORPOS (Ken Burns) renderizar AO MESMO TEMPO. O `zoompan` do FFmpeg é single-thread,
    então em fila cada render usa só ~2-3 núcleos e sobra CPU ociosa — o gargalo da montagem. Rodar
    N corpos em paralelo ocupa os núcleos livres SEM mudar o output (cada capítulo é a MESMA chamada
    _kenburns_imagens; muda só o agendamento). Teto 4 por causa do limite de sessões NVENC
    simultâneas em placas consumer (driver antigo trava em 3). 1 = sequencial (comportamento antigo).
    Env: ROTEIRO_CORPO_PARALELO (default 3)."""
    try:
        v = int(float(os.environ.get("ROTEIRO_CORPO_PARALELO", "3")))
    except (TypeError, ValueError):
        v = 3
    return max(1, min(4, v))


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
    sw, sh = _kb_supersample(w, h)
    mb = _kb_motionblur(fps)
    fade = _kb_fade_frames()
    inputs, filtros, labels = [], [], []
    for k, img in enumerate(imgs):
        # -framerate fps + -t seg alimenta EXATAMENTE seg*fps frames; zoompan d=1 emite 1 frame de
        # saída por frame de entrada, e `on` (0..frames-1) dá a progressão. O movimento é função de
        # `on` (NÃO acumulador com teto), então nunca congela. Super-amostra (sw×sh, lanczos) p/ não
        # serrilhar e p/ o zoompan arredondar numa grade fina (anti-tremido). tmix funde o judder.
        z, x, y = _kb_expr(_KB_MODES[k % len(_KB_MODES)], frames)
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", "%.3f" % seg, "-i", str(img)]
        chain = [
            "scale=%d:%d:force_original_aspect_ratio=increase:flags=lanczos" % (sw, sh),
            "crop=%d:%d" % (sw, sh),
            "zoompan=z='%s':x='%s':y='%s':d=1:s=%dx%d:fps=%d" % (z, x, y, w, h, fps),
        ]
        if fade and frames > 2 * fade:
            st_out = (frames - fade) / float(fps)
            chain.append("fade=t=in:st=0:d=%.4f:color=black" % (fade / float(fps)))
            chain.append("fade=t=out:st=%.4f:d=%.4f:color=black" % (st_out, fade / float(fps)))
        if mb > 1:
            chain.append("tmix=frames=%d" % mb)   # motion-blur temporal: funde a tremida do zoompan
        chain.append("setsar=1")
        filtros.append("[%d:v]%s[v%d]" % (k, ",".join(chain), k))
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


def _teaser_min_corte():
    """Duração mínima (s) de um corte do teaser. Frases muito curtas do gancho ("Wanted.",
    "Dangerous.") são fundidas com a seguinte até atingir esse piso — evita flashes subliminais
    de clipe. Env ROTEIRO_TEASER_MIN_CORTE (default 0.8s; 0 = não funde, corta em toda frase)."""
    try:
        return max(0.0, float(os.environ.get("ROTEIRO_TEASER_MIN_CORTE", "0.8")))
    except (TypeError, ValueError):
        return 0.8


def _agrupar_por_min(beats, min_dur):
    """Funde frases (beats) CONSECUTIVAS até cada grupo durar >= min_dur, preservando as fronteiras
    de frase (nunca corta no meio de uma). Devolve [(ini, fim), ...]. min_dur<=0 => frases intactas."""
    if not beats:
        return []
    if min_dur <= 0:
        return list(beats)
    grupos = []
    ini_g, fim_g = beats[0]
    for (a, b) in beats[1:]:
        if (fim_g - ini_g) < min_dur:
            fim_g = b               # grupo ainda curto: anexa a próxima frase
        else:
            grupos.append((ini_g, fim_g))
            ini_g, fim_g = a, b
    grupos.append((ini_g, fim_g))
    if len(grupos) >= 2 and (grupos[-1][1] - grupos[-1][0]) < min_dur:
        grupos[-2:] = [(grupos[-2][0], grupos[-1][1])]   # último curto -> funde no anterior
    return grupos


def _teaser_sync_off():
    v = os.environ.get("ROTEIRO_TEASER_SYNC", "1").strip().lower()
    return v in ("0", "off", "none", "nao", "não", "no", "false")


def _teaser_plano(proj, ff, teaser_clips, hook_dur, log):
    """Plano de montagem do teaser: (clips, duracoes, motivo), UM item por FRASE do gancho.

    Cada corte cai numa fronteira de frase falada (sincronia com o texto do teaser do roteiro) e os
    clipes disponíveis são CICLADOS — quando há menos clipes que frases eles se DUPLICAM (clipe
    1,2,…,N,1,2,…) até o gancho terminar (pedido da editora: "se a quantidade de vídeos for
    insuficiente, duplica eles até terminar o teaser"). Frases curtas demais são fundidas (ver
    _teaser_min_corte). Cai na divisão igual (um clipe por clipe) se o sync estiver off ou o gancho
    não mapear na SRT."""
    n = len(teaser_clips)
    igual = (list(teaser_clips), [hook_dur / n] * n)
    if _teaser_sync_off():
        return (*igual, "divisão igual (ROTEIRO_TEASER_SYNC=0)")
    beats = None
    try:
        beats = _hook_beats(proj, ff, hook_dur, log)
    except Exception as e:
        log("    ⚠ teaser: falha ao mapear as frases do gancho (%s) — divisão igual." % e)
    if not beats:
        return (*igual, "divisão igual (gancho não mapeado na SRT)")
    grupos = _agrupar_por_min(beats, _teaser_min_corte())
    clips_out = [teaser_clips[i % n] for i in range(len(grupos))]   # cicla/duplica os clipes
    durs_out = [max(0.2, b - a) for (a, b) in grupos]
    sobra = ""
    if len(grupos) < n:
        sobra = (" — ⚠ %d clipe(s) sem uso (o gancho tem só %d corte(s); corte-o em mais frases p/ usar todos)"
                 % (n - len(grupos), len(grupos)))
    voltas = "sem duplicar" if len(grupos) <= n else "ciclados %.1fx" % (len(grupos) / n)
    motivo = ("sincronizado às frases do gancho (%d frase(s) → %d corte(s); %d clipe(s) %s)%s"
              % (len(beats), len(grupos), n, voltas, sobra))
    return clips_out, durs_out, motivo


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
    (cortes alinhados aos beats do gancho — ver _teaser_plano). Cada clipe é enquadrado em w×h."""
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
    if not (out.exists() and out.stat().st_size > 0):
        return False
    # Garantia anti-buraco: o teaser TEM que cobrir a duração-alvo inteira. Se algum clipe
    # falhou ao renderizar (fatia perdida) e o concat ficou curto, duplica o teaser inteiro
    # (stream_loop) até fechar o alvo — evita tela preta/congelada no fim da render final.
    alvo = sum(duracoes)
    atual = _dur(ff, out)
    if alvo > 0 and atual < alvo - 0.1:
        loop = tmp / (out.stem + "_loopfill.mp4")
        _run([ff, "-y", "-hide_banner", "-loglevel", "error",
              "-stream_loop", "-1", "-t", "%.3f" % alvo, "-i", str(out),
              *_venc_args(fps), "-an", str(loop)], log, "teaser-loop-fill")
        if loop.exists() and loop.stat().st_size > 0:
            out.unlink(missing_ok=True)
            loop.replace(out)
            log("    teaser: concat ficou curto (%.1fs < %.1fs alvo) — duplicado até fechar o teaser."
                % (atual, alvo))
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
# Remoção da marca d'água "Veo" dos clipes de teaser (Google Veo carimba o canto)
# ---------------------------------------------------------------------------
# Os clipes do teaser (projects/<slug>/teaser/) vêm do Google Veo com a marca "Veo" no canto
# inferior direito. Os vídeos OFICIAIS não têm essa marca. Como esses clipes reaparecem no
# teaser, no resumo P2, no CTA e no card "Part 2" do último minuto, limpamos UMA vez no começo
# da montagem (delogo, que preserva o enquadramento — sem zoom) e passamos os clipes LIMPOS pra
# todos os consumidores. Idempotente (cache em out/_teaser_clean/). Desliga com ROTEIRO_DEVEO=0.

def _deveo_on():
    return os.environ.get("ROTEIRO_DEVEO", "1").strip().lower() not in (
        "0", "off", "none", "nao", "não", "no", "false")


def _probe_dim(ff, src):
    """(w, h) do 1º stream de vídeo de `src`, ou (0, 0) se não der pra ler."""
    ffprobe = str(Path(ff).with_name("ffprobe" + Path(ff).suffix))
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v", "-show_entries",
                            "stream=width,height", "-of", "csv=s=x:p=0", str(src)],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **SUBPROCESS_FLAGS)
        w, h = (r.stdout or b"").decode("utf-8", "replace").strip().split("x")[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


def _deveo_box(cw, ch):
    """Box (x, y, w, h) do delogo sobre a marca 'Veo' (canto inf. dir.), em px do clipe. As frações
    foram calibradas na marca real do Veo (medida em 1280x720: ~x1238-1272, y685-705) e ESCALAM com
    a resolução do clipe. Override total por ROTEIRO_DEVEO_BOX=x:y:w:h (px do clipe)."""
    env = (os.environ.get("ROTEIRO_DEVEO_BOX", "") or "").strip()
    if env.count(":") == 3:
        try:
            x, y, w, h = (int(float(v)) for v in env.split(":"))
            return x, y, w, h
        except ValueError:
            pass
    bw = max(24, int(cw * 0.040))      # ~51 em 1280, ~77 em 1920
    bh = max(16, int(ch * 0.050))      # ~36 em 720,  ~54 em 1080
    rm = max(3, int(cw * 0.004))       # margem à borda direita
    bm = max(3, int(ch * 0.012))       # margem à borda inferior
    return cw - rm - bw, ch - bm - bh, bw, bh


def _limpar_marca(ff, src, out, fps, log):
    """Aplica delogo sobre a marca 'Veo' de `src` -> `out` (mesma resolução; só re-encoda o vídeo,
    -c:a copy). O box é calculado na resolução NATIVA do clipe. True em sucesso."""
    cw, ch = _probe_dim(ff, src)
    if cw <= 0 or ch <= 0:
        cw, ch = 1280, 720
    x, y, bw, bh = _deveo_box(cw, ch)
    x = max(1, min(x, cw - bw - 1))    # dentro do frame, com 1px de borda p/ o delogo interpolar
    y = max(1, min(y, ch - bh - 1))
    vf = "delogo=x=%d:y=%d:w=%d:h=%d" % (x, y, bw, bh)
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
          "-vf", vf, *_venc_args(fps), "-c:a", "copy", str(out)], log, "deveo")
    return out.exists() and out.stat().st_size > 0


def _teaser_limpo(ff, clips, fps, tmp, log):
    """Devolve os clipes de teaser SEM a marca 'Veo' (cópias em out/_teaser_clean/), idempotente.
    ROTEIRO_DEVEO=0 ou lista vazia -> devolve os originais intactos. Falha num clipe -> usa o
    original daquele (não derruba a montagem)."""
    if not clips or not _deveo_on():
        return clips
    d = tmp / "_teaser_clean"
    d.mkdir(parents=True, exist_ok=True)
    out, limpos = [], 0
    for c in clips:
        dst = d / ("clean_" + Path(c).name)
        if dst.exists() and dst.stat().st_size > 0:
            out.append(dst); limpos += 1; continue
        if _limpar_marca(ff, c, dst, fps, log):
            out.append(dst); limpos += 1
        else:
            out.append(Path(c))
    log("    marca 'Veo' removida (delogo) de %d/%d clipe(s) de teaser." % (limpos, len(clips)))
    return out


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


def _legendar(proj, ff, video_montado, tmp, w, h, fps, log, qr_img=None, qr_frag=None,
              mudos_ranges=None):
    """Queima a legenda no vídeo MONTADO. Como a montagem insere capas (silêncio) entre os
    capítulos, a narration.srt original NÃO casa — então RE-TRANSCREVE o áudio já montado
    (Whisper) e a legenda fica perfeitamente sincronizada. Não-fatal (devolve None em falha).

    Se `qr_img`+`qr_frag` vierem preenchidos (isca P1 com QR), o QR é sobreposto NA MESMA
    passada de encode — evita reencodar o vídeo inteiro uma 2ª vez só pro QR.

    `mudos_ranges` (lista de (ini, fim) em s, timeline do vídeo montado): trechos onde a legenda
    NÃO deve ser queimada — as PEÇAS FIXAS de detalhe (tutorial, intros, aviso de clone) já trazem
    legenda própria embutida, então os blocos re-transcritos que caem nesses intervalos são
    descartados (decisão da editora 2026-07-09), evitando legenda dupla.

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
    # Peças com legenda própria (tutorial/intros/aviso de clone): dropa os blocos re-transcritos
    # que caem no intervalo delas — a legenda embutida do clipe fica, sem legenda dupla por cima.
    if mudos_ranges:
        n_rm = _drop_dialogos_em_ranges(ass, mudos_ranges, log)
        if n_rm:
            log("    legenda: %d bloco(s) suprimido(s) nas peças de legenda própria "
                "(tutorial/intros/aviso de clone)." % n_rm)
    # fontsdir: copia o .ttf da fonte da legenda pra uma subpasta e aponta o libass PRA ELA —
    # garante que a serifa (Times New Roman etc.) resolva mesmo em builds sem fontconfig do sistema.
    fontsdir_frag = ""
    try:
        _ff_file = _fonte_legenda_arquivo(_legenda_fonte())
        if _ff_file:
            _fdir = tmp / "_fonts"
            _fdir.mkdir(exist_ok=True)
            _dest = _fdir / _ff_file.name
            if not _dest.exists():
                shutil.copyfile(_ff_file, _dest)
            fontsdir_frag = ":fontsdir=_fonts"
    except Exception as e:
        log("    ⚠ fonte da legenda não pôde ser preparada (%s) — usando fontconfig do sistema." % e)
    sub_f = "subtitles=%s%s:force_style='%s'" % (ass.name, fontsdir_frag, style)
    if qr_img is not None and qr_frag is not None:
        # Legenda + QR na MESMA passada: [0:v]->subtitles->[s]; [1:v]->QR->[qr]; overlay.
        prep, qx, qy = qr_frag  # prep produz o label [qr] a partir do input 1
        fc = "[0:v]%s[s];%s;[s][qr]overlay=%s:%s:format=auto[v]" % (sub_f, prep, qx, qy)
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", video_montado.name,
               "-i", str(qr_img), "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
               *_venc_args(fps), "-c:a", "copy", out.name]
        _run(cmd, log, "legenda+qr", cwd=tmp)
    else:
        _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", video_montado.name,
              "-vf", sub_f, *_venc_args(fps), "-c:a", "copy", out.name], log, "legenda", cwd=tmp)
    narr.unlink(missing_ok=True)
    if out.exists() and out.stat().st_size > 0:
        extra = " + QR (mesma passada)" if (qr_img is not None and qr_frag is not None) else ""
        log("    ✓ legenda queimada (uma linha ≤%d, estilo vertical, MarginV alto)%s." % (maximo, extra))
        return out
    log("    ⚠ falha ao queimar a legenda%s — seguindo sem." % (" + QR" if qr_img else ""))
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

    # Resolve o encoder UMA vez (loga qual máquina caiu em quê). 'auto' detecta o melhor de
    # hardware; um valor explícito é usado direto. NVENC/QSV/AMF = 3-8x mais rápido que a CPU.
    _pedido = (os.environ.get("ROTEIRO_ENCODER", "auto").strip() or "auto")
    _enc = _encoder()
    _cpu = _enc == "libx264"
    log("  encoder de vídeo: %s%s%s" % (
        _enc,
        " (auto-detectado)" if _pedido.lower() == "auto" else "",
        "  ⚠ sem acel. de hardware — render na CPU (mais lento)" if _cpu else ""))

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
    mat_dirs = materiais_dirs(canal)      # [pasta própria] (+ herdada: Rowan/Kay ← Lena)
    modelos = ler_modelos(canal)

    def _drop_mat(sub):
        """Clipes de <sub> varrendo materiais próprios e, se vazio, os herdados (Lena)."""
        for d in mat_dirs:
            r = _drop(d, sub)
            if r:
                return r
        return []

    tmp = proj.dir / "out"
    tmp.mkdir(parents=True, exist_ok=True)

    # Clipes do teaser (POR VÍDEO, fallback por canal), JÁ SEM a marca 'Veo' — limpos UMA vez e
    # reusados no teaser, resumo P2, CTA e último minuto. Memoizado (a limpeza é cara/idempotente).
    _teaser_cache = {}

    def _teaser_clips():
        if "v" not in _teaser_cache:
            crus = _drop(proj.dir, "teaser") or _drop_mat("teaser")
            _teaser_cache["v"] = _teaser_limpo(ff, crus, fps, tmp, log)
        return _teaser_cache["v"]

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
    detalhe_segs = set()  # peças fixas (tutorial/intros/aviso de clone) — sem legenda queimada

    def _seg_path(nome):
        return tmp / ("seg_%s.mp4" % nome)

    def _seg_detalhe(nome, key):
        """Segmento de uma PEÇA FIXA de detalhe do canal (intro pós-teaser, INTRO BOOK 02,
        AVISO DE CLONE, TUTORIAL PLATAFORMA). Acha o clipe pronto do canal (detalhes.achar:
        por-vídeo > canal > herdado), enquadra em w×h e MANTÉM o áudio próprio do clipe (são
        vídeos finalizados). None se a peça não existe — o rabo só encolhe (sem drop-in genérico).
        Idempotente por segmento (reaproveita o seg_*.mp4 se mais novo que o clipe-fonte)."""
        import detalhes
        alvo = detalhes.achar(proj, mat_dirs, key)
        if not alvo:
            return None
        s = _seg_path(nome)
        detalhe_segs.add(s)  # peça de legenda própria: não recebe legenda queimada por cima
        if _seg_fresco(s, alvo):
            return s
        if _tem_audio(ff, alvo):
            _video_ajustado(ff, alvo, None, w, h, fps, s, log, mudo=False)
        else:
            vmudo = tmp / ("_v_%s.mp4" % nome)
            _video_ajustado(ff, alvo, None, w, h, fps, vmudo, log, mudo=True)
            aud = tmp / ("_a_%s.m4a" % nome)
            _silencio(ff, _dur(ff, vmudo), aud, log)
            _mux(ff, vmudo, aud, s, log)
            vmudo.unlink(missing_ok=True); aud.unlink(missing_ok=True)
        if s.exists() and s.stat().st_size > 0:
            log("    detalhe '%s': %s (%.1fs)." % (key, alvo.name, _dur(ff, s)))
            return s
        return None

    # --- 1) TEASER (gancho) — SÓ na isca (P1). A extensão (P2) começa direto no cap 1. -----
    if extensao:
        log("    P2 (extensão): sem teaser-isca — o capítulo 1 começa do 0.")
    else:
        hook_dur = max(1.0, bnds[0]) if bnds else min(8.0, total)
        # Teaser é POR VÍDEO: lê de projects/<slug>/teaser/ (isolado deste card). Só cai no
        # drop-in por canal (materiais/<canal>/teaser/) como fallback legado, se o projeto não tiver.
        # Os clipes já vêm SEM a marca 'Veo' (limpos por _teaser_clips → _teaser_limpo).
        teaser_clips = _teaser_clips()
        seg = _seg_path("00_teaser")
        if not _seg_fresco(seg, proj.narration_mp3):
            vmudo = tmp / "_v_teaser.mp4"
            if teaser_clips:
                clips_seq, durs, motivo = _teaser_plano(proj, ff, teaser_clips, hook_dur, log)
                _concat_videos_trim(ff, clips_seq, durs, w, h, fps, vmudo, log)
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

        # INTRO PÓS TEASER (peça fixa do canal): abertura entre o teaser e o cap 1. Pulada se
        # o canal não tiver o clipe. Só na isca (P1). Ver detalhes.py.
        ipt = _seg_detalhe("01_intro_pos_teaser", "intro_pos_teaser")
        if ipt:
            segmentos.append(ipt); log("    INTRO PÓS TEASER adicionada.")

    # --- 2) CAPÍTULOS ---------------------------------------------------------
    # Os corpos (Ken Burns/zoompan) são o gargalo: o zoompan é single-thread, então em fila sobra
    # CPU ociosa. Renderizamos ATÉ _corpo_paralelo() capítulos AO MESMO TEMPO — o output é idêntico
    # (a MESMA chamada _kenburns_imagens por capítulo; muda só o agendamento) e os segmentos são
    # montados DEPOIS, na ordem dos capítulos, então o paralelismo não bagunça a sequência final.
    capa_pos = _capa_pos()

    def _render_cap(idx, c):
        """Renderiza capa+corpo de UM capítulo (roda em thread própria). Devolve (idx, capa, corpo, msg).
        Todas as variáveis do capítulo (n/ini/fim/capa) são LOCAIS — não há captura tardia de loop."""
        if cancel is not None and cancel.is_set():
            return (idx, None, None, None)
        n = c["n"]
        # Na extensão (P2) não há teaser, então o 1º capítulo começa em 0 (não perde a narração
        # do trecho que, na isca, iria por baixo do teaser).
        ini = 0.0 if (extensao and idx == 0) else bnds[idx]
        fim = bnds[idx + 1] if idx + 1 < len(bnds) else None
        capa = proj.covers_dir / ("capa_%02d.mp4" % (idx + 1))

        def _seg_capa():
            s = _seg_path("%02d_capa" % n)
            titulo_mp3 = proj.covers_dir / ("titulo_%02d.mp3" % n)
            if _seg_fresco(s, titulo_mp3):
                return s
            if not (capa.exists() and capa.stat().st_size > 0):
                return None
            vmudo = tmp / ("_v_capa_%02d.mp4" % n)
            _video_ajustado(ff, capa, None, w, h, fps, vmudo, log, mudo=True)
            dur = _dur(ff, vmudo)
            aud = tmp / ("_a_capa_%02d.m4a" % n)
            # A capa toca a narração do título ("Chapter N — Título", Etapa 3) por baixo — a
            # duração do vídeo já foi cortada p/ essa fala + respiro (Etapa 6), então casa. Sem
            # o áudio do título (desligado / TTS falhou), a capa é a "pausa" em silêncio (legado).
            if _cover_narrar_on() and titulo_mp3.exists() and titulo_mp3.stat().st_size > 0:
                _audio_titulo(ff, titulo_mp3, dur, aud, log)
            else:
                _silencio(ff, dur, aud, log)
            _mux(ff, vmudo, aud, s, log)
            vmudo.unlink(missing_ok=True); aud.unlink(missing_ok=True)
            return s

        def _seg_corpo():
            s = _seg_path("%02d_corpo" % n)
            if _seg_fresco(s, proj.narration_mp3):
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
        msg = "    capítulo %d: %.1fs de corpo%s." % (
            n, ((fim or total) - ini), ", com capa" if capa_seg else " (sem capa)")
        return (idx, capa_seg, corpo_seg, msg)

    _nparal = _corpo_paralelo()
    _res = {}
    if _nparal > 1 and len(caps) > 1:
        log("    corpos em paralelo: até %d capítulo(s) por vez (zoompan é single-thread)." % _nparal)
        with ThreadPoolExecutor(max_workers=_nparal) as _ex:
            for _idx, _capa, _corpo, _msg in _ex.map(lambda ic: _render_cap(*ic),
                                                     list(enumerate(caps))):
                _res[_idx] = (_capa, _corpo, _msg)
    else:
        for _idx, _c in enumerate(caps):
            if cancel is not None and cancel.is_set():
                raise ErroPipeline("Cancelado pelo usuário.")
            _i, _capa, _corpo, _msg = _render_cap(_idx, _c)
            _res[_i] = (_capa, _corpo, _msg)

    if cancel is not None and cancel.is_set():
        raise ErroPipeline("Cancelado pelo usuário.")

    # Monta os segmentos NA ORDEM dos capítulos — o paralelismo mexe só no tempo de render, não na
    # sequência. As mensagens por-capítulo também saem aqui, em ordem (durante o render elas se
    # intercalariam por serem concorrentes).
    for _idx in range(len(caps)):
        capa_seg, corpo_seg, _msg = _res[_idx]
        ordem = [capa_seg, corpo_seg] if capa_pos == "antes" else [corpo_seg, capa_seg]
        for s in ordem:
            if s is not None:
                segmentos.append(s)
        if _msg:
            log(_msg)

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
            clipes = _drop_mat(subpasta)
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
        log("    P2 (extensão): sem rabo de isca (só capas + corpo).")
    else:
        # RABO DA ISCA (P1) — ordem fixa (decisão da editora 2026-07-09):
        #   INTRO BOOK 02 → RESUMO → CTA → AVISO DE CLONE → TUTORIAL PLATAFORMA → MINUTO FINAL
        # PEÇAS FIXAS por canal (clipes prontos, áudio próprio, pulam se faltarem): INTRO BOOK 02,
        # AVISO DE CLONE, TUTORIAL PLATAFORMA (ver detalhes.py). PEÇAS GERADAS (mantêm o template,
        # trocam só os takes pelos do teaser, duplicando se faltar clipe): RESUMO e CTA
        # (resumo_cta.py) e MINUTO FINAL (ultimo_minuto.py). Só a ORDEM/posição mudou.
        import resumo_cta
        teaser_clips = _teaser_clips()   # já sem a marca 'Veo' (memoizado)

        # INTRO BOOK 02 (fixa) ------------------------------------------------
        book2 = _seg_detalhe("80_intro_book_02", "intro_book_02")
        if book2:
            segmentos.append(book2); log("    INTRO BOOK 02 adicionada.")

        # RESUMO (gerado: template + takes do teaser) -------------------------
        resumo = None
        try:
            resumo = resumo_cta.construir_resumo_p2(proj, base_mat, teaser_clips, w, h, fps, tmp, log, cancel)
        except Exception as e:
            log("    ⚠ resumo (geração) falhou (%s) — tentando drop-in." % e)
        if not resumo:
            resumo = _drop_seg("90_resumo", "book2", "resumo_p2", proj_stem="resumo_p2")
        if resumo:
            segmentos.append(resumo); log("    RESUMO adicionado (%s)." % resumo.name)
        else:
            log("    (sem RESUMO — sem clipes de teaser + texto no roteiro, e sem drop-in em "
                "materiais/%s/book2/)." % base_mat.name)

        # CTA (gerado: template + takes do teaser) ---------------------------
        cta = None
        try:
            cta = resumo_cta.construir_cta(proj, base_mat, modelos, teaser_clips, w, h, fps, tmp, log, cancel)
        except Exception as e:
            log("    ⚠ CTA (geração) falhou (%s) — tentando drop-in." % e)
        if not cta:
            cta = _drop_seg("91_cta", "cta", "cta_final")
        if cta:
            segmentos.append(cta); log("    CTA adicionada.")
        else:
            log("    (sem CTA — sem clipes de teaser + base cta_base.mp4 em materiais/%s/cta/, e "
                "sem drop-in/modelo cta_final)." % base_mat.name)

        # AVISO DE CLONE (fixa) ----------------------------------------------
        aviso = _seg_detalhe("92_aviso_clone", "aviso_clone")
        if aviso:
            segmentos.append(aviso); log("    AVISO DE CLONE adicionado.")

        # TUTORIAL PLATAFORMA (fixa) -----------------------------------------
        tut = _seg_detalhe("93_tutorial_plataforma", "tutorial_plataforma")
        if tut:
            segmentos.append(tut); log("    TUTORIAL PLATAFORMA adicionado.")

        # MINUTO FINAL (gerado: retenção pós-CTA — tela de comentários + card "Part 2" com os
        # clipes do teaser tocando no quadrado, ~1 min). Ver ultimo_minuto.py.
        try:
            import ultimo_minuto
            um = ultimo_minuto.construir_ultimo_minuto(
                proj, base_mat, teaser_clips, w, h, fps, tmp, log, cancel)
            if um:
                segmentos.append(um); log("    MINUTO FINAL adicionado (%s)." % um.name)
        except Exception as e:
            log("    ⚠ MINUTO FINAL (geração) falhou (%s) — seguindo sem." % e)

    # --- 4) CONCAT dos segmentos ---------------------------------------------
    segmentos = [s for s in segmentos if s and s.exists() and s.stat().st_size > 0]
    if not segmentos:
        raise ErroPipeline("Nenhum segmento montado — verifique narração/imagens.")
    concat = _concat_segmentos(ff, segmentos, tmp, fps, log)

    # Faixas (ini, fim) das PEÇAS FIXAS de detalhe na timeline concatenada — a legenda queimada
    # é SUPRIMIDA nelas (já trazem legenda própria embutida). Offset = soma das durações dos
    # segmentos na ordem; casa com a timeline do concat (a legenda é re-transcrita do concat).
    mudos_ranges = []
    if detalhe_segs:
        off = 0.0
        for s in segmentos:
            d = _dur(ff, s)
            if s in detalhe_segs:
                mudos_ranges.append((off, off + d))
            off += d
        if mudos_ranges:
            log("    legenda: %d peça(s) de legenda própria protegida(s) (sem legenda queimada)."
                % len(mudos_ranges))

    # --- 5) LEGENDA (+ QR fundido) — re-transcrita p/ casar com cortes/pausas --
    # O QR fixo da ISCA (P1) é resolvido ANTES e, quando há legenda, entra NA MESMA passada de
    # encode (poupa reencodar o vídeo inteiro só pro QR). Asset por canal:
    # projects/<slug>/qr/ > materiais/<canal>/qr/ (+ herdada) > assets/qr/. A extensão (P2) não leva QR.
    video_src = concat
    qr_img, qr_frag = None, None
    if not extensao:
        try:
            import qr_overlay
            qr_img = qr_overlay.imagem(proj, canal)
            if qr_img is not None:
                qr_frag = qr_overlay.fragmento_filtro(1, w, h)
        except Exception as e:
            log("    ⚠ QR não resolvido (%s) — seguindo sem QR." % e)
            qr_img = qr_frag = None

    legendou = False
    if _legenda_on():
        try:
            capd = _legendar(proj, ff, concat, tmp, w, h, fps, log, qr_img=qr_img, qr_frag=qr_frag,
                             mudos_ranges=mudos_ranges)
            if capd:
                video_src = capd
                legendou = True
        except Exception as e:
            log("    ⚠ legenda não aplicada (%s) — seguindo sem legenda." % e)

    # --- 5b) QR standalone: SÓ se a legenda não rodou (senão já foi fundido acima) ---
    if qr_img is not None and not legendou:
        try:
            import qr_overlay
            qrd = qr_overlay.aplicar(proj, canal, ff, video_src, tmp / "_qr.mp4",
                                     w, h, fps, log, _venc_args(fps))
            if qrd:
                video_src = qrd
        except Exception as e:
            log("    ⚠ QR não aplicado (%s) — seguindo sem QR." % e)

    # --- 6) ÁUDIO tratado (cleanup+loudnorm) + MÚSICA -> final ----------------
    musica = _musica(base_mat, modelos)
    final = proj.final_mp4
    limpeza = _audio_limpeza()   # só a limpeza da narração (loudnorm vem por último, no bus)
    ln = _audio_loudnorm()       # loudnorm final (define o volume) — aplicado depois do amix
    af = _audio_filtro()         # limpeza+loudnorm juntos (caminho SEM música)
    if musica:
        db = os.environ.get("ROTEIRO_MUSICA_DB", "-10")
        # Limpeza SÓ na narração; música entra por baixo (-10 dB); e o loudnorm é o ÚLTIMO elo,
        # aplicado ao BUS JÁ MIXADO (narração+música), pra "volume bom" (I=-14/TP=-1.5) valer no
        # arquivo FINAL. amix normalize=0 evita o corte de ~6 dB (fator 1/n) que rebaixava a
        # narração assim que a música entrava — o loudnorm no fim segura o pico (TP=-1.5), sem clip.
        # Sem loudnorm (NIVEL=off) mantém o normalize padrão pra não estourar o mix.
        narr = ("[0:a]%s[n]" % limpeza) if limpeza else "[0:a]anull[n]"
        norm = ":normalize=0" if ln else ""
        amix_out = "mx" if ln else "a"
        fc = ("%s;[1:a]volume=%sdB[m];[n][m]amix=inputs=2:duration=first:dropout_transition=0%s[%s]"
              % (narr, db, norm, amix_out))
        if ln:
            fc += ";[mx]%s[a]" % ln
        _run([ff, "-y", "-hide_banner", "-loglevel", "error",
              "-i", str(video_src), "-stream_loop", "-1", "-i", str(musica),
              "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
              "-c:v", "copy", *_AENC, str(final)], log, "musica")
        if final.exists() and final.stat().st_size > 0:
            tratado = " + narração tratada (loudnorm no bus final)" if (limpeza or ln) else ""
            log("    música de fundo (%sdB) mixada%s." % (db, tratado))
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
    (tmp / "_qr.mp4").unlink(missing_ok=True)

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
