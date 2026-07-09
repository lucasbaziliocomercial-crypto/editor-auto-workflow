# -*- coding: utf-8 -*-
"""vozes_p2.py — separação de voz masculina/feminina para a NARRAÇÃO da Parte 2.

Porta enxuta (Python puro, sem LLM) da lógica de voz do Romance Maker
(`app/script_parser.py` + `app/srt_build.py`), adaptada à esteira roteiro-auto:

  - A P1 é sempre voz ÚNICA (narração da heroína). A P2 alterna ♀/♂ por POV.
  - O gênero de cada trecho sai, em ordem de confiança:
      1. do MARCADOR do roteiro: `✦ NOME — male POV` / `✦ NOME — female POV`
         (ou "POV masculino/feminino"); o gênero vem escrito, é 100% confiável.
      2. do legado `[M]...[/M]` (masculino entre tags, resto feminino).
      3. SEM marcador: detecção automática de POV por capítulo — em 1ª pessoa o
         narrador quase não se nomeia, então o lead MENOS citado no capítulo é
         quem narra. Os nomes dos leads M/F vêm do character_bible.txt (reusa a P1).

Este módulo NÃO decide voz de canal nem faz TTS — só devolve segmentos
`[{"genero": "m"|"f", "texto": str}]` na ordem de narração, já SEM as linhas de
marcador `✦` (que não são faladas). Quem casa gênero→voz do canal é a Etapa 3.

Reaproveita o parser de capítulos oficial (`roteiro_estrutura.parse_roteiro`).
"""

import re

import roteiro_estrutura


# ---------------------------------------------------------------------------
# Marcadores de voz no corpo do capítulo
# ---------------------------------------------------------------------------

# Linha de POV: "✦ LUDOVICO — male POV", "✦ Nerina — POV feminino", "✦ TAVIAN".
# name = tudo até um separador cercado por espaços (— / – / -); pov = o resto (opcional,
# costuma declarar o gênero). Sem separador, name = a linha toda e pov fica vazio.
_RE_POV = re.compile(
    r"^[ \t]*✦[ \t]*(?P<name>[^\n]+?)"
    r"(?:[ \t]+[—–\-][ \t]+(?P<pov>[^\n]*?))?"
    r"[ \t]*$",
    re.MULTILINE,
)

# Legado: trechos masculinos entre [M]...[/M]; resto = feminino.
_RE_TAG_M = re.compile(r"\[M\](.*?)\[/M\]", re.IGNORECASE | re.DOTALL)


def _genero_do_pov_token(pov, name, male_upper, female_upper):
    """Resolve 'm'/'f' de UMA linha ✦. Prioridade: gênero escrito na linha
    ('male'/'masculino' vs 'female'/'feminino') > nome nas listas do bible >
    palpite por terminação. ATENÇÃO: 'female' contém 'male' — checa F primeiro."""
    t = (pov or "").lower()
    if "femal" in t or "femin" in t or "mulher" in t or "(f)" in t or t.strip() in ("f", "ela"):
        return "f"
    if "male" in t or "mascul" in t or "homem" in t or "(m)" in t or t.strip() in ("m", "ele"):
        return "m"
    key = (name or "").strip().upper()
    if key in male_upper:
        return "m"
    if key in female_upper:
        return "f"
    return palpite_genero(name)


def palpite_genero(nome):
    """Palpite por terminação do nome (último recurso). Termina em 'a' → 'f',
    senão 'm'. Acerta NERINA/IOLANDA=f, LUDOVICO/TAVIAN=m."""
    n = (nome or "").strip().lower()
    return "f" if n.endswith("a") else "m"


# ---------------------------------------------------------------------------
# Gênero dos leads a partir do character_bible.txt
# ---------------------------------------------------------------------------

_RE_CHAR_PRINCIPAL = re.compile(
    r"^\[Character\s+\d+\s*:\s*(?P<nome>[^\]]+?)\s*\](?P<resto>.*)$",
    re.IGNORECASE,
)


def _primeiro_nome(nome_completo):
    """'TAVIAN MORETTI' -> 'TAVIAN'. O roteiro cita os personagens pelo 1º nome."""
    nome = (nome_completo or "").strip()
    # ignora um '(alias)' colado
    nome = re.split(r"[\(,/]", nome, maxsplit=1)[0].strip()
    return nome.split()[0] if nome.split() else nome


def _genero_de_bloco(texto):
    """Palpite de gênero de um bloco de descrição de personagem. Devolve 'm'/'f'/''.

    Prioridade: a anotação 'Ref image: ... (male/female figure)' do bible (sinal
    forte e sem ruído) > contagem de pronomes na descrição."""
    t = (texto or "").lower()
    if "female figure" in t:
        return "f"
    if "male figure" in t:
        return "m"
    fem = len(re.findall(r"\b(?:she|her|hers|woman|women|heroine)\b", t))
    masc = len(re.findall(r"\b(?:he|him|his|man|men)\b", t))
    if fem > masc:
        return "f"
    if masc > fem:
        return "m"
    return ""


def generos_do_bible(bible_text):
    """Lê o character_bible.txt e devolve ({nomes_m}, {nomes_f}) em MAIÚSCULAS
    (1º nome), cobrindo os leads `[Character N: NAME]` e os secundários.

    Sinais: linha 'Ref image: ... (male/female figure)' e pronomes na descrição.
    Sem bible → ({}, {}) — a detecção cai no palpite por nome."""
    male, female = set(), set()
    if not (bible_text or "").strip():
        return male, female

    linhas = bible_text.replace("\r\n", "\n").split("\n")

    # 1) Personagens principais: bloco começa em "[Character N: NAME]" e vai até o
    #    próximo "[..." ou fim. Usa a descrição do bloco pra decidir o gênero.
    i = 0
    n = len(linhas)
    while i < n:
        m = _RE_CHAR_PRINCIPAL.match(linhas[i].strip())
        if not m:
            i += 1
            continue
        nome = _primeiro_nome(m.group("nome"))
        bloco = [m.group("resto") or ""]
        j = i + 1
        while j < n and not linhas[j].lstrip().startswith("["):
            bloco.append(linhas[j])
            j += 1
        g = _genero_de_bloco("\n".join(bloco))
        if g == "m":
            male.add(nome.upper())
        elif g == "f":
            female.add(nome.upper())
        else:
            (female if palpite_genero(nome) == "f" else male).add(nome.upper())
        i = j

    # 2) Secundários: linhas "NOME (alias): descrição" ou "NOME: descrição" numa
    #    seção do tipo "[Secondary characters ...]". Pronomes na descrição decidem.
    for ln in linhas:
        s = ln.strip()
        if not s or s.startswith("["):
            continue
        m2 = re.match(r"^(?P<nome>[A-Z][A-Za-z'’\-]+)(?:\s*\([^)]*\))?\s*:\s*(?P<desc>.+)$", s)
        if not m2:
            continue
        nome = _primeiro_nome(m2.group("nome"))
        up = nome.upper()
        if up in male or up in female:
            continue
        g = _genero_de_bloco(m2.group("desc"))
        if g == "m":
            male.add(up)
        elif g == "f":
            female.add(up)
    return male, female


# ---------------------------------------------------------------------------
# Detecção automática de POV por capítulo (roteiro SEM marcador)
# ---------------------------------------------------------------------------

_SELFID_RES = [
    re.compile(r"\bI,\s+([A-Z][\w'’-]+)"),
    re.compile(r"\bI\s+am\s+([A-Z][\w'’-]+)"),
    re.compile(r"\bI['’]?m\s+([A-Z][\w'’-]+)"),
    re.compile(r"(?i)\bmy name (?:is|was)\s+([A-Z][\w'’-]+)"),
    re.compile(r"(?i)\bcall me\s+([A-Z][\w'’-]+)"),
]


def _conta_mencoes(texto, nomes):
    total = 0
    for nm in nomes:
        nm = (nm or "").strip()
        if nm:
            total += len(re.findall(r"\b" + re.escape(nm) + r"\b", texto, re.IGNORECASE))
    return total


def detectar_pov_capitulo(corpo, male_leads, female_leads):
    """POV de UM capítulo (sem marcador). Devolve 'm'/'f'.

    Em 1ª pessoa o narrador é 'eu' e quase não se nomeia; o OUTRO lead É citado.
    Logo, o lado MAIS citado NÃO é o narrador. Reforço: auto-ID ('I'm Nikolai')."""
    texto = corpo or ""
    male_leads = [n for n in (male_leads or []) if n]
    female_leads = [n for n in (female_leads or []) if n]
    genero_de = {n.upper(): "m" for n in male_leads}
    genero_de.update({n.upper(): "f" for n in female_leads})

    # 1) auto-identificação no começo do capítulo (alta confiança)
    head = texto[:800]
    for rx in _SELFID_RES:
        for m in rx.finditer(head):
            g = genero_de.get((m.group(1) or "").strip().upper())
            if g:
                return g

    # 2) frequência: ele citado mais → ela narra (f)
    mc = _conta_mencoes(texto, male_leads)
    fc = _conta_mencoes(texto, female_leads)
    if mc == 0 and fc == 0:
        return "f"  # default narradora
    return "f" if mc >= fc else "m"


# ---------------------------------------------------------------------------
# Separação em segmentos de voz
# ---------------------------------------------------------------------------

def _norm(texto):
    """Colapsa quebras internas mantendo o conteúdo (o TTS narra em prosa corrida)."""
    return re.sub(r"[ \t]*\n[ \t]*", " ", (texto or "")).strip()


def _iter_markers(corpo):
    """Marcadores ✦ no corpo, cada um como (start, end, name, pov)."""
    return [(m.start(), m.end(), (m.group("name") or "").strip(),
             (m.group("pov") or "").strip())
            for m in _RE_POV.finditer(corpo)]


def segmentos_do_corpo(corpo, male_upper, female_upper):
    """Separa o CORPO de um capítulo em segmentos ordenados [{'genero','texto'}].

    ✦ NOME — POV: cada trecho entre marcadores vira um segmento (a LINHA do
    marcador não é narrada). Legado [M]...[/M]: masculino entre tags. Sem
    marcador: capítulo inteiro no POV detectado (1 segmento)."""
    corpo = corpo or ""
    if not corpo.strip():
        return []

    markers = _iter_markers(corpo)
    if markers:
        segs = []
        pre = _norm(corpo[:markers[0][0]])
        if pre:
            segs.append({"genero": "f", "texto": pre})  # antes do 1º ✦ = narradora
        for i, (s, e, name, pov) in enumerate(markers):
            g = _genero_do_pov_token(pov, name, male_upper, female_upper)
            fim = markers[i + 1][0] if i + 1 < len(markers) else len(corpo)
            corpo_seg = _norm(corpo[e:fim])
            if corpo_seg:
                segs.append({"genero": g, "texto": corpo_seg})
        return segs

    if _RE_TAG_M.search(corpo):
        segs = []
        pos = 0
        for m in _RE_TAG_M.finditer(corpo):
            antes = _norm(corpo[pos:m.start()])
            if antes:
                segs.append({"genero": "f", "texto": antes})
            masc = _norm(m.group(1))
            if masc:
                segs.append({"genero": "m", "texto": masc})
            pos = m.end()
        resto = _norm(re.sub(r"\[/?M\]", "", corpo[pos:], flags=re.IGNORECASE))
        if resto:
            segs.append({"genero": "f", "texto": resto})
        return segs

    # sem marcador → decidido lá em cima (segmentos_p2 passa o POV do capítulo)
    return [{"genero": None, "texto": _norm(corpo)}]


def limpar_marcadores(texto):
    """Remove o que o TTS NÃO fala: as linhas de marcador '✦ ...' e as tags [M]/[/M].
    Usado pra alinhar a âncora de capítulo (capitulos.json) com a narração real."""
    t = _RE_POV.sub("", texto or "")
    t = re.sub(r"\[/?M\]", "", t, flags=re.IGNORECASE)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def segmentos_p2(roteiro_texto, bible_text="", male_leads=None, female_leads=None):
    """Segmentos de narração da P2 na ordem de leitura: [{'genero':'m'|'f','texto':str}].

    Usa o parser oficial de capítulos. O gancho (antes do Chapter 1), se houver,
    entra como narradora (f). Cada capítulo é separado por segmentos_do_corpo; um
    capítulo SEM marcador recebe o POV detectado (detectar_pov_capitulo) inteiro.

    male_leads/female_leads: nomes (1º nome) das vozes M/F. Se vierem vazios, são
    extraídos do character_bible.txt."""
    if male_leads is None and female_leads is None:
        male_upper, female_upper = generos_do_bible(bible_text)
    else:
        male_upper = {n.strip().upper() for n in (male_leads or []) if n and n.strip()}
        female_upper = {n.strip().upper() for n in (female_leads or []) if n and n.strip()}

    est = roteiro_estrutura.parse_roteiro(roteiro_texto or "")
    out = []

    hook = (est.get("hook") or "").strip()
    if hook:
        for seg in segmentos_do_corpo(hook, male_upper, female_upper):
            seg["genero"] = seg["genero"] or "f"
            out.append(seg)

    for c in est.get("chapters", []):
        corpo = c.get("corpo") or ""
        segs = segmentos_do_corpo(corpo, male_upper, female_upper)
        # capítulo sem marcador → 1 segmento com genero=None → resolve pelo POV
        if len(segs) == 1 and segs[0]["genero"] is None:
            g = detectar_pov_capitulo(corpo, list(male_upper), list(female_upper))
            segs[0]["genero"] = g
        else:
            for seg in segs:
                seg["genero"] = seg["genero"] or "f"
        out.extend(s for s in segs if s["texto"].strip())

    # funde segmentos consecutivos de mesma voz (menos cortes no TTS)
    fundido = []
    for seg in out:
        if fundido and fundido[-1]["genero"] == seg["genero"]:
            fundido[-1]["texto"] = fundido[-1]["texto"].rstrip() + "\n\n" + seg["texto"].lstrip()
        else:
            fundido.append(dict(seg))
    return fundido


def resumo_segmentos(segmentos):
    """Estatística curta pra log: (n_seg, chars_m, chars_f)."""
    cm = sum(len(s["texto"]) for s in segmentos if s["genero"] == "m")
    cf = sum(len(s["texto"]) for s in segmentos if s["genero"] == "f")
    return len(segmentos), cm, cf


# --- teste standalone: py -3 vozes_p2.py <roteiro.txt> [character_bible.txt] ---------
if __name__ == "__main__":
    import sys
    from pathlib import Path
    rot = Path(sys.argv[1] if len(sys.argv) > 1 else "roteiro.txt")
    bib = Path(sys.argv[2]) if len(sys.argv) > 2 else rot.with_name("character_bible.txt")
    bible_txt = bib.read_text(encoding="utf-8", errors="replace") if bib.is_file() else ""
    try:
        import sys as _sys
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if bible_txt:
        m, f = generos_do_bible(bible_txt)
        print("bible -> M=%s | F=%s" % (sorted(m), sorted(f)))
    segs = segmentos_p2(rot.read_text(encoding="utf-8", errors="replace"), bible_txt)
    n, cm, cf = resumo_segmentos(segs)
    print("%d segmento(s) | M %d chars | F %d chars" % (n, cm, cf))
    for i, s in enumerate(segs, 1):
        print("  [%02d] %s | %d chars | %s" % (i, s["genero"].upper(), len(s["texto"]),
                                               s["texto"][:70].replace("\n", " ")))
