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
# Cabeçalho da Parte 2 (tolera emoji/markdown à esquerda): "🥰 PARTE 2", "PART 2", "**PARTE 2**".
_RE_PARTE2 = re.compile(r"^\W*(?:PARTE|PART)\s*2\b", re.IGNORECASE)
# Cabeçalho de capítulo em EN ou PT (usado só p/ delimitar o bloco do resumo).
_RE_CAP_ANY = re.compile(r"^\**\s*(?:Chapter|Cap[íi]tulo)\s+\d+\b", re.IGNORECASE)
# Marcador de voz de POV (1ª pessoa) que às vezes precede a prosa — não deve ser narrado.
_RE_MARCADOR_VOZ = re.compile(r"^\s*✦")


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


def _primeira_frase(corpo, min_palavras=12, max_palavras=16):
    """Âncora textual do começo do capítulo, usada pela montagem pra localizar o início do
    capítulo na SRT (que não tem cabeçalho).

    Acumula FRASES INTEIRAS a partir do começo até juntar ~`min_palavras` palavras (aparando em
    `max_palavras`). Antes pegava só a 1ª frase: quando ela é curta/genérica (ex.: "I knew she'd
    come down.", 5 palavras), NÃO ancorava sozinha na narração e o capítulo caía na estimativa
    proporcional (a capa/troca entrava minutos atrasada — card 250 P2, cap 4). Juntar a 2ª frase
    dá o trecho distintivo ("...I'd been in the library since before dawn") que o matcher
    deslizante (montagem_vertical._casar) consegue casar. Normaliza p/ bater com o Whisper."""
    if not corpo:
        return ""
    # frases a partir do início (cada uma até . ! ?), ou a 1ª linha se não houver pontuação.
    frases = re.findall(r".+?[.!?](?=\s|$)", corpo, re.DOTALL) or [corpo.splitlines()[0]]
    acc = []
    for fr in frases:
        acc.extend(fr.split())
        if len(acc) >= min_palavras:
            break
    return " ".join(acc[:max_palavras]).strip()


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


def _limpar_bloco(linhas):
    """Junta as linhas de um bloco de prosa em texto narrável: descarta cabeçalhos
    (Chapter/Capítulo/PARTE/END OF PART) e marcadores de voz (✦, [M]…[/M]) que porventura
    caiam no bloco, colapsa linhas em branco. Devolve '' se sobrar nada."""
    out = []
    for ln in linhas:
        s = ln.strip()
        if not s:
            if out and out[-1] != "":
                out.append("")
            continue
        if (_RE_CAP_ANY.match(s) or _RE_PARTE2.match(s) or _RE_FIM.match(s)
                or _RE_MARCADOR_VOZ.match(s)):
            continue
        s = re.sub(r"\[/?M\]", "", s).strip()
        if s:
            out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out).strip())


def resumo_parte2(texto):
    """Texto do RESUMO/gancho da Parte 2 — o bloco-ponte entre P1 e P2.

    Regras, na ordem:
      1) Se houver 'END OF PART 1'/'FIM DA PARTE 1': o resumo é o que vem DEPOIS dele, até o
         próximo cabeçalho (Chapter/Capítulo N ou PARTE 2) ou o fim. Este é o caminho OFICIAL:
         o marcador 'END OF PART 1' é FIXO em todo roteiro e o texto após ele É o resumo da P2
         (confirmado pela editora 2026-07-09). Como o roteiro.txt é só a Parte 1, depois do
         marcador normalmente só existe esse bloco.
      2) Senão, se houver um cabeçalho 'PARTE 2'/'PART 2': o resumo é o bloco de parágrafo
         contíguo IMEDIATAMENTE ANTES desse cabeçalho (fallback defensivo p/ roteiro sem o marcador).
      3) Senão: '' (sem resumo detectável — o chamador decide o fallback).
    """
    linhas = texto.replace("\r\n", "\n").split("\n")
    idx_fim = next((i for i, l in enumerate(linhas) if _RE_FIM.match(l.strip())), None)
    idx_p2 = next((i for i, l in enumerate(linhas) if _RE_PARTE2.match(l.strip())), None)

    if idx_fim is not None:
        fim = len(linhas)
        for j in range(idx_fim + 1, len(linhas)):
            s = linhas[j].strip()
            if _RE_CAP_ANY.match(s) or _RE_PARTE2.match(s):
                fim = j
                break
        return _limpar_bloco(linhas[idx_fim + 1:fim])

    if idx_p2 is not None:
        j = idx_p2 - 1
        while j >= 0 and not linhas[j].strip():
            j -= 1
        fim = j + 1
        ini = fim
        while ini - 1 >= 0 and linhas[ini - 1].strip():
            ini -= 1
        return _limpar_bloco(linhas[ini:fim])

    return ""


if __name__ == "__main__":
    import sys
    from pathlib import Path
    p = Path(sys.argv[1] if len(sys.argv) > 1 else "roteiro.txt")
    est = parse_roteiro(p.read_text(encoding="utf-8", errors="replace"))
    _r = resumo_parte2(p.read_text(encoding="utf-8", errors="replace"))
    print("resumo_parte2: %d chars | %r" % (len(_r), _r[:120]))
    print("hook: %d chars" % len(est["hook"]))
    for c in est["chapters"]:
        print("  Cap %d — %s | %d chars | ancora: %r"
              % (c["n"], c["titulo"], len(c["corpo"]), c["primeira_frase"]))
    print("\ntexto_narracao: %d chars" % len(texto_narracao(
        p.read_text(encoding="utf-8", errors="replace"))))
