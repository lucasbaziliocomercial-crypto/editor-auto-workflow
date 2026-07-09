# -*- coding: utf-8 -*-
"""roteiro_estrutura.py — parser do roteiro.txt (formato do Google Doc) para a esteira roteiro-auto.

O roteiro que a Etapa 1 baixa (Tab 1 = P1) tem SEMPRE este formato:

    <gancho falado>            <- teaser: o texto ANTES de "Chapter 1" (é narrado; o teaser
    ...                            de vídeo drop-in é sincronizado à DURAÇÃO desse gancho)
    Chapter 1 — The Empty Chair   <- linha de CABEÇALHO (NÃO é narrada; vira a capa do cap.)
    <prosa do capítulo 1>
    Chapter 2 — Guest of Honor
    <prosa do capítulo 2>
    ...
    END OF PART 1              <- marcador (NÃO narrado); o que vem depois é o gancho da P2

Este módulo é a FONTE DA VERDADE da estrutura, usada por:
  - s3_narracao  : monta o texto que o TTS realmente narra (gancho + prosa, SEM cabeçalhos)
  - s4_prompts   : deriva os prompts de imagem por capítulo e os títulos das capas
  - s6_capas     : os títulos de capa (um por Chapter N)
  - s7_montagem  : mapeia onde cada capítulo começa na narração (ancorado na 1ª frase da prosa)

Tudo é determinístico e testável (Python puro). Nada de LLM aqui.
"""

import re

# "Chapter 1 — Título", "Chapter 1 - Título", "Chapter 1: Título", com/sem ** de markdown,
# e tolerando o travessão (—), hífen (-) ou dois-pontos como separador.
_RE_CAP = re.compile(
    r"^\**\s*Chapter\s+(\d+)\s*(?:[—:\-]\s*(.*?))?\s*\**\s*$",
    re.IGNORECASE,
)
# Fim da Parte 1 — tudo depois disso é o gancho da P2 (não entra no vídeo da P1).
_RE_FIM = re.compile(r"^\**\s*(END OF PART\s*\d*|FIM DA PARTE\s*\d*)\b", re.IGNORECASE)


def _cortar_no_fim(texto):
    """Devolve só o miolo da P1 (corta em 'END OF PART 1' se existir)."""
    linhas = texto.replace("\r\n", "\n").split("\n")
    out = []
    for ln in linhas:
        if _RE_FIM.match(ln.strip()):
            break
        out.append(ln)
    return "\n".join(out)


def parse_roteiro(texto):
    """Parseia o roteiro.txt (P1) em estrutura.

    Devolve {"hook": str, "chapters": [{"n": int, "titulo": str, "corpo": str,
             "primeira_frase": str}]}.

    - hook = texto ANTES do primeiro "Chapter N" (o gancho falado). Pode ser "".
    - chapters em ordem; "corpo" é a prosa do capítulo (sem a linha de cabeçalho).
    - primeira_frase = âncora curta (até ~12 palavras) da 1ª frase da prosa — usada pela
      montagem pra achar o começo do capítulo na SRT (que não tem os cabeçalhos).
    """
    miolo = _cortar_no_fim(texto)
    linhas = miolo.split("\n")

    hook_linhas = []
    chapters = []
    atual = None  # capítulo em construção
    for ln in linhas:
        m = _RE_CAP.match(ln.strip())
        if m:
            if atual is not None:
                chapters.append(atual)
            atual = {"n": int(m.group(1)), "titulo": (m.group(2) or "").strip(), "corpo_linhas": []}
        else:
            if atual is None:
                hook_linhas.append(ln)
            else:
                atual["corpo_linhas"].append(ln)
    if atual is not None:
        chapters.append(atual)

    for c in chapters:
        corpo = "\n".join(c.pop("corpo_linhas")).strip()
        c["corpo"] = corpo
        c["primeira_frase"] = _primeira_frase(corpo)

    return {"hook": "\n".join(hook_linhas).strip(), "chapters": chapters}


def _primeira_frase(corpo, max_palavras=12):
    """Âncora textual do começo do capítulo: a 1ª frase, aparada em `max_palavras` palavras.

    Usada pela montagem pra localizar o início do capítulo na SRT (que não tem cabeçalho).
    Normaliza aspas curvas e espaços pra casar melhor com a transcrição do Whisper."""
    if not corpo:
        return ""
    # 1ª frase (até o primeiro . ! ? seguido de espaço) ou a 1ª linha.
    m = re.search(r"(.+?[.!?])(?:\s|$)", corpo, re.DOTALL)
    frase = (m.group(1) if m else corpo.splitlines()[0]).strip()
    palavras = frase.split()
    if len(palavras) > max_palavras:
        frase = " ".join(palavras[:max_palavras])
    return frase


def texto_narracao(texto):
    """Texto que o TTS deve narrar: o gancho + a prosa de todos os capítulos, SEM os
    cabeçalhos 'Chapter N' e SEM nada depois de 'END OF PART 1'.

    Um parágrafo em branco separa gancho e capítulos (o chunker do TTS é paragraph-aware)."""
    est = parse_roteiro(texto)
    partes = []
    if est["hook"]:
        partes.append(est["hook"])
    for c in est["chapters"]:
        if c["corpo"]:
            partes.append(c["corpo"])
    return "\n\n".join(partes).strip()


def titulos_capas(texto):
    """Lista de títulos de capa, um por capítulo (na ordem). Ex.: ['The Empty Chair', ...].

    Cai no rótulo 'Chapter N' se o capítulo não trouxe título depois do separador."""
    est = parse_roteiro(texto)
    out = []
    for c in est["chapters"]:
        out.append(c["titulo"] or ("Chapter %d" % c["n"]))
    return out


if __name__ == "__main__":
    import sys
    from pathlib import Path
    p = Path(sys.argv[1] if len(sys.argv) > 1 else "roteiro.txt")
    est = parse_roteiro(p.read_text(encoding="utf-8", errors="replace"))
    print("hook: %d chars" % len(est["hook"]))
    for c in est["chapters"]:
        print("  Cap %d — %s | %d chars | ancora: %r"
              % (c["n"], c["titulo"], len(c["corpo"]), c["primeira_frase"]))
    print("\ntexto_narracao: %d chars" % len(texto_narracao(
        p.read_text(encoding="utf-8", errors="replace"))))
