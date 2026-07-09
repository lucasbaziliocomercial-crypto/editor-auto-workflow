# -*- coding: utf-8 -*-
"""Etapa 3 — Narração (voz do canal) + SRT + mapa de capítulos.

Porta enxuta da s4_narracao_srt do long-form, adaptada à esteira roteiro-auto:

  - VOZ ÚNICA POR CANAL: a voz primária vem de `common.voz_do_canal(canal)` (canal lido do
    source.json), não de uma env fixa. A cadeia de fallback (cool_lady, labebe — vozes CapCut
    NÃO-artista) continua absorvendo o SmartToolRateLimit da voz de artista.
  - TEXTO LIMPO: o TTS narra `roteiro_estrutura.texto_narracao(roteiro.txt)` — o gancho + a
    prosa dos capítulos, SEM as linhas de cabeçalho "Chapter N — Título" e SEM nada depois de
    "END OF PART 1". (Senão a Joanne leria "Chapter 1" e "END OF PART 1" em voz alta.)
  - MAPA DE CAPÍTULOS: grava capitulos.json (n, titulo, primeira_frase) — a montagem (Etapa 7)
    usa a `primeira_frase` como âncora pra achar o início de cada capítulo na SRT.

Reusa a mecânica testada do long-form: chunking paragraph-aware, cadeia de voz com backoff,
concat via FFmpeg, otimização de pausa (anti-robótico) e SRT via Whisper (gerar-srt-en.py).

Contrato: run(proj, log, cancel, **kw). Idempotente (âncora = narration.srt).
"""

import os
import re
import json
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

import common
from common import (ErroPipeline, WHISPER_SCRIPT, achar_audio, achar_ffmpeg,
                    SUBPROCESS_FLAGS, idioma, nome_idioma)
from runner import rodar_script
import roteiro_estrutura


# ---------------------------------------------------------------------------
# Texto de narração + mapa de capítulos (determinístico)
# ---------------------------------------------------------------------------

def preparar_texto(proj, log):
    """Escreve roteiro_tts.txt = texto de narração limpo (gancho + prosa, sem cabeçalhos) e
    capitulos.json (âncoras dos capítulos p/ a montagem). Idempotente."""
    if not proj.existe(proj.roteiro):
        raise ErroPipeline("Falta roteiro.txt (Etapa 1) para narrar.")
    bruto = proj.roteiro.read_text(encoding="utf-8", errors="replace")
    est = roteiro_estrutura.parse_roteiro(bruto)
    texto = roteiro_estrutura.texto_narracao(bruto)
    if not texto.strip():
        raise ErroPipeline("roteiro.txt não produziu texto de narração (vazio após limpeza).")

    proj.roteiro_tts.write_text(texto, encoding="utf-8")
    caps = [{"n": c["n"], "titulo": c["titulo"], "primeira_frase": c["primeira_frase"]}
            for c in est["chapters"]]
    (proj.dir / "capitulos.json").write_text(
        json.dumps({"hook_chars": len(est["hook"]), "capitulos": caps},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    log("  narração preparada: gancho %d chars + %d capítulo(s) -> roteiro_tts.txt / capitulos.json"
        % (len(est["hook"]), len(caps)))


# ---------------------------------------------------------------------------
# Voz do canal
# ---------------------------------------------------------------------------

def _voz_primaria(proj, log):
    """ID CapCut da voz do canal (lido do source.json). '' se o canal não tem id capturado."""
    canal = ""
    if proj.existe(proj.source):
        try:
            canal = (json.loads(proj.source.read_text(encoding="utf-8")).get("canal") or "").strip()
        except (OSError, ValueError):
            pass
    nome, vid = common.voz_do_canal(canal, "f")
    if not vid:
        log("  ⚠ voz '%s' do canal '%s' sem id CapCut capturado — usando a cadeia de fallback "
            "(Joanne/cool_lady)." % (nome, canal or "?"))
        vid = os.environ.get("CAPCUT_TTS_VOICE", common.VOZ_IDS.get("joanne", ""))
    else:
        log("  voz do canal '%s': %s (%s)" % (canal or "?", nome, vid))
    return vid


def _voice_chain(voz_primaria, log=None):
    """Cadeia [primária, fallbacks...] sem duplicar. Fallbacks = LONGFORM_TTS_VOICE_FALLBACK
    (default 'cool_lady,labebe' — vozes EN NÃO-artista, outro pool de quota)."""
    fb_csv = os.environ.get("LONGFORM_TTS_VOICE_FALLBACK", "cool_lady,labebe").strip()
    cadeia, vistos = [], set()
    for v in [(voz_primaria or "").strip(), *[x.strip() for x in fb_csv.split(",")]]:
        if v and v not in vistos:
            cadeia.append(v); vistos.add(v)
    if not cadeia:
        raise ErroPipeline("Etapa 3 (TTS) sem voz definida — confira o canal e LONGFORM_TTS_VOICE_FALLBACK.")
    return cadeia


# ---------------------------------------------------------------------------
# Chunking + concat (idênticos ao long-form)
# ---------------------------------------------------------------------------

def _chunizar(texto, maxlen):
    paras = [p.strip() for p in re.split(r"\n\s*\n", texto.strip()) if p.strip()]
    blocos, atual = [], ""
    for p in paras:
        pedacos = [p] if len(p) <= maxlen else re.split(r"(?<=[.!?])\s+", p)
        for ped in pedacos:
            if atual and len(atual) + len(ped) + 2 > maxlen:
                blocos.append(atual); atual = ped
            else:
                atual = (atual + "\n\n" + ped) if atual else ped
    if atual:
        blocos.append(atual)
    return blocos or [texto]


def _concat_mp3(partes, saida, log):
    ff = achar_ffmpeg()
    lista = saida.parent / "_tts_concat.txt"
    lista.write_text("".join("file '%s'\n" % p.name for p in partes), encoding="utf-8")
    cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lista),
           "-c:a", "libmp3lame", "-q:a", "2", str(saida)]
    log("    Concatenando %d bloco(s) de narração via FFmpeg..." % len(partes))
    proc = subprocess.run(cmd, cwd=str(saida.parent), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **SUBPROCESS_FLAGS)
    lista.unlink(missing_ok=True)
    if not (saida.exists() and saida.stat().st_size > 0):
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        log("    [ffmpeg stderr] " + " | ".join(err[-6:]) if err else "    [ffmpeg sem stderr]")
        raise ErroPipeline("FFmpeg não produziu o MP3 final (código %s)." % proc.returncode)
    if proc.returncode != 0:
        log("    (FFmpeg retornou %s mas o MP3 foi gerado — seguindo.)" % proc.returncode)


# ---------------------------------------------------------------------------
# TTS com cadeia de voz + backoff (idêntico ao long-form)
# ---------------------------------------------------------------------------

def _esperar_cancelavel(segundos, cancel):
    restante = float(segundos)
    while restante > 0:
        if cancel is not None and cancel.is_set():
            raise ErroPipeline("Cancelado pelo usuário.")
        passo = min(2.0, restante)
        time.sleep(passo)
        restante -= passo


def _tentar_cadeia(bloco, cadeia, chunk_mp3, base, i, n,
                   queimadas, falhas_consec, cooldown_limite, log):
    from capcut_tts import sintetizar, RateLimitError
    for v in cadeia:
        if v in queimadas:
            continue
        try:
            log("    bloco %d/%d (voz=%s, %d chars)..." % (i, n, v, len(bloco)))
            sintetizar(bloco, v, str(chunk_mp3), base=base, log=lambda *a, **k: None)
            falhas_consec[v] = 0
            return v
        except RateLimitError:
            falhas_consec[v] += 1
            if falhas_consec[v] >= cooldown_limite and v not in queimadas:
                queimadas.add(v)
                log("    ⚠ voz '%s' queimada nesta passada (%d falhas) — pulando até liberar." % (v, falhas_consec[v]))
            else:
                log("    ⚠ voz '%s' em rate-limit — caindo p/ próxima da cadeia." % v)
            continue
    return None


def synthesize(proj, log, cancel=None):
    """Gera narration.mp3 (provider CapCut) narrando roteiro_tts.txt com a voz do canal."""
    from capcut_tts import garantir_sidecar

    fonte = proj.roteiro_tts if proj.existe(proj.roteiro_tts) else proj.roteiro
    texto = fonte.read_text(encoding="utf-8", errors="replace").strip()
    if not texto:
        raise ErroPipeline("%s vazio — nada para narrar." % fonte.name)
    maxlen = int(os.environ.get("LONGFORM_TTS_CHUNK_CHARS", "1500"))
    cadeia = _voice_chain(_voz_primaria(proj, log), log=log)
    log("▶ Etapa 3 — TTS CapCut (idioma=%s, cadeia=%s, chunk=%d chars)."
        % (nome_idioma(), ", ".join(cadeia), maxlen))

    base = garantir_sidecar(log=log)
    blocos = _chunizar(texto, maxlen)
    log("    %d chars em %d bloco(s)." % (len(texto), len(blocos)))

    cooldown_limite = int(os.environ.get("LONGFORM_TTS_VOICE_COOLDOWN", "3"))
    max_esperas = int(os.environ.get("LONGFORM_TTS_RATELIMIT_RETRIES", "6"))
    wait_base = float(os.environ.get("LONGFORM_TTS_RATELIMIT_WAIT", "45"))
    wait_max = float(os.environ.get("LONGFORM_TTS_RATELIMIT_WAIT_MAX", "300"))
    falhas_consec = {v: 0 for v in cadeia}
    queimadas = set()

    partes, vozes_usadas = [], []
    for i, bloco in enumerate(blocos, 1):
        if cancel is not None and cancel.is_set():
            raise ErroPipeline("Cancelado pelo usuário.")
        chunk_mp3 = proj.dir / ("_tts_%02d.mp3" % i)
        if proj.existe(chunk_mp3):
            log("    bloco %d/%d já existe — pulado." % (i, len(blocos)))
            partes.append(chunk_mp3); continue

        espera_n = 0
        while True:
            voz_ok = _tentar_cadeia(bloco, cadeia, chunk_mp3, base, i, len(blocos),
                                    queimadas, falhas_consec, cooldown_limite, log)
            if voz_ok is not None:
                vozes_usadas.append(voz_ok); break
            if espera_n >= max_esperas:
                raise ErroPipeline(
                    "Todas as vozes da cadeia CapCut seguem em rate-limit no bloco %d após %d "
                    "esperas (%s). Rode de novo mais tarde (os blocos prontos são reaproveitados) "
                    "ou aumente LONGFORM_TTS_RATELIMIT_RETRIES." % (i, max_esperas, ", ".join(cadeia)))
            espera = min(wait_base * (2 ** espera_n), wait_max)
            espera_n += 1
            log("    ⏳ cadeia inteira em rate-limit no bloco %d — esperando %.0fs (%d/%d)..."
                % (i, espera, espera_n, max_esperas))
            _esperar_cancelavel(espera, cancel)
            queimadas.clear()
            for v in cadeia:
                falhas_consec[v] = 0

        if not proj.existe(chunk_mp3):
            raise ErroPipeline("CapCut TTS não gerou %s (bloco %d)." % (chunk_mp3.name, i))
        partes.append(chunk_mp3)

    if len(partes) == 1:
        shutil.copyfile(partes[0], proj.narration_mp3)
    else:
        _concat_mp3(partes, proj.narration_mp3, log)
    for i in range(1, len(blocos) + 1):
        (proj.dir / ("_tts_%02d.mp3" % i)).unlink(missing_ok=True)
    if not proj.existe(proj.narration_mp3):
        raise ErroPipeline("CapCut TTS não produziu narration.mp3.")
    if vozes_usadas:
        ranking = Counter(vozes_usadas).most_common()
        log("    ✓ narration.mp3 pronto. Vozes usadas: %s."
            % ", ".join("%s×%d" % (v, n) for v, n in ranking))


# ---------------------------------------------------------------------------
# Otimização de pausa (anti-robótico) — porta do long-form
# ---------------------------------------------------------------------------

def _pausa_opt_on():
    v = os.environ.get("LONGFORM_PAUSE_OPT", "1").strip().lower()
    return v not in ("0", "off", "none", "nao", "não", "no", "false")


def _duracao(ffmpeg, arq):
    ffprobe = str(Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix))
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", str(arq)],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **SUBPROCESS_FLAGS)
        return float((r.stdout or b"").decode("utf-8", errors="replace").strip() or 0)
    except Exception:
        return 0.0


def otimizar_pausas(proj, log):
    if not _pausa_opt_on() or proj.existe(proj.pausas_flag) or not proj.existe(proj.narration_mp3):
        if proj.existe(proj.pausas_flag):
            log("    pausas já otimizadas — pulado.")
        return
    min_sil = os.environ.get("LONGFORM_PAUSE_MIN", "0.30")
    keep = os.environ.get("LONGFORM_PAUSE_KEEP", "0.22")
    th = os.environ.get("LONGFORM_PAUSE_THRESHOLD", "-32dB")
    ff = achar_ffmpeg()
    if not proj.existe(proj.narration_raw):
        shutil.copyfile(proj.narration_mp3, proj.narration_raw)
    dur_antes = _duracao(ff, proj.narration_raw)
    tmp = proj.dir / "_narration_pausas.mp3"
    af = ("silenceremove=stop_periods=-1:stop_duration=%s:stop_threshold=%s:stop_silence=%s"
          % (min_sil, th, keep))
    log("▶ Etapa 3 — otimizando pausas da narração (cap >%ss → %ss, piso %s)..." % (min_sil, keep, th))
    proc = subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                           "-i", str(proj.narration_raw), "-af", af,
                           "-c:a", "libmp3lame", "-q:a", "2", str(tmp)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, **SUBPROCESS_FLAGS)
    if not (tmp.exists() and tmp.stat().st_size > 0):
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise ErroPipeline("Falha ao otimizar pausas (FFmpeg): %s" % (err[-300:] or "sem stderr"))
    os.replace(str(tmp), str(proj.narration_mp3))
    dur_depois = _duracao(ff, proj.narration_mp3)
    if proj.existe(proj.narration_srt):
        proj.narration_srt.unlink()
        log("    narration.srt antiga removida (será regerada do áudio otimizado).")
    economia = dur_antes - dur_depois
    pct = (100.0 * economia / dur_antes) if dur_antes else 0.0
    proj.pausas_flag.write_text(
        "min=%s keep=%s th=%s antes=%.1fs depois=%.1fs economia=%.1fs (%.0f%%)\n"
        % (min_sil, keep, th, dur_antes, dur_depois, economia, pct), encoding="utf-8")
    log("    ✓ pausas otimizadas: %.1fs → %.1fs (−%.1fs / −%.0f%%)." % (dur_antes, dur_depois, economia, pct))


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

def run(proj, log, cancel=None, **_):
    preparar_texto(proj, log)

    if proj.existe(proj.narration_mp3):
        log("    narration.mp3 já existe — TTS pulado.")
    else:
        synthesize(proj, log, cancel)

    otimizar_pausas(proj, log)

    if proj.existe(proj.narration_srt):
        log("    narration.srt já existe — Whisper pulado.")
        return
    if not WHISPER_SCRIPT.is_file():
        raise ErroPipeline(
            "Script de Whisper não encontrado em %s. Ajuste a env TINAGO_DIR." % WHISPER_SCRIPT)
    audio = achar_audio(proj.dir)
    os.environ["WHISPER_LANG"] = idioma()
    log("▶ Etapa 3 — SRT (Whisper, idioma=%s) a partir de %s..." % (nome_idioma(), audio.name))
    rodar_script([WHISPER_SCRIPT, audio], proj.dir, log, cancel)
    gerado = audio.with_suffix(".srt")
    if gerado.exists() and gerado != proj.narration_srt:
        shutil.copyfile(gerado, proj.narration_srt)
    if not proj.existe(proj.narration_srt):
        raise ErroPipeline("Whisper não gerou narration.srt.")
    log("    ✓ narration.srt pronto (timestamps reais da narração).")


# --- teste standalone: py -3 stages/s3_narracao.py <slug> -----------------------------
if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    slug = sys.argv[1] if len(sys.argv) > 1 else "82-faxineira-bilionario"
    run(projeto_por_slug(slug), print, None)
