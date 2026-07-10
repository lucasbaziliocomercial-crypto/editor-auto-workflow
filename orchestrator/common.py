# -*- coding: utf-8 -*-
"""common.py — helpers compartilhados da esteira long-form (YouTube 16:9).

Porta enxuta de novela_common.py / novela_orquestra.py do projeto TINAGO, adaptada
para o pipeline long-form: layout de projeto (projects/<slug>/), descoberta de
executáveis, slug, e parse de SRT -> cues. SEM dependência de tkinter.
"""

import os
import re
import sys
import json
import shutil
import subprocess
import unicodedata
from pathlib import Path

# Flags p/ ESCONDER a janela preta de console que cada subprocesso (claude -p, py,
# ffmpeg, powershell…) abriria no Windows. A GUI roda sem console, então o SO aloca
# um console novo por subprocesso — CREATE_NO_WINDOW suprime isso. Espalhe via
# `**SUBPROCESS_FLAGS` em TODO subprocess.Popen/run de programa de console.
# (Fora do Windows vira {} e não tem efeito.)
if os.name == "nt":
    SUBPROCESS_FLAGS = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
else:
    SUBPROCESS_FLAGS = {}

# Raiz do orquestrador (…/orchestrator) e do pacote (a pasta AUTOMAÇÃO EDIÇÃO - WORKFLOW).
# Mantemos o NOME `LONGFORM_DIR` porque os engines copiados (ffmpeg_montagem, magnific_seam…)
# importam esse símbolo — aqui ele aponta para a RAIZ DESTE pacote, não para o long-form.
ORCH_DIR = Path(__file__).resolve().parent
LONGFORM_DIR = ORCH_DIR.parent          # raiz do pacote roteiro-auto (este projeto)
PKG_DIR = LONGFORM_DIR                   # alias legível
PROJECTS_DIR = LONGFORM_DIR / "projects"
PANEL_DIR = LONGFORM_DIR / "panel"       # copiado para cá
ASSETS_DIR = LONGFORM_DIR / "assets"
MATERIAIS_DIR = LONGFORM_DIR / "materiais"   # DROP-IN fixo por canal (teaser/book2/template_capa…)

# Instalação EXISTENTE do long-form — dela reaproveitamos o RENDERER (Remotion + node_modules)
# e o SIDECAR CapCut-TTS, sem duplicar aqui. Sobrescrevível por env LONGFORM_HOME.
LONGFORM_INSTALL = Path(os.environ.get(
    "LONGFORM_HOME", str(Path.home() / "AUTOM - VIDEOS DE ROMANCE (WORKFLOW)" / "longform")))
# O Remotion (compositions + node_modules) mora na instalação do long-form. Sobrescrevível.
REMOTION_DIR = Path(os.environ.get(
    "ROTEIRO_REMOTION_DIR", str(LONGFORM_INSTALL / "remotion")))

# Thumbs de referência do CANAL — a BASE DE ESTILO de QUALQUER thumb (Etapa 6).
# A usuária dropa aqui as capas que servem de padrão visual (composição, luz, estética);
# a Etapa 6 anexa todas como reference no Magnific. Sobrescrevível por env.
THUMB_REF_ESTILO_DIR = Path(os.environ.get(
    "LONGFORM_THUMB_REF_DIR", str(ASSETS_DIR / "thumb_ref_estilo")))

# Skills (slash commands) ficam no perfil do usuário, junto com as novela-en.
COMMANDS_DIR = Path.home() / ".claude" / "commands"


# ---------------------------------------------------------------------------
# Tabela de voz por canal (Etapa 3 — roteador de voz)
# ---------------------------------------------------------------------------
# Cada canal tem uma voz FEMININA (♀) e uma MASCULINA (♂). P1 usa só a ♀ (narração única);
# P2 alterna ♀/♂ pelos marcadores ✦ NOME. Fonte: config_alves.json + planejamento.
#   Todos os canais → ♂ Knightley (POV masculino unificado). ♀ = Joanne (Rowan é Knightley ♀=♂).
# 2026-07-09: TODOS os canais passaram a usar a MESMA voz masculina da Rowan (Knightley) — unifica o POV ♂ e simplifica.
CANAL_VOZ = {
    "kay":    {"f": "joanne",    "m": "knightley"},
    "rowan":  {"f": "knightley", "m": "knightley"},
    "lena":   {"f": "joanne",    "m": "knightley"},
    "spice":  {"f": "joanne",    "m": "knightley"},
    "selena": {"f": "joanne",    "m": "knightley"},
}
# nome da voz → id no CapCut-TTS. HOJE só a Joanne está capturada; Aramis/Knightley são
# PENDÊNCIA (o editor precisa capturar os ids — liste com capcut_tts.py --speakers).
# Sobrescrevível por env ROTEIRO_VOZ_<NOME>=<id> (ex.: ROTEIRO_VOZ_ARAMIS=...).
VOZ_IDS = {
    "joanne":    "XMWzAzwYm487GEok2uG2",  # resourceId 7374727186323870209
    "aramis":    "l1zE9xgNpUTaQCZzpNJa",  # resourceId 7478165790156395009 (capturada do CapCut Web)
    "knightley": "oxcpUoZsixp23oWJaFgp",  # resourceId 7374670931064525313 (capturada do CapCut Web)
}


def voz_do_canal(canal, genero="f"):
    """Devolve (nome_voz, id_capcut) da voz do canal p/ o gênero pedido ('f'|'m').

    Aplica override por env ROTEIRO_VOZ_<NOME>. Se o id ainda não foi capturado
    (Aramis/Knightley), devolve id="" — a Etapa 3 decide o fallback (Joanne) e loga o aviso.
    Canal desconhecido cai no default 'selena'."""
    ch = (canal or "").strip().lower()
    tabela = CANAL_VOZ.get(ch)
    if tabela is None:
        # Nome de List pode vir "sujo" (ex.: "Lena 2 (Spice)") — casa por substring.
        # 'spice' antes de 'lena' pra "Lena 2 (Spice)" cair em spice.
        for chave in ("rowan", "kay", "spice", "selena", "lena"):
            if chave in ch:
                tabela = CANAL_VOZ[chave]
                break
    tabela = tabela or CANAL_VOZ["selena"]
    nome = tabela.get(genero, tabela["f"])
    vid = os.environ.get("ROTEIRO_VOZ_" + nome.upper(), VOZ_IDS.get(nome, ""))
    return nome, vid


# Formato do vídeo POR CANAL (chave = slug do nome da List). O DEFAULT global já é HORIZONTAL
# (config.py → 1920x1080 16:9); este mapa é para OVERRIDES por canal. Hoje serve p/ dois fins:
#   (1) declarar um canal VERTICAL (9:16) — vertical virou opt-in; e
#   (2) âncora DEFENSIVA das irmãs horizontais (lena/rowan/kay): mesmo valor do default, mas
#       explícito pra elas seguirem horizontais mesmo se o default global for revertido.
# O pipeline aplica isto nas envs ROTEIRO_W/H/ASPECT antes das etapas visuais (4/5/6/7).
# Bug pego 2026-07-10 (card 84 Rowan saiu vertical): a raiz era o default global 9:16, não só
# a ausência no mapa — por isso o default virou horizontal. Ver decisoes-changelog.
CANAL_FORMATO = {
    "lena": (1920, 1080, "16:9"),   # Lena Principal = vídeo isca / YouTube (horizontal)
    "rowan": (1920, 1080, "16:9"),  # irmã da Lena (materiais herdados horizontais)
    "kay": (1920, 1080, "16:9"),    # irmã da Lena (materiais herdados horizontais)
}


def formato_do_canal(canal):
    """(W, H, ASPECT) do canal. Casa por slug EXATO do nome da List (evita 'Lena' pegar
    'Lena 2 (Spice)'); sem match, devolve os defaults globais das envs ROTEIRO_*."""
    slug = slugify(canal or "", maxlen=40)
    if slug in CANAL_FORMATO:
        return CANAL_FORMATO[slug]
    try:
        w = int(float(os.environ.get("ROTEIRO_W", "1920")))
        h = int(float(os.environ.get("ROTEIRO_H", "1080")))
    except ValueError:
        w, h = 1920, 1080
    return (w, h, os.environ.get("ROTEIRO_ASPECT", "16:9").strip() or "16:9")


def materiais_canal(canal):
    """Pasta de materiais drop-in do canal: materiais/<canal>/ (teaser/, book2/, template_capa/…).

    O editor abastece uma vez por canal; o pipeline reaproveita em todo card. Cria a árvore
    padrão se não existir (vazia — só a estrutura, pro editor saber onde largar cada coisa)."""
    base = MATERIAIS_DIR / slugify(canal or "sem-canal", maxlen=40)
    for sub in ("teaser", "take_p2", "book2", "cta", "padronizados", "template_capa", "qr",
                "detalhes", "sfx", "musicas"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


# Herança de materiais: canais que reusam os materiais FIXOS de outro (mesmos drop-ins — CTA,
# book2, capa, último-minuto). A diferença entre eles é só a VOZ (ver CANAL_VOZ). Decisão do
# editor 2026-07-09: Rowan e Kay usam os materiais da Lena. O item PRÓPRIO (se o editor largar
# um na pasta do canal) sempre vence; o herdado só preenche o que faltar.
# Override/desliga por env ROTEIRO_HERDA_<CANAL>=<base>  (ex.: ROTEIRO_HERDA_ROWAN= desliga).
MATERIAIS_HERDA = {"rowan": "lena", "kay": "lena"}


def canal_base_materiais(canal):
    """Slug do canal-base cujos materiais fixos <canal> herda (ou None se não herda)."""
    ch = slugify(canal or "", maxlen=40)
    alvo = MATERIAIS_HERDA.get(ch)
    if alvo is None:
        for chave, base in MATERIAIS_HERDA.items():
            if chave in ch:               # nome de List "sujo" (ex.: "Rowan EN")
                alvo = base
                break
    ov = os.environ.get("ROTEIRO_HERDA_" + ch.upper().replace("-", "_"))
    if ov is not None:                    # env presente (mesmo vazio) sobrescreve
        alvo = ov.strip() or None
    if alvo and slugify(alvo, maxlen=40) == ch:
        alvo = None                       # não herda de si mesmo
    return alvo


def materiais_dirs(canal):
    """Pastas de materiais na ORDEM de busca: [pasta do canal] (+ [pasta herdada] se houver).
    Quem lê material fixo deve varrer esta lista e usar o 1º que existir (próprio vence)."""
    dirs = [materiais_canal(canal)]
    base = canal_base_materiais(canal)
    if base:
        dirs.append(materiais_canal(base))
    return dirs


# Modelos por canal (arquivos que o editor anexa 1x). Guardados em materiais/<canal>/modelos.json.
#   capa_ref  = vídeo de troca de capítulo de referência (a automação replica a animação/formato/fonte)
#   cta_final / resumo_p2 = agora só FALLBACK. Desde 2026-07-09 (resumo_cta.py) resumo P2 e CTA são
#     GERADOS reusando os clipes do teaser: CTA usa a base fixa materiais/<canal>/cta/cta_base.mp4
#     (só o áudio é fixo); resumo P2 = TTS do bloco-gancho do roteiro. Estes só entram se a geração falhar.
# (capa_bg/capa_fonte foram aposentados: fundo é dinâmico por capítulo e a fonte é embutida.)
MODELOS_CHAVES = ("capa_ref", "cta_final", "resumo_p2")


def modelos_arquivo(canal):
    return materiais_canal(canal) / "modelos.json"


def _ler_modelos_bruto(canal):
    """Modelos SÓ do próprio canal (sem herança). Base p/ leitura e gravação."""
    p = modelos_arquivo(canal)
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return {k: d.get(k, "") for k in MODELOS_CHAVES}
        except (OSError, ValueError):
            pass
    return {k: "" for k in MODELOS_CHAVES}


def ler_modelos(canal):
    """Dict {capa_ref, cta_final, resumo_p2} com os caminhos escolhidos (ou '').
    Chave em branco herda do canal-base (Rowan/Kay ← Lena; ver canal_base_materiais)."""
    d = _ler_modelos_bruto(canal)
    base = canal_base_materiais(canal)
    if base:
        bd = _ler_modelos_bruto(base)
        for k in MODELOS_CHAVES:
            if not d.get(k):
                d[k] = bd.get(k, "")
    return d


def salvar_modelo(canal, chave, caminho):
    """Grava (referencia, não copia) o caminho de um modelo do canal em modelos.json."""
    if chave not in MODELOS_CHAVES:
        raise ValueError("chave de modelo inválida: %s" % chave)
    d = _ler_modelos_bruto(canal)         # não persiste os valores herdados no json do canal
    d[chave] = str(caminho or "")
    modelos_arquivo(canal).write_text(
        json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def garantir_gpu_preferencia(log=None):
    """Força o Windows a rodar o Chromium do Remotion na GPU DEDICADA (NVIDIA), não na integrada.

    PROBLEMA: nesta máquina há 2 GPUs (NVIDIA RTX 2060 + AMD Radeon integrada). Sem dizer nada,
    o Windows roda o `chrome-headless-shell.exe` (que o Remotion usa pra desenhar cada frame) na
    GPU integrada fraca ou em SOFTWARE — ~3x mais lento. A correção é gravar a preferência de GPU
    do Windows (HKCU\\...\\DirectX\\UserGpuPreferences) com GpuPreference=2 ("alto desempenho" =
    a dedicada) para os .exe do Remotion. É o MESMO ajuste que aparece em
    Configurações > Sistema > Tela > Gráficos.

    Por que no código (e não só uma vez na mão): quando o Remotion ATUALIZA, o caminho do
    chrome-headless-shell.exe muda (vai pra outra pasta em node_modules/.remotion/...) e a
    preferência se perde. Rodar isto antes de cada render deixa a otimização AUTO-CURÁVEL.

    Best-effort: NUNCA derruba o render — só loga. No-op fora do Windows ou se não achar os .exe.
    GpuPreference: 0=Windows decide, 1=economia (integrada), 2=alto desempenho (dedicada).
    """
    if os.name != "nt":
        return
    def _log(msg):
        if log:
            log(msg)
    try:
        import winreg
    except Exception:
        return
    # Acha o chrome-headless-shell.exe que o Remotion baixou (caminho varia por versão) + o node.
    alvos = []
    cache = REMOTION_DIR / "node_modules" / ".remotion"
    if cache.is_dir():
        alvos += [str(p) for p in cache.rglob("chrome-headless-shell.exe")]
    node = shutil.which("node") or shutil.which("node.exe")
    if node:
        alvos.append(str(Path(node).resolve()))
    if not alvos:
        return
    chave = r"SOFTWARE\Microsoft\DirectX\UserGpuPreferences"
    valor = "GpuPreference=2;"
    aplicados = 0
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, chave, 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
            for exe in alvos:
                try:
                    atual, _ = winreg.QueryValueEx(k, exe)
                except FileNotFoundError:
                    atual = None
                if atual != valor:
                    winreg.SetValueEx(k, exe, 0, winreg.REG_SZ, valor)
                    aplicados += 1
    except Exception as e:
        _log("    [gpu] aviso: não consegui gravar a preferência de GPU (%s)." % e)
        return
    if aplicados:
        _log("    [gpu] preferência 'alto desempenho' (NVIDIA) aplicada a %d executável(is) do Remotion." % aplicados)
    else:
        _log("    [gpu] preferência de GPU já estava correta (NVIDIA).")

# Reuso de scripts mecânicos que JÁ existem no projeto TINAGO (Whisper etc.).
# Sobrescrevível por env var caso o usuário mova a pasta.
TINAGO_DIR = Path(os.environ.get("TINAGO_DIR", str(Path.home() / "TINAGO AUTOMAÇÃO")))
WHISPER_SCRIPT = TINAGO_DIR / "gerar-srt-en.py"

AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
IMG_EXTS = {".jpeg", ".jpg", ".png", ".webp", ".bmp"}

# Idiomas suportados do CONTEÚDO narrado.
_PT_ALIASES = {"pt", "pt-br", "ptbr", "pt_br", "br", "portugues", "português"}


def idioma():
    """Idioma do CONTEÚDO narrado (roteiro + narração + legendas).

    'en' (default — conversão original do canal) | 'pt' (MODO TESTE: vídeo em
    português, pra equipe avaliar a HISTÓRIA antes de produzir a versão final EN).
    Ligado pela caixa "Vídeo em português" da GUI -> env `LONGFORM_IDIOMA`.

    ATENÇÃO: os prompts de imagem (Etapas 5/7) ficam SEMPRE em inglês — é direção
    visual pro Magnific, não muda com o idioma da narração. Só roteiro/narração/legenda
    trocam de língua."""
    v = os.environ.get("LONGFORM_IDIOMA", "en").strip().lower()
    return "pt" if v in _PT_ALIASES else "en"


def nome_idioma(cod=None):
    """Rótulo legível do idioma ('português' / 'inglês') p/ logs e prompts."""
    return "português" if (cod or idioma()) == "pt" else "inglês"


# ---------------------------------------------------------------------------
# Detecção de idioma do roteiro (trava anti-narração-em-português)
# ---------------------------------------------------------------------------
# A esteira NÃO gera roteiro — ela baixa o Doc EN do card (s1_clickup) e o TTS narra
# o texto LITERAL. Se um Doc em PORTUGUÊS escapar (ex.: a versão "Roteiro em português"
# do card, ou um roteiro.txt semeado à mão), a narração sai em PT e a legenda (Whisper
# forçado a en) vira inglês destroçado. Decisão do editor 2026-07-09: TRAVAR e exigir o
# Doc EN (nunca traduzir automático). Estas heurísticas puras (sem dependência) detectam
# PT com folga em texto longo, sendo conservadoras pra NÃO reprovar um roteiro EN legítimo.
_PT_STOP = re.compile(
    r"\b(n[aã]o|que|com|uma?|el[ea]s?|para|porque|ent[aã]o|mas|seu|sua|foi|era|tinha|"
    r"dele|dela|voc[eê]|isso|quando|muito|tamb[eé]m|dos|das|meu|minha|nada|tudo|aquele|"
    r"aquela|estava|ser[aá]|coisa|sobre|at[eé]|depois|ainda|sempre|nunca)\b", re.I)
_EN_STOP = re.compile(
    r"\b(the|and|was|were|her|his|with|that|this|they|have|had|been|would|could|she|"
    r"he|you|your|when|what|there|their|about|which|only|just|of|to|for|from|into)\b", re.I)


def idioma_do_texto(texto):
    """Devolve 'pt' | 'en' | 'indefinido' pelo texto do roteiro (heurística de stopwords +
    caracteres exclusivos do PT). Conservadora: só crava 'pt' quando o PT domina com folga,
    pra jamais reprovar um roteiro EN legítimo (bloquear a entrega à toa é o erro caro)."""
    if not texto or not texto.strip():
        return "indefinido"
    baixo = texto.lower()
    pt = len(_PT_STOP.findall(baixo))
    en = len(_EN_STOP.findall(baixo))
    ptchars = sum(baixo.count(c) for c in "ãõç")   # ã/õ/ç ~ inexistentes em inglês
    if (pt >= 5 and pt > en) or (ptchars >= 8 and pt > en):
        return "pt"
    if en >= 5 and en > pt:
        return "en"
    return "indefinido"


def parece_portugues(texto):
    """True se o roteiro parece estar em PORTUGUÊS (trava anti-narração-PT). Só dispara em
    MODO INGLÊS (idioma()=='en'); no MODO TESTE (LONGFORM_IDIOMA=pt) o PT é intencional."""
    return idioma() == "en" and idioma_do_texto(texto) == "pt"


class ErroPipeline(Exception):
    """Erro de etapa do pipeline (mensagem amigável para a GUI/CLI)."""
    pass


def thumbs_ref_estilo():
    """Imagens de referência de ESTILO do canal (base de qualquer thumb da Etapa 6).

    Lê THUMB_REF_ESTILO_DIR (longform/assets/thumb_ref_estilo/ por padrão). Devolve os
    caminhos ABSOLUTOS ordenados das imagens encontradas, ou [] se a pasta não existir/vazia
    (nesse caso a thumb sai só pelo prompt + direção de arte, sem quebrar nada)."""
    d = THUMB_REF_ESTILO_DIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXTS)


# ---------------------------------------------------------------------------
# Descoberta de executáveis (porta de novela_orquestra.achar_claude/achar_python)
# ---------------------------------------------------------------------------

def achar_claude():
    """Acha o executável do Claude Code. Retorna a lista-prefixo do comando."""
    for nome in ("claude", "claude.cmd", "claude.exe"):
        p = shutil.which(nome)
        if p:
            if p.lower().endswith((".cmd", ".bat")):
                return ["cmd", "/c", p]
            return [p]
    raise ErroPipeline(
        "Executável 'claude' não encontrado no PATH. Instale/configure o Claude Code "
        "(o mesmo que você usa no terminal)."
    )


def achar_python():
    """Prefere o launcher 'py -3'; senão, o python atual."""
    if shutil.which("py"):
        return ["py", "-3"]
    return [sys.executable]


def achar_ffmpeg():
    """Acha o executável do ffmpeg. Retorna o caminho (string)."""
    for nome in ("ffmpeg", "ffmpeg.exe"):
        p = shutil.which(nome)
        if p:
            return p
    raise ErroPipeline(
        "ffmpeg não encontrado no PATH. Instale o FFmpeg (https://ffmpeg.org/download.html) "
        "e adicione a pasta bin/ ao PATH — a montagem híbrida (Etapa 8) depende dele."
    )


# ---------------------------------------------------------------------------
# Slug + layout de projeto
# ---------------------------------------------------------------------------

def slugify(texto, maxlen=60):
    """'Alpha King: His Secret Heir' -> 'alpha-king-his-secret-heir'."""
    if not texto:
        return "sem-titulo"
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    if len(t) > maxlen:
        t = t[:maxlen].rstrip("-")
    return t or "sem-titulo"


class Projeto:
    """Aponta para projects/<slug>/ e centraliza os nomes dos artefatos por etapa."""

    def __init__(self, base):
        self.dir = Path(base).resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "thumbs").mkdir(exist_ok=True)
        (self.dir / "images").mkdir(exist_ok=True)
        (self.dir / "referencias").mkdir(exist_ok=True)
        (self.dir / "covers").mkdir(exist_ok=True)
        (self.dir / "out").mkdir(exist_ok=True)

    # --- artefatos (a ordem é a ordem do pipeline) ---
    @property
    def source(self):           return self.dir / "source.json"            # Etapa 1
    @property
    def thumb_ref(self):        return self.dir / "thumb_ref.png"          # Etapa 1 (anexo do card)
    @property
    def roteiro(self):          return self.dir / "roteiro.txt"            # Etapa 2
    @property
    def roteiro_tts(self):      return self.dir / "roteiro_tts.txt"        # Etapa 4 (roteiro humanizado p/ TTS)
    @property
    def roteiro_docx(self):     return self.dir / "roteiro.docx"           # Etapa 2 (entrega p/ equipe)
    @property
    def roteiro_pdf(self):      return self.dir / "roteiro.pdf"            # Etapa 2 (entrega p/ equipe)
    @property
    def validacao(self):        return self.dir / "roteiro_validacao.json" # Etapa 3
    @property
    def narration_mp3(self):    return self.dir / "narration.mp3"          # Etapa 4
    @property
    def narration_raw(self):    return self.dir / "narration_raw.mp3"      # Etapa 4 (TTS cru, pré-otimização de pausa)
    @property
    def pausas_flag(self):      return self.dir / ".pausas_otimizadas"     # Etapa 4 (marca: pausas já aparadas)
    @property
    def narration_srt(self):    return self.dir / "narration.srt"          # Etapa 4
    @property
    def style_bible(self):      return self.dir / "style_bible.txt"        # Etapa 5 (= CHARACTER BIBLE)
    @property
    def prompts_referencia(self): return self.dir / "prompts_referencia.txt" # Etapa 5 (fichas de personagem)
    @property
    def prompts_thumb(self):    return self.dir / "prompts_thumbnail.txt"  # Etapa 5
    @property
    def personagens_dir(self):  return self.dir / "personagens"            # Etapa 2 (fotos de personagem que a EDITORA larga na GUI, junto do teaser)
    @property
    def referencias_dir(self):  return self.dir / "referencias"            # Etapa 6 (PNG das fichas)
    @property
    def referencias_json(self): return self.dir / "referencias.json"       # Etapa 6 (mapa [Character N]->Library id)
    @property
    def thumbs_dir(self):       return self.dir / "thumbs"                 # Etapa 6
    @property
    def thumb_base_gpt2(self):  return self.thumbs_dir / "_base_gpt2.png"  # Etapa 6 (base leve GPT-2 p/ refino)
    @property
    def thumb_status(self):     return self.thumbs_dir / "thumb_status.json" # Etapa 6 (status da geração: moderado?)
    @property
    def thumb_qa(self):         return self.thumbs_dir / "thumb_qa.json"   # Etapa 6 (veredito do QA do Claude/Opus)
    @property
    def thumb_selected(self):   return self.dir / "thumb_selected.png"     # Gate 2
    @property
    def prompts_imagens(self):  return self.dir / "prompts_imagens.txt"    # Etapa 7
    @property
    def images_dir(self):       return self.dir / "images"                 # Etapa 5 (corpo)
    @property
    def img_qa(self):           return self.images_dir / "img_qa.json"     # Etapa 5 (veredito do QA visual das imagens)
    @property
    def character_bible(self):  return self.dir / "character_bible.txt"    # Etapa 2 (personagens)
    @property
    def prompts_capas(self):    return self.dir / "prompts_capas.txt"      # Etapa 4 (títulos das capas)
    @property
    def covers_dir(self):       return self.dir / "covers"                 # Etapa 6 (cap<c>.mp4)
    @property
    def mapping(self):          return self.dir / "mapping.json"           # Etapa 7
    @property
    def base_mp4(self):         return self.dir / "out" / "base.mp4"       # Etapa 8 (FFmpeg: Ken Burns+áudio)
    @property
    def final_mp4(self):        return self.dir / "out" / "final.mp4"      # Etapa 8
    @property
    def render_meta(self):      return self.dir / "out" / ".render.json"   # Etapa 8 (assinatura do render: motor/fps/legenda)
    @property
    def gate1_flag(self):       return self.dir / ".gate1_aprovado"        # Gate 1 (marca de aprovação)
    @property
    def thumb_anexada_flag(self): return self.dir / ".thumb_anexada_clickup" # Gate 2 (capa já anexada no card)
    @property
    def relight_flag(self):     return self.dir / ".thumb_relit"          # Etapa 7 (relight da capa já rodou)

    def existe(self, p):
        p = Path(p)
        return p.exists() and (p.is_dir() or p.stat().st_size > 0)

    # --- assinatura do render (motor/fps/legenda) p/ re-render automático sem gastar crédito ---
    @staticmethod
    def assinatura_render():
        """Assinatura do FORMATO de render ATUAL, lida do ambiente (= longform.env já aplicado).

        Captura só o que muda a SAÍDA do vídeo e justifica re-renderizar: o motor
        (dynamic/hybrid/ffmpeg/remotion), o fps e se a legenda está ligada. Os defaults
        espelham os de s8_montagem.py / build-mapping.py (engine=dynamic, fps=30, legenda ligada)
        para a assinatura bater com o que a Etapa 8 realmente produz.
        """
        eng = (os.environ.get("LONGFORM_RENDER_ENGINE", "dynamic") or "dynamic").strip().lower()
        fps = (os.environ.get("LONGFORM_FPS", "30") or "30").strip()
        cap = (os.environ.get("LONGFORM_CAPTIONS", "1") or "1").strip().lower() \
            in {"1", "true", "yes", "sim", "on"}
        return {"v": 1, "engine": eng, "fps": fps, "captions": cap}

    def gravar_render_meta(self):
        """Grava a assinatura do render ATUAL ao lado do final.mp4 (chamado ao concluir a Etapa 8).

        É esse marcador que permite ao 'Continuar' saber que um vídeo já está no FORMATO novo
        e NÃO precisa re-renderizar. Best-effort: falha de escrita nunca derruba a Etapa 8."""
        try:
            self.render_meta.parent.mkdir(parents=True, exist_ok=True)
            self.render_meta.write_text(json.dumps(self.assinatura_render()), encoding="utf-8")
        except OSError:
            pass

    def render_desatualizado(self):
        """True se existe um final.mp4 cujo FORMATO de render difere do atual (motor/fps/legenda).

        Vídeos antigos (sem o marcador out/.render.json) ou renderizados num motor/fps diferente
        contam como DESATUALIZADOS → o 'Continuar' re-renderiza SÓ a Etapa 8 (FFmpeg, local, sem
        custo de crédito), reaproveitando roteiro/narração/imagens. Sem final.mp4 não há o que
        desatualizar (devolve False — a etapa só está 'pendente' por não existir ainda)."""
        if not self.existe(self.final_mp4):
            return False
        if not self.render_meta.is_file():
            return True
        try:
            gravado = json.loads(self.render_meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        return gravado != self.assinatura_render()

    def etapa_pronta(self, n):
        """True se o artefato-âncora da etapa n já existe (base do 'Continuar')."""
        if n == 1: return self.existe(self.source)
        if n == 2: return self.existe(self.roteiro)
        if n == 3: return self.existe(self.validacao)
        if n == 4: return self.existe(self.narration_mp3) and self.existe(self.narration_srt)
        if n == 5: return self.existe(self.style_bible) and self.existe(self.prompts_thumb)
        if n == 6: return bool(list(self.thumbs_dir.glob("thumb_*.png")))
        if n == 7: return bool(list(self.images_dir.glob("img_*.png")))
        # Etapa 8: pronta SÓ se o final.mp4 existe E está no formato de render atual.
        # Se o motor/fps mudou (ex.: dynamic 60fps -> hybrid 30fps), conta como PENDENTE
        # para o 'Continuar' re-renderizar só o vídeo (sem refazer nada pago).
        if n == 8: return self.existe(self.final_mp4) and not self.render_desatualizado()
        return False

    def etapas_pendentes(self, etapas):
        """Das `etapas` pedidas, devolve só as que ainda não estão prontas (em ordem)."""
        return [n for n in sorted(etapas) if not self.etapa_pronta(n)]

    # --- limpeza p/ "Refazer" ---
    def _artefatos_etapa(self, n):
        """Lista os arquivos/pastas que devem ser apagados ao 'refazer' a etapa n.
        Preserva sempre source.json e thumb_ref.png (vêm do ClickUp e são caros de
        re-baixar — se quiser refazer a etapa 1, apague à mão)."""
        if n == 1: return [self.source]
        if n == 2: return [self.roteiro, self.roteiro_docx, self.roteiro_pdf]
        if n == 3: return [self.validacao, self.gate1_flag]
        if n == 4: return [self.narration_mp3, self.narration_srt, self.roteiro_tts,
                           self.narration_raw, self.pausas_flag]
        if n == 5: return [self.style_bible, self.prompts_referencia, self.prompts_thumb,
                           self.referencias_dir, self.referencias_json]
        if n == 6: return [self.thumbs_dir, self.thumb_selected]
        if n == 7: return [self.prompts_imagens, self.images_dir]
        if n == 8: return [self.mapping, self.base_mp4, self.final_mp4]
        return []

    def limpar_etapas(self, etapas):
        """Apaga os artefatos das etapas pedidas para forçar regeneração.
        Pastas (thumbs/, images/, referencias/) são esvaziadas e recriadas vazias.
        Retorna a lista de paths que foram apagados (para log)."""
        apagados = []
        for n in sorted(etapas):
            for alvo in self._artefatos_etapa(n):
                p = Path(alvo)
                if not p.exists():
                    continue
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                    p.mkdir(parents=True, exist_ok=True)
                else:
                    try:
                        p.unlink()
                    except OSError:
                        continue
                apagados.append(p)
        return apagados


def projeto_por_slug(slug):
    return Projeto(PROJECTS_DIR / slug)


def projeto_mais_recente():
    """Slug do projeto modificado mais recentemente (ignora os _tmp_). None se não houver."""
    if not PROJECTS_DIR.is_dir():
        return None
    pastas = [p for p in PROJECTS_DIR.iterdir()
              if p.is_dir() and not p.name.startswith("_tmp_")]
    if not pastas:
        return None
    return max(pastas, key=lambda p: p.stat().st_mtime).name


def achar_audio(pasta):
    """narration.<audio> (preferido) ou o 1º arquivo de áudio da pasta."""
    pasta = Path(pasta)
    if not pasta.is_dir():
        return None
    for p in pasta.iterdir():
        if p.is_file() and p.stem.lower() == "narration" and p.suffix.lower() in AUDIO_EXTS:
            return p
    cands = [p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    return sorted(cands)[0] if cands else None


# ---------------------------------------------------------------------------
# Parse de SRT (para build-mapping e contagem de palavras/segmentos)
# ---------------------------------------------------------------------------

_SRT_TIME = re.compile(
    r"(\d\d):(\d\d):(\d\d)[,.](\d{3})\s*-->\s*(\d\d):(\d\d):(\d\d)[,.](\d{3})"
)


def _tc(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(caminho):
    """Lê um .srt e devolve [(idx, start_s, end_s, texto), ...] em ordem."""
    texto = Path(caminho).read_text(encoding="utf-8", errors="replace")
    blocos = re.split(r"\n\s*\n", texto.strip())
    cues = []
    for b in blocos:
        linhas = [l for l in b.splitlines() if l.strip() != ""]
        if not linhas:
            continue
        # acha a linha de tempo (pode ou não haver índice antes)
        tline = None
        ti = 0
        for i, l in enumerate(linhas):
            if _SRT_TIME.search(l):
                tline, ti = l, i
                break
        if tline is None:
            continue
        m = _SRT_TIME.search(tline)
        start = _tc(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _tc(m.group(5), m.group(6), m.group(7), m.group(8))
        fala = " ".join(linhas[ti + 1:]).strip()
        cues.append((len(cues) + 1, start, end, fala))
    return cues


def contar_palavras(texto):
    return len(re.findall(r"\b[\w'-]+\b", texto or ""))
