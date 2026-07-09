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
                    SUBPROCESS_FLAGS, idioma, nome_idioma, parece_portugues)
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

    # P2 = voz dupla: separa a narração em segmentos ♂/♀ por POV (marcador ✦ ou
    # detecção pelo character_bible). P1 = voz única (texto limpo padrão).
    seg_file = proj.dir / "narracao_segmentos.json"
    segmentos = None
    if _parte(proj) == "p2":
        segmentos = _segmentos_p2(proj, bruto, log)
    if segmentos:
        seg_file.write_text(json.dumps(segmentos, ensure_ascii=False, indent=2), encoding="utf-8")
        texto = "\n\n".join(s["texto"] for s in segmentos)
    else:
        seg_file.unlink(missing_ok=True)  # evita resíduo de uma rodada P2 anterior
        texto = roteiro_estrutura.texto_narracao(bruto)
    if not texto.strip():
        raise ErroPipeline("roteiro.txt não produziu texto de narração (vazio após limpeza).")

    # Trava de idioma (última linha de defesa antes de gastar TTS): se o roteiro estiver em
    # PORTUGUÊS, a narração sairia em PT (voz do canal lendo o texto literal). Bloqueia aqui
    # — pega roteiro.txt semeado à mão que burlou a Etapa 1. Só no modo inglês; no modo teste
    # (LONGFORM_IDIOMA=pt) o PT é intencional. Decisão do editor 2026-07-09.
    if parece_portugues(texto):
        raise ErroPipeline(
            "O texto de narração (roteiro.txt) está em PORTUGUÊS — a narração sairia em PT e a "
            "legenda em inglês destroçado. Substitua projects/%s/roteiro.txt pela versão em "
            "INGLÊS (rode a Etapa 1 apontando o Doc EN do card, ou cole o roteiro EN). Só pra "
            "teste em PT: LONGFORM_IDIOMA=pt." % proj.dir.name)

    proj.roteiro_tts.write_text(texto, encoding="utf-8")
    if segmentos:
        # na P2 a narração não fala as linhas ✦ — a âncora tem que casar com a SRT real.
        import vozes_p2
        caps = [{"n": c["n"], "titulo": c["titulo"],
                 "primeira_frase": roteiro_estrutura._primeira_frase(
                     vozes_p2.limpar_marcadores(c["corpo"]))}
                for c in est["chapters"]]
    else:
        caps = [{"n": c["n"], "titulo": c["titulo"], "primeira_frase": c["primeira_frase"]}
                for c in est["chapters"]]
    (proj.dir / "capitulos.json").write_text(
        json.dumps({"hook_chars": len(est["hook"]), "capitulos": caps},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    log("  narração preparada: gancho %d chars + %d capítulo(s) -> roteiro_tts.txt / capitulos.json"
        % (len(est["hook"]), len(caps)))


def _segmentos_p2(proj, bruto, log):
    """Segmentos de voz ♂/♀ da P2 (ou None se não aplicável). Usa o character_bible
    da P1 (reaproveitado na pasta -p2) pra inferir o gênero dos leads."""
    try:
        import vozes_p2
    except ImportError as e:
        log("  ⚠ P2: módulo vozes_p2 indisponível (%s) — narração em voz única." % e)
        return None
    bible = ""
    if proj.existe(proj.character_bible):
        bible = proj.character_bible.read_text(encoding="utf-8", errors="replace")
    segs = vozes_p2.segmentos_p2(bruto, bible)
    if not segs:
        return None
    n, cm, cf = vozes_p2.resumo_segmentos(segs)
    tem_m = any(s["genero"] == "m" for s in segs)
    log("  P2 voz dupla: %d segmento(s) — ♂ %d chars / ♀ %d chars%s"
        % (n, cm, cf, "" if tem_m else " (sem POV masculino detectado — sai em voz única)"))
    return segs


# ---------------------------------------------------------------------------
# Voz do canal
# ---------------------------------------------------------------------------

def _canal(proj):
    """Nome do canal (List do card), do source.json. '' se ausente."""
    if proj.existe(proj.source):
        try:
            return (json.loads(proj.source.read_text(encoding="utf-8")).get("canal") or "").strip()
        except (OSError, ValueError):
            pass
    return ""


def _parte(proj):
    """'p1' | 'p2' — lido do source.json (semeado pelo pipeline na pasta -p2)."""
    if proj.existe(proj.source):
        try:
            return (json.loads(proj.source.read_text(encoding="utf-8")).get("parte") or "p1").strip().lower()
        except (OSError, ValueError):
            pass
    return "p1"


def _voz_do_canal_id(proj, genero, log):
    """ID CapCut da voz do canal para o gênero pedido ('f'/'m'). Cai na Joanne se o
    canal não tem id capturado. A P1 sempre usa 'f'; a P2 usa 'm'/'f' por segmento."""
    canal = _canal(proj)
    nome, vid = common.voz_do_canal(canal, genero)
    if not vid:
        log("  ⚠ voz '%s' (%s) do canal '%s' sem id CapCut — usando fallback (Joanne/cool_lady)."
            % (nome, genero, canal or "?"))
        vid = os.environ.get("CAPCUT_TTS_VOICE", common.VOZ_IDS.get("joanne", ""))
    else:
        log("  voz do canal '%s' (%s): %s (%s)" % (canal or "?", genero, nome, vid))
    return vid


def _voz_primaria(proj, log):
    """ID da voz feminina do canal (compat: a P1 é voz única feminina)."""
    return _voz_do_canal_id(proj, "f", log)


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


def _blocos_de_narracao(proj, maxlen, log):
    """Lista ordenada de (voz_primaria, texto_bloco) para o TTS.

    P1 (ou canal de voz única): todos os blocos na voz feminina do canal.
    P2 (narracao_segmentos.json presente): cada segmento ♂/♀ recebe a voz masculina
    ou feminina do canal e é fatiado em blocos ≤ maxlen. Fundir vozes iguais já foi
    feito no vozes_p2, então a alternância aqui é só onde o POV realmente muda."""
    seg_file = proj.dir / "narracao_segmentos.json"
    if proj.existe(seg_file):
        try:
            segs = json.loads(seg_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            segs = []
        if segs:
            voz_f = _voz_do_canal_id(proj, "f", log)
            dupla_on = os.environ.get("ROTEIRO_P2_VOZ_DUPLA", "1").strip().lower() \
                not in ("0", "off", "nao", "não", "no", "false")
            voz_m = _voz_do_canal_id(proj, "m", log) if dupla_on else voz_f
            if not dupla_on:
                log("    ROTEIRO_P2_VOZ_DUPLA desligado — P2 em voz feminina (✦ ainda removidos).")
            blocos = []
            for s in segs:
                v = voz_m if s.get("genero") == "m" else voz_f
                for b in _chunizar(s.get("texto", ""), maxlen):
                    if b.strip():
                        blocos.append((v, b))
            dual = voz_m != voz_f
            n_m = sum(1 for v, _ in blocos if v == voz_m)
            if dual:
                log("    P2 voz dupla ATIVA: ♂ %s / ♀ %s — %d bloco(s) ♂, %d bloco(s) ♀."
                    % (voz_m, voz_f, n_m, len(blocos) - n_m))
            else:
                log("    P2 em voz única (canal com ♂==♀=%s) — marcadores ✦ removidos da fala."
                    % voz_f)
            if blocos:
                return blocos, dual
    # Caminho padrão (P1) — voz feminina única, a partir do roteiro_tts.txt.
    fonte = proj.roteiro_tts if proj.existe(proj.roteiro_tts) else proj.roteiro
    texto = fonte.read_text(encoding="utf-8", errors="replace").strip()
    if not texto:
        raise ErroPipeline("%s vazio — nada para narrar." % fonte.name)
    voz = _voz_primaria(proj, log)
    return [(voz, b) for b in _chunizar(texto, maxlen)], False


def synthesize(proj, log, cancel=None):
    """Gera narration.mp3 (provider CapCut). Voz única (P1) ou alternada ♂/♀ (P2)."""
    from capcut_tts import garantir_sidecar

    maxlen = int(os.environ.get("LONGFORM_TTS_CHUNK_CHARS", "1500"))
    blocos, _dual = _blocos_de_narracao(proj, maxlen, log)
    n = len(blocos)
    total_chars = sum(len(b) for _, b in blocos)
    log("▶ Etapa 3 — TTS CapCut (idioma=%s, chunk=%d chars): %d chars em %d bloco(s)."
        % (nome_idioma(), maxlen, total_chars, n))

    base = garantir_sidecar(log=log)

    cooldown_limite = int(os.environ.get("LONGFORM_TTS_VOICE_COOLDOWN", "3"))
    max_esperas = int(os.environ.get("LONGFORM_TTS_RATELIMIT_RETRIES", "6"))
    wait_base = float(os.environ.get("LONGFORM_TTS_RATELIMIT_WAIT", "45"))
    wait_max = float(os.environ.get("LONGFORM_TTS_RATELIMIT_WAIT_MAX", "300"))
    # Estado de rate-limit compartilhado por TODAS as vozes que podem entrar (primárias
    # ♂/♀ + fallbacks), já que a cadeia muda de bloco pra bloco na P2.
    todas = set()
    for voz_primaria, _ in blocos:
        todas.update(_voice_chain(voz_primaria))
    falhas_consec = {v: 0 for v in todas}
    queimadas = set()

    partes, vozes_usadas = [], []
    for i, (voz_primaria, bloco) in enumerate(blocos, 1):
        if cancel is not None and cancel.is_set():
            raise ErroPipeline("Cancelado pelo usuário.")
        chunk_mp3 = proj.dir / ("_tts_%02d.mp3" % i)
        if proj.existe(chunk_mp3):
            log("    bloco %d/%d já existe — pulado." % (i, n))
            partes.append(chunk_mp3); continue
        cadeia = _voice_chain(voz_primaria)

        espera_n = 0
        while True:
            voz_ok = _tentar_cadeia(bloco, cadeia, chunk_mp3, base, i, n,
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
            for v in todas:
                falhas_consec[v] = 0

        if not proj.existe(chunk_mp3):
            raise ErroPipeline("CapCut TTS não gerou %s (bloco %d)." % (chunk_mp3.name, i))
        partes.append(chunk_mp3)

    if len(partes) == 1:
        shutil.copyfile(partes[0], proj.narration_mp3)
    else:
        _concat_mp3(partes, proj.narration_mp3, log)
    for i in range(1, n + 1):
        (proj.dir / ("_tts_%02d.mp3" % i)).unlink(missing_ok=True)
    if not proj.existe(proj.narration_mp3):
        raise ErroPipeline("CapCut TTS não produziu narration.mp3.")
    if vozes_usadas:
        ranking = Counter(vozes_usadas).most_common()
        log("    ✓ narration.mp3 pronto. Vozes usadas: %s."
            % ", ".join("%s×%d" % (v, k) for v, k in ranking))


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
# Narração das TROCAS DE CAPÍTULO (mini-TTS por título)
# ---------------------------------------------------------------------------
# No Romance Maker a narradora DITAVA "Chapter N — Título" no início de cada capítulo e a
# capa (troca) ficava na tela exatamente pelo tempo dessa fala. Isso se perdeu na portagem
# (o texto_narracao remove os cabeçalhos). Aqui geramos um mini-áudio POR capítulo —
# "Chapter N. <Título>." na voz feminina do canal — gravado em covers/titulo_NN.mp3. A
# Etapa 6 usa a duração dele como a duração da capa; a Etapa 7 toca ele por baixo da capa.
# Desacoplado do narration.mp3 do corpo → a sincronia sai por construção (não depende de
# Whisper). Desliga com ROTEIRO_COVER_NARRAR_TITULO=0 (capa volta a ser muda/duração fixa).

def _cover_narrar_on():
    v = os.environ.get("ROTEIRO_COVER_NARRAR_TITULO", "1").strip().lower()
    return v not in ("0", "off", "nao", "não", "no", "false")


def _texto_titulo(n, titulo):
    """Texto que a narradora fala na troca: 'Chapter N. <Título>.' (ponto = pausa dramática).
    Sem título (fallback 'Chapter N') → só 'Chapter N.'. Remove prefixo 'Chapter N' duplicado
    caso o título já venha rotulado."""
    t = re.sub(r"^\**\s*chapter\s+\d+\s*[—:\-]?\s*", "", (titulo or "").strip(),
               flags=re.IGNORECASE).strip()
    return ("Chapter %d. %s." % (n, t)) if t else ("Chapter %d." % n)


def sintetizar_titulos(proj, log, cancel=None):
    """Sintetiza covers/titulo_NN.mp3 ('Chapter N. Título.') por capítulo na voz do canal e
    grava a duração de cada um em capitulos.json (campo 'titulo_dur', em s). Idempotente
    (âncora = o mp3). TTS curtíssimo → sem o backoff longo do synthesize: se a cadeia toda
    estiver em rate-limit, aquele título fica sem áudio e a capa cai no silêncio (gracioso)."""
    cap_json = proj.dir / "capitulos.json"
    if not proj.existe(cap_json):
        return
    if not _cover_narrar_on():
        log("    narração das trocas de capítulo desligada (ROTEIRO_COVER_NARRAR_TITULO=0).")
        return
    try:
        dados = json.loads(cap_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    caps = dados.get("capitulos", [])
    if not caps:
        return

    from capcut_tts import garantir_sidecar
    ff = achar_ffmpeg()
    proj.covers_dir.mkdir(parents=True, exist_ok=True)
    base = garantir_sidecar(log=log)
    voz = _voz_primaria(proj, log)
    cadeia = _voice_chain(voz, log)
    cooldown_limite = int(os.environ.get("LONGFORM_TTS_VOICE_COOLDOWN", "3"))
    queimadas, falhas = set(), {v: 0 for v in cadeia}

    log("▶ Etapa 3 — narração das trocas de capítulo (%d título[s], voz=%s)..." % (len(caps), voz))
    for c in caps:
        if cancel is not None and cancel.is_set():
            raise ErroPipeline("Cancelado pelo usuário.")
        n = c.get("n")
        mp3 = proj.covers_dir / ("titulo_%02d.mp3" % n)
        if proj.existe(mp3):
            c["titulo_dur"] = round(_duracao(ff, mp3), 3)
            continue
        texto = _texto_titulo(n, c.get("titulo", ""))
        voz_ok = _tentar_cadeia(texto, cadeia, mp3, base, n, len(caps),
                                queimadas, falhas, cooldown_limite, log)
        if voz_ok is None or not proj.existe(mp3):
            log("    ⚠ capítulo %d: título não sintetizado (rate-limit) — capa fica muda." % n)
            c.pop("titulo_dur", None)
            continue
        c["titulo_dur"] = round(_duracao(ff, mp3), 3)
        log("    ✓ titulo_%02d.mp3 — \"%s\" (%.1fs)." % (n, texto[:44], c["titulo_dur"]))

    dados["capitulos"] = caps
    cap_json.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


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
    else:
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

    # Narração das trocas de capítulo (mini-TTS por título) — alimenta a capa (Etapas 6 e 7).
    sintetizar_titulos(proj, log, cancel)


# --- teste standalone: py -3 stages/s3_narracao.py <slug> -----------------------------
if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    slug = sys.argv[1] if len(sys.argv) > 1 else "82-faxineira-bilionario"
    run(projeto_por_slug(slug), print, None)
