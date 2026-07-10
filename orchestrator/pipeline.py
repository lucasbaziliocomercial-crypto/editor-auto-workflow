# -*- coding: utf-8 -*-
"""pipeline.py — orquestrador da esteira roteiro-auto (edição automática de vídeo).

Mesma filosofia da esteira long-form: cada etapa é um módulo em stages/ com
`run(proj, log, cancel, **kw)`, IDEMPOTENTE (checa o artefato-âncora e pula se já existe).
Diferença desta esteira: o roteiro JÁ VEM PRONTO (em inglês, nos comentários do card) —
não há geração nem validação de roteiro. As etapas:

    1 ClickUp     s1_clickup     -> source.json + roteiro.txt (roteiro pronto dos comentários)
       ── GATE 1 (opcional): conferir o roteiro antes de gastar TTS/Magnific
    2 Personagens s2_personagens -> character_bible.txt + fichas na Library do Magnific
    3 Narração    s3_narracao    -> narration.mp3 + narration.srt (CapCut-TTS + Whisper)
    4 Prompts     s4_prompts     -> prompts_imagens.txt + prompts_capas.txt
    5 Imagens     s5_imagens     -> images/cap<c>_<nn>.png (Magnific, ancorado na Library)
    6 Capas       s6_capas       -> covers/cap<c>.mp4 (Ken-Burns + título; covers.py portado)
       ── GATE 2 (opcional): conferir os drop-in de materiais/<canal>/ (teaser, book2)
    7 Montagem    s7_montagem    -> mapping.json -> out/video_mudo.mp4 -> out/final.mp4
    8 Entrega     s8_entrega     -> ENTREGAS/<card>/ (P1+P2 juntas: MP4 no topo + extras/parte-N/)
                                    + VIDEOS-PRONTOS/<card> — Parte N.mp4 (lista plana, hardlink)

CLI:  py -3 pipeline.py [alvo] [N N N ...] [--slug X] [--categoria selena|mafia]
                        [--no-gates] [--refazer]
  alvo  = texto de busca do card (default "Alpha King"); dígitos = etapas (default todas).
"""

import os
import sys
import json
import time
import importlib

import config  # noqa: F401  (efeito colateral: liga as env vars da esteira)
import common
from common import Projeto, ErroPipeline, slugify, PROJECTS_DIR, projeto_por_slug

TODAS = (1, 2, 3, 4, 5, 6, 7, 8)

# Rótulo curto por etapa (pro resumo de tempos no fim da rodada).
STAGE_LABELS = {
    1: "ClickUp/roteiro",
    2: "Personagens",
    3: "Narração",
    4: "Prompts",
    5: "Imagens",
    6: "Capas",
    7: "Montagem",
    8: "Entrega",
}

# Etapa -> (módulo em stages/, artefato-âncora relativo ao proj p/ idempotência).
# O âncora é checado por proj.existe(); glob (com *) usa list(dir.glob(...)).
STAGES = {
    1: ("s1_clickup",     "source.json"),
    2: ("s2_personagens", "character_bible.txt"),
    3: ("s3_narracao",    "narration.srt"),
    4: ("s4_prompts",     "prompts_imagens.txt"),
    5: ("s5_imagens",     "images/*.png"),
    6: ("s6_capas",       "covers/*.mp4"),
    7: ("s7_montagem",    "out/final.mp4"),
    8: ("s8_entrega",     ".entregue"),
}


def _stale_off():
    """A checagem de obsolescência está desligada? (ROTEIRO_TTS_STALE=off)."""
    return os.environ.get("ROTEIRO_TTS_STALE", "regen").strip().lower() == "off"


def _mais_velho(a, b):
    """True se o arquivo `a` (mtime) é mais velho que `b`, com folga ROTEIRO_TTS_STALE_TOL."""
    try:
        tol = float(os.environ.get("ROTEIRO_TTS_STALE_TOL", "2"))
        return a.stat().st_mtime + tol < b.stat().st_mtime
    except (OSError, ValueError):
        return False


def _narracao_obsoleta(proj):
    """narration.mp3 mais velho que roteiro.txt = áudio de OUTRO draft (roteiro regerado depois
    do TTS). Dispara em regen (regenera) E warn (só avisa no run()); off desliga. Marcar a
    Etapa 3 não-pronta é o que faz o pipeline CHAMAR o run() — senão a trava dentro dele nunca
    roda numa re-rodada 'já pronta'. Ver s3_narracao._narracao_stale."""
    if _stale_off() or not (proj.existe(proj.narration_mp3) and proj.existe(proj.roteiro)):
        return False
    return _mais_velho(proj.narration_mp3, proj.roteiro)


def _montagem_obsoleta(proj):
    """out/final.mp4 mais velho que a narração = render de um áudio anterior (ex.: a Etapa 3
    acabou de regenerar o TTS obsoleto). Força re-montar em vez de manter o vídeo dessincronizado.
    off desliga. No caminho feliz (audio gerado -> montagem) o final.mp4 é mais NOVO, não dispara."""
    if _stale_off():
        return False
    fin = proj.dir / "out" / "final.mp4"
    if not (fin.exists() and proj.existe(proj.narration_mp3)):
        return False
    return _mais_velho(fin, proj.narration_mp3)


# Arquivos de CÓDIGO de que cada etapa depende. Se algum for MAIS NOVO que o artefato-âncora, o
# artefato foi produzido por código velho e NÃO tem as features/ajustes novos → o auto-rebuild age.
# _CORE_DEPS = base compartilhada (common.py tem o CANAL_VOZ; por isso mudar a voz reflete na
# Etapa 3) — vale pra TODAS as etapas rastreadas. _COD_DEPS = arquivos específicos de cada etapa.
#
# DUAS POLÍTICAS (decisão 2026-07-09, "só local/grátis"):
#   • _AUTO_REBUILD_STAGES = {3, 6, 7} → LOCAL/GRÁTIS: quando o código muda, a etapa RE-RODA sozinha
#     e limpa o cache p/ reaplicar (narração, capas, montagem — sem custo externo). É o que faz
#     "toda mudança minha já entrar" ao reusar o mesmo card.
#   • Rastreadas mas FORA do auto (2 bible, 4 prompts, 5 imagens Magnific $$$) → só AVISAM
#     "código novo, rode Refazer": a cadeia 2→4→5 termina em crédito Magnific, então não refaço
#     sozinho (evita queimar crédito e bible/prompt novo com imagem velha).
# ROTEIRO_AUTO_REBUILD=0 desliga tudo (volta à idempotência pura por arquivo).
_CORE_DEPS = ("common.py", "config.py")
_COD_DEPS = {
    2: ("stages/s2_personagens.py", "clickup_api.py", "runner.py", "roteiro_estrutura.py"),
    3: ("stages/s3_narracao.py", "runner.py", "roteiro_estrutura.py", "capcut_tts.py", "vozes_p2.py"),
    4: ("stages/s4_prompts.py", "runner.py", "roteiro_estrutura.py"),
    5: ("stages/s5_imagens.py", "stages/magnific_seam.py"),
    6: ("stages/s6_capas.py", "covers.py", "roteiro_estrutura.py", "montagem_vertical.py"),
    7: ("stages/s7_montagem.py", "montagem_vertical.py", "resumo_cta.py",
        "qr_overlay.py", "ultimo_minuto.py"),
}
_AUTO_REBUILD_STAGES = {3, 6, 7}


def _auto_rebuild_on():
    return os.environ.get("ROTEIRO_AUTO_REBUILD", "1").strip().lower() \
        not in ("0", "off", "no", "false", "nao", "não")


def _artefato_mtime(proj, n):
    """mtime do artefato-âncora da etapa (o mais NOVO, se for glob); None se não existe."""
    alvo = STAGES[n][1]
    try:
        if "*" in alvo:
            d, pad = alvo.split("/", 1)
            fs = list((proj.dir / d).glob(pad))
            return max((f.stat().st_mtime for f in fs), default=None)
        p = proj.dir / alvo
        return p.stat().st_mtime if p.exists() else None
    except OSError:
        return None


def _codigo_desatualizado(proj, n):
    """Nome do 1º arquivo de código da etapa n que é MAIS NOVO que o artefato-âncora (= artefato
    feito por código velho, sem as features novas). None se nenhum / etapa não-render / desligado."""
    if not _auto_rebuild_on() or n not in _COD_DEPS:
        return None
    art = _artefato_mtime(proj, n)
    if art is None:
        return None
    base = os.path.dirname(os.path.abspath(__file__))
    tol = float(os.environ.get("ROTEIRO_TTS_STALE_TOL", "2"))
    for rel in _COD_DEPS[n] + _CORE_DEPS:
        f = os.path.join(base, rel)
        try:
            if os.path.exists(f) and art + tol < os.path.getmtime(f):
                return os.path.basename(rel)
        except OSError:
            pass
    return None


def _limpar_render(proj, n, log):
    """Limpa os artefatos de RENDER da etapa p/ um rebuild completo (as features novas re-aplicam,
    sem reuso de segmento). NÃO toca em entradas de outras etapas (ex.: covers/titulo_*.mp3 é da
    Etapa 3, então na 6 só apagamos os .mp4 das capas)."""
    import shutil
    if n == 3:
        # Narração: apaga o áudio + derivados (mesma lista da trava anti-áudio-velho) p/ o TTS
        # rodar de novo com o código/voz atual (ex.: CANAL_VOZ mudou no common.py). Os _tts_*.mp3
        # são chunks de um run interrompido — limpa também p/ não reusar a voz antiga.
        alvos = 0
        for f in (proj.narration_mp3, proj.narration_raw, proj.pausas_flag, proj.narration_srt):
            try:
                if f.exists():
                    f.unlink(missing_ok=True); alvos += 1
            except OSError:
                pass
        for p in proj.dir.glob("_tts_*.mp3"):
            try:
                p.unlink(); alvos += 1
            except OSError:
                pass
        if alvos:
            log("  limpei narração + derivados p/ re-sintetizar o TTS com o código/voz atual.")
    elif n == 7:
        outd = proj.dir / "out"
        if outd.exists():
            shutil.rmtree(outd, ignore_errors=True)
            log("  limpei out/ (segmentos + vídeo) p/ remontar do zero com as features novas.")
    elif n == 6:
        apagados = 0
        for p in proj.covers_dir.glob("*.mp4"):
            try:
                p.unlink(); apagados += 1
            except OSError:
                pass
        if apagados:
            log("  limpei %d capa(s) p/ regerar." % apagados)


def _etapa_pronta(proj, n, log=None):
    """True se o artefato-âncora da etapa n já existe (base da idempotência/‘Continuar’)."""
    alvo = STAGES[n][1]
    if "*" in alvo:
        d, pad = alvo.split("/", 1)
        pronta = bool(list((proj.dir / d).glob(pad)))
    else:
        pronta = proj.existe(proj.dir / alvo)
    # Anti-áudio-velho (o problema de sincronia do 256): marcar a etapa NÃO-pronta força o
    # pipeline a chamar o run() dela. Etapa 3 = narração de outro draft; Etapa 7 = montagem de
    # um áudio anterior (após a Etapa 3 regenerar). Sem isto, a re-rodada 'já pronta' pula tudo
    # e o vídeo sai/continua dessincronizado.
    if pronta and n == 3 and _narracao_obsoleta(proj):
        if log:
            log("Etapa 3 (s3_narracao): narração é de outro draft do roteiro — regenerando p/ sincronizar.")
        return False
    if pronta and n == 7 and _montagem_obsoleta(proj):
        if log:
            log("Etapa 7 (s7_montagem): vídeo montado com áudio anterior — remontando p/ sincronizar.")
        return False
    return pronta


# --- trava .running por projeto (uma rodada por card; porta do long-form) -------------

def _pid_vivo(pid):
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _adquirir_lock(proj, log):
    lock = proj.dir / ".running"
    if lock.is_file():
        try:
            pid = int((lock.read_text(encoding="utf-8").split() or ["0"])[0])
        except (ValueError, OSError):
            pid = 0
        if pid and pid != os.getpid() and _pid_vivo(pid):
            raise ErroPipeline(
                "Já existe uma rodada deste card em andamento (pid %d). "
                "Espere terminar ou apague %s." % (pid, lock))
    lock.write_text("%d %d" % (os.getpid(), int(time.time())), encoding="utf-8")
    return lock


def _liberar_lock(lock):
    try:
        lock.unlink()
    except OSError:
        pass


# --- resolução do projeto -------------------------------------------------------------

def _aplicar_formato_canal(proj, log, _feito=[]):
    """Força ROTEIRO_W/H/ASPECT conforme o canal (nome da List em source.json), pra as
    etapas visuais (4/5/6/7) saírem no formato certo — ex.: Lena = horizontal 16:9. Canal
    sem override mantém os defaults globais. No-op se source.json ainda não existe."""
    if not proj.existe(proj.source):
        return
    try:
        canal = (json.loads(proj.source.read_text(encoding="utf-8")).get("canal") or "").strip()
    except (OSError, ValueError):
        return
    w, h, asp = common.formato_do_canal(canal)
    os.environ["ROTEIRO_W"], os.environ["ROTEIRO_H"], os.environ["ROTEIRO_ASPECT"] = str(w), str(h), asp
    if not _feito:
        log("  formato do canal '%s': %dx%d (%s)." % (canal or "?", w, h, asp))
        _feito.append(True)


def _garantir_projeto(slug, log):
    """Resolve o projeto. Nesta v1 exige --slug (a Etapa 1 grava tudo em projects/<slug>/).

    (Sem slug, a Etapa 1 rodaria num _tmp_ e renomearia pelo título do card — porte futuro.)"""
    if not slug:
        raise ErroPipeline(
            "Informe --slug <nome> (v1). Ex.: --slug 141-lena. A Etapa 1 grava o card ali.")
    proj = projeto_por_slug(slug)
    log("Projeto: %s" % proj.dir)
    return proj


# --- Parte 2 = vídeo separado (pasta irmã) --------------------------------------------

def _card_sem_p2(parent):
    """True quando o card DEFINITIVAMENTE só tem P1: source.json presente, `n_caps_p2`==0 e sem
    roteiro_p2.txt. Nesse caso a P2 é pulada SEM erro (não há Tab 2 a montar) — comum ao rodar
    com "Ambas". Casos ambíguos (source.json ausente, ou n_caps_p2>0 mas o arquivo sumiu) caem
    no erro informativo de _preparar_projeto_p2 (aí a Etapa 1 realmente precisa rodar)."""
    if parent.existe(parent.dir / "roteiro_p2.txt"):
        return False
    if not parent.existe(parent.source):
        return False
    try:
        src = json.loads(parent.source.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return False
    return int(src.get("n_caps_p2", 0) or 0) == 0


def _preparar_projeto_p2(parent, slug, log):
    """Prepara um PROJETO SEPARADO para a Parte 2 (vídeo próprio), reaproveitando o trabalho
    da P1. A P2 roda a esteira inteira numa pasta irmã `projects/<slug>-p2/` com
    `roteiro.txt` = a Parte 2 do card. Assim os artefatos (narration.*, images/, covers/,
    out/final.mp4) NÃO colidem com os da P1 e a idempotência por pasta funciona sem tocar no
    código das etapas. Entrega sai separada (nome_card + ' - P2').

    Semente idempotente (só cria o que falta):
      - roteiro.txt   <- parent/roteiro_p2.txt (a Parte 2 baixada pela Etapa 1)
      - source.json   <- parent/source.json, com nome_card + ' - P2'
      - character_bible.txt + referencias.json + referencias/*  <- copiados da P1 (mesmos
        personagens) → as Etapas 1 e 2 já contam como prontas e são puladas na P2.
    """
    p2_txt = parent.dir / "roteiro_p2.txt"
    if not parent.existe(p2_txt):
        raise ErroPipeline(
            "Parte 2: não achei roteiro_p2.txt em '%s'. Rode a Parte 1 (ou ao menos a Etapa 1) "
            "antes — é ela que baixa a Tab 2 do card. (Se o card só tem P1, não há P2 a gerar.)"
            % parent.dir.name)

    p2 = projeto_por_slug(slug + "-p2")
    log("Parte 2 → projeto separado: %s" % p2.dir)

    if not p2.existe(p2.roteiro):
        p2.roteiro.write_text(p2_txt.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        log("  P2: roteiro.txt semeado a partir de roteiro_p2.txt.")

    if not p2.existe(p2.source) and parent.existe(parent.source):
        try:
            src = json.loads(parent.source.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            src = {}
        base_nome = src.get("nome_card") or src.get("titulo") or parent.dir.name
        src["nome_card"] = "%s - P2" % base_nome
        src["parte"] = "p2"
        p2.source.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")
        log("  P2: source.json semeado (nome_card='%s')." % src["nome_card"])

    # Reaproveita personagens da P1 (mesmos) → pula Etapas 1 e 2 na P2.
    import shutil
    if parent.existe(parent.character_bible) and not p2.existe(p2.character_bible):
        shutil.copyfile(parent.character_bible, p2.character_bible)
    if parent.existe(parent.referencias_json) and not p2.existe(p2.referencias_json):
        shutil.copyfile(parent.referencias_json, p2.referencias_json)
    if parent.referencias_dir.is_dir():
        for ref in parent.referencias_dir.glob("*"):
            if ref.is_file() and not (p2.referencias_dir / ref.name).exists():
                shutil.copyfile(ref, p2.referencias_dir / ref.name)
    if p2.existe(p2.character_bible):
        log("  P2: bible + refs da P1 reaproveitados (Etapas 1-2 puladas na P2).")
    else:
        log("  P2: sem bible da P1 — a Etapa 2 será gerada na pasta da P2.")

    # A P2 ganhou ABERTURA (thumbnail animado por IA) e CENAS FINAIS (clipes do teaser) — 2026-07-10.
    # Ambas moram na P1: o thumb_ref.png (baixado na Etapa 1) e a pasta teaser/ (largada pela editora
    # por-vídeo). A pasta da P2 é irmã e não os tinha; copiamos aqui (só o que falta) pra a Etapa 7
    # da P2 achar os insumos sem depender da pasta da P1.
    if parent.existe(parent.thumb_ref) and not p2.existe(p2.thumb_ref):
        shutil.copyfile(parent.thumb_ref, p2.thumb_ref)
        log("  P2: thumb_ref.png copiado da P1 (fonte da abertura animada).")
    tsrc = parent.dir / "teaser"
    if tsrc.is_dir():
        tdst = p2.dir / "teaser"
        tdst.mkdir(parents=True, exist_ok=True)
        copiados = 0
        for clip in sorted(tsrc.glob("*")):
            if clip.is_file() and not (tdst / clip.name).exists():
                shutil.copyfile(clip, tdst / clip.name)
                copiados += 1
        if copiados:
            log("  P2: %d clipe(s) do teaser copiado(s) da P1 (fonte das cenas finais)." % copiados)
    return p2


# --- laço principal -------------------------------------------------------------------

def _fmt_dur(seg):
    """Formata segundos como '1h 03m 20s' / '4m 12s' / '38s' pro log de conclusão."""
    seg = int(round(seg))
    h, r = divmod(seg, 3600)
    m, s = divmod(r, 60)
    if h:
        return "%dh %02dm %02ds" % (h, m, s)
    if m:
        return "%dm %02ds" % (m, s)
    return "%ds" % s


def _fmt_min(seg):
    """Segundos -> minutos com 1 casa (o que a editora quer pra ter noção do gargalo)."""
    return "%.1f min" % (seg / 60.0)


def _resumo_tempos(proj, tempos, total, log):
    """Loga o tempo por etapa (em minutos) e persiste em _tempos.json, pra dar noção de qual
    processo demorou mais. `tempos` = {n: segundos} só das etapas que REALMENTE rodaram nesta
    rodada (as puladas por idempotência não entram)."""
    if tempos:
        log("── Tempo por etapa (esta rodada) ─────────────")
        for n in sorted(tempos):
            log("   Etapa %d — %-16s %8s" % (n, STAGE_LABELS.get(n, ""), _fmt_min(tempos[n])))
    log("   %-25s %8s" % ("TOTAL", _fmt_min(total)))
    # Persiste pra editora comparar entre vídeos ao longo do tempo (dotfile discreto na pasta).
    try:
        dados = {
            "total_min": round(total / 60.0, 2),
            "etapas": {str(n): round(tempos[n] / 60.0, 2) for n in sorted(tempos)},
        }
        (proj.dir / "_tempos.json").write_text(
            json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def pipeline(alvo=None, etapas=TODAS, log=print, cancel=None, *,
             slug=None, card_query="Alpha King", card_id=None, categoria=None,
             parte="p1", pular_gates=False, refazer=False, **extra):
    t0 = time.perf_counter()
    etapas = set(etapas or TODAS)
    parent = _garantir_projeto(slug, log)
    # Parte 2 = vídeo próprio numa pasta irmã. `proj` passa a apontar pra P2; todas as etapas
    # rodam nela sem colidir com os artefatos da P1.
    p2_mode = str(parte).lower() == "p2"
    if p2_mode and _card_sem_p2(parent):
        log("── Parte 2 pulada: este card só tem Parte 1 (sem Tab 2 no Doc) — nada a gerar. ✓")
        return parent
    proj = projeto_por_slug(slug + "-p2") if p2_mode else parent
    if refazer:
        for n in sorted(etapas):
            _limpar_etapa(proj, n, log)
    # Semeia a P2 DEPOIS do refazer (a semente é idempotente e restaura roteiro/source/bible
    # que o refazer possa ter limpado — assim a Etapa 1 não rebaixa a P1 pra dentro da P2).
    if p2_mode:
        _preparar_projeto_p2(parent, slug, log)
    lock = _adquirir_lock(proj, log)
    tempos = {}  # {n: segundos} das etapas que realmente rodaram nesta rodada
    try:
        for n in sorted(etapas):
            if cancel and cancel():
                log("Cancelado.")
                break
            nome_mod, _ = STAGES[n]
            _aplicar_formato_canal(proj, log)
            if not refazer:
                # Código da etapa mudou desde o último artefato? (senão a idempotência reusa o
                # antigo — foi por isso que o card 256 saiu "igual" mesmo com o código mudado.)
                dep = _codigo_desatualizado(proj, n)
                if dep and n in _AUTO_REBUILD_STAGES:
                    # LOCAL/GRÁTIS (3/6/7): re-roda e limpa o cache p/ as mudanças reaplicarem.
                    log("Etapa %d (%s): código novo (%s) desde o último render — refazendo p/ aplicar as mudanças."
                        % (n, nome_mod, dep))
                    _limpar_render(proj, n, log)
                else:
                    if dep:
                        # Rastreada mas FORA do auto (2 bible / 4 prompts / 5 imagens Magnific):
                        # avisa, mas NÃO refaz sozinho (evita queimar crédito) — o usuário Refaz.
                        log("Etapa %d (%s): código novo (%s) desde o último artefato — NÃO refiz sozinho "
                            "(etapa custosa). Rode “Refazer” nesta etapa quando quiser reaplicar."
                            % (n, nome_mod, dep))
                    if _etapa_pronta(proj, n, log):
                        log("Etapa %d (%s): já pronta — pulando." % (n, nome_mod))
                        continue
            log("── Etapa %d — %s ─────────────────────────" % (n, nome_mod))
            try:
                mod = importlib.import_module("stages." + nome_mod)
            except ModuleNotFoundError:
                raise ErroPipeline(
                    "Etapa %d ainda não implementada (stages/%s.py não existe)." % (n, nome_mod))
            kw = dict(card_query=(alvo or card_query), card_id=card_id,
                      categoria=categoria, parte=parte, pular_gates=pular_gates)
            t_etapa = time.perf_counter()
            mod.run(proj, log, cancel, **kw)
            tempos[n] = time.perf_counter() - t_etapa
            log("   ⏱ Etapa %d (%s) levou %s." % (n, STAGE_LABELS.get(n, nome_mod), _fmt_dur(tempos[n])))
            # Gates (só onde há humano). Desligados por --no-gates.
            if not pular_gates:
                _gate(proj, n, log, cancel)
    finally:
        _liberar_lock(lock)
    total = time.perf_counter() - t0
    _resumo_tempos(proj, tempos, total, log)
    log("Concluído em %s (montagem do vídeo completo)." % _fmt_dur(total))
    return proj


def _limpar_etapa(proj, n, log):
    """Apaga o artefato-âncora da etapa (força regeneração). Best-effort."""
    import shutil
    alvo = STAGES[n][1]
    p = proj.dir / (alvo.split("/*", 1)[0] if "*" in alvo else alvo)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        try:
            p.unlink()
        except OSError:
            pass
    log("  refazer: limpei %s" % p.name)


def _gate(proj, n, log, cancel):
    """Gates humanos: Gate 1 após a Etapa 1 (conferir roteiro), Gate 2 após a 6 (materiais).

    Reusa panel/gates.py do long-form quando aplicável; por ora só loga (implementação por vir)."""
    if n == 1:
        log("  [GATE 1] roteiro pronto em %s — confira antes de gastar TTS/Magnific." % proj.roteiro.name)
    elif n == 6:
        log("  [GATE 2] confira o teaser deste vídeo (projects/<slug>/teaser/) e os drop-in do canal "
            "(materiais/<canal>/: book2) antes da montagem.")


# --- CLI ------------------------------------------------------------------------------

def main(argv=None):
    # Headless/CLI: o stdout do Windows é cp1252 e quebra nos caracteres dos logs (──/▶/✓/⚠).
    # Força utf-8 para o CLI rodar igual à GUI (que usa seu próprio log, não print).
    for _fluxo in (sys.stdout, sys.stderr):
        try:
            _fluxo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    argv = list(argv if argv is not None else sys.argv[1:])
    opts = dict(slug=None, card_id=None, categoria=None, parte="p1",
                pular_gates=False, refazer=False)
    resto = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--no-gates", "--sem-gates"):
            opts["pular_gates"] = True
        elif a == "--refazer":
            opts["refazer"] = True
        elif a == "--slug" and i + 1 < len(argv):
            opts["slug"] = argv[i + 1]; i += 1
        elif a == "--card" and i + 1 < len(argv):
            opts["card_id"] = argv[i + 1]; i += 1
        elif a == "--categoria" and i + 1 < len(argv):
            opts["categoria"] = argv[i + 1]; i += 1
        elif a == "--parte" and i + 1 < len(argv):
            opts["parte"] = argv[i + 1]; i += 1
        else:
            resto.append(a)
        i += 1
    etapas = sorted(int(x) for x in resto if x.isdigit()) or list(TODAS)
    alvo = next((x for x in resto if not x.isdigit()), None)
    try:
        pipeline(alvo=alvo, etapas=etapas, **opts)
    except ErroPipeline as e:
        print("ERRO:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
