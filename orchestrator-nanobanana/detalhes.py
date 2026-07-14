# -*- coding: utf-8 -*-
"""detalhes.py — clipes FIXOS de detalhe por canal, encaixados na isca (P1).

As "peças de detalhe" são vídeos PRONTOS (com áudio próprio) que a editora larga UMA vez por
canal e que NÃO mudam entre vídeos — ao contrário do resumo/CTA/minuto-final, que são GERADOS
reusando os takes do teaser. Cada canal tem os seus (Kay/Rowan têm clipes diferentes da Lena),
mas Rowan/Kay ainda herdam os da Lena quando faltar uma peça (ver common.materiais_dirs).

Ordem na isca P1 (montagem_vertical.construir):

    TEASER → [intro_pos_teaser] → capítulos → [intro_book_02] → RESUMO → CTA →
        [aviso_clone] → [tutorial_plataforma] → MINUTO FINAL

As peças entre colchetes são as fixas deste módulo; RESUMO/CTA/MINUTO FINAL continuam gerados.
Peça sem arquivo (ex.: AVISO DE CLONE ainda não subiu, Spice sem tutorial) é simplesmente
PULADA — o rabo só encolhe.

Onde ficam (cadeia de busca, o 1º que existir vence):

    projects/<slug>/detalhes/      (override por-vídeo — sempre vence)
  > materiais/<canal>/detalhes/    (drop-in fixo do canal)
  > materiais/<canal-base>/detalhes/   (herança: Rowan/Kay ← Lena)
  > assets/detalhes/               (PADRÃO GLOBAL — vale p/ TODA categoria; mesmo p/ todos)

O casamento é por NOME NORMALIZADO do arquivo (sem acento/pontuação/caixa), então
"INTRO BOOK 02.mp4", "intro_book2.mov" e "Intro Book 2.mp4" caem todos na mesma peça.

O fallback global `assets/detalhes/` (2026-07-13, editora) é onde mora a peça que é IGUAL p/ todos
os canais — o TUTORIAL PLATAFORMA é padrão da casa (mesmo vídeo em toda categoria), então basta
UM arquivo lá e todo canal SEM o seu próprio o herda. Um clipe por-canal (materiais/<canal>) ainda
VENCE o global (own > herdado > global), pra o canal que quiser um detalhe exclusivo.
"""

import re
import unicodedata
from pathlib import Path


def _norm(s):
    """Nome comparável: tira acento, deixa minúsculo e remove tudo que não for [a-z0-9]."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


# key da peça -> assinaturas normalizadas que casam com o nome do arquivo (sem extensão).
# A ORDEM das keys aqui é a ordem lógica na timeline; a montagem decide o posicionamento.
# Assinaturas pensadas pros nomes que a editora usa ("INTRO BOOK 02", "TUTORIAL PLATAFORMA"…)
# e variações plausíveis. Casa por substring nos dois sentidos (nome⊇assinatura ou vice-versa).
PECAS = {
    "intro_pos_teaser":     ("intropostease", "introposteaser", "postease", "posteaser",
                             "aposteaser", "aposteaserintro"),
    "intro_book_02":        ("introbook02", "introbook2", "book02", "book2", "introlivro2",
                             "introlivro02"),
    # "clone" e "plágio" são a MESMA peça (a editora chama de aviso de plágio; o código a
    # nomeia aviso_clone). Aceita os dois nomes p/ o arquivo do editor resolver de qualquer jeito.
    "aviso_clone":          ("avisodeplagio", "avisoplagio", "plagio",
                             "avisodeclone", "avisoclone", "clonewarning", "avisoclonagem",
                             "clone"),
    "tutorial_plataforma":  ("tutorialplataforma", "tutorialplatform", "tutorialdaplataforma",
                             "tutorial", "plataforma"),
}

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


def _casa(stem, sigs):
    n = _norm(stem)
    if not n:
        return False
    return any(sig and (sig in n or n in sig) for sig in sigs)


def dirs_busca(proj, mat_dirs):
    """Pastas onde procurar as peças, na ORDEM de prioridade:
    projects/<slug>/detalhes/ (por-vídeo) > materiais/<canal>/detalhes/ (+ herdada) >
    assets/detalhes/ (PADRÃO GLOBAL — última, menor prioridade; a peça igual p/ todos)."""
    dirs = [Path(proj.dir) / "detalhes"]
    for d in mat_dirs:
        dirs.append(Path(d) / "detalhes")
    dirs.append(Path(__file__).resolve().parent.parent / "assets" / "detalhes")
    return dirs


def achar(proj, mat_dirs, key, exts=VIDEO_EXTS):
    """Path do clipe fixo da peça `key`, varrendo dirs_busca na ordem. None se nenhuma casar.
    O 1º diretório com um arquivo que case vence (por-vídeo > próprio > herdado)."""
    sigs = PECAS.get(key, ())
    if not sigs:
        return None
    for d in dirs_busca(proj, mat_dirs):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix.lower() in exts and _casa(p.stem, sigs):
                return p
    return None


def inventario(proj, mat_dirs):
    """{key: Path|None} de todas as peças — útil pra logar o que a montagem encontrou."""
    return {k: achar(proj, mat_dirs, k) for k in PECAS}
