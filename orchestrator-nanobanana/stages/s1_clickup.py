# -*- coding: utf-8 -*-
"""Etapa 1 — ClickUp + Roteiro pronto (extrai do Doc linkado nos comentários).

Contrato: run(proj, log, cancel, card_id=..., **_). Idempotente (âncora = source.json).

O que faz (Python puro, sem MCP — via REST do ClickUp + export do Google Docs):
  1. Lê o card (clickup_api REST): nome literal, canal (= nome da List), e os rótulos da
     descrição (Título:, Premissa:, Thumb:, TEXTO NA THUMB).
  2. Lê os COMENTÁRIOS e acha o link do Google Doc do ROTEIRO EN — ignorando os comentários
     rotulados "Roteiro em português" (versão de teste PT) e "PREMISSA:".
  3. Baixa o Doc em texto (endpoint /export?format=txt — funciona com o link compartilhado)
     e separa por abas (guias): `Tab 1`/`… Part 1` = P1 (isca/YouTube),
     `Tab 2`/`… Part 2` = P2 (extensão/plataforma).
  4. Grava roteiro.txt (P1 = Tab 1) e roteiro_p2.txt (Tab 2), preservando os títulos de
     capítulo (linhas "Chapter N — Título") p/ a Etapa 4 gerar as capas. Grava source.json.

Falha ALTO (ErroPipeline) se não achar card, roteiro no comentário, ou Doc acessível —
nunca gera nada silenciosamente (regra de ouro do plano).
"""

import os
import re
import json
import urllib.request
import urllib.error

import clickup_api
from common import ErroPipeline, slugify, parece_portugues

_DOC_ID = re.compile(r"docs\.google\.com/document/d/([A-Za-z0-9_-]{20,})")
# Marcador de aba (guia) do Doc EN — LINHA ISOLADA no export .txt. Duas convenções:
#   "Tab 1" / "Tab 2"            (nome curto padrão da guia — sempre presente hoje)
#   "Part 2" / "Part 2 — Título" (guia cujo título COMEÇA em 'Part N', com título opcional)
# O número (1, 2, …) mapeia direto pra P1/P2/…  ⚠ ANCORADO NO INÍCIO de propósito: a versão
# antiga casava ".*?Part N$" (fim da linha) e engolia o separador de conteúdo "END OF PART 1"
# como se fosse a guia da P1 — o "Tab 2" seguinte então SOBRESCREVIA a P1 com o hook da P2,
# deixando roteiro.txt com 0 capítulos (o falso "Doc está em português" do card 256, 2026-07-09).
_TAB = re.compile(r"^﻿?\s*(?:Tab\s+(\d+)|Part\s+(\d+)(?:\s*[—\-:].*)?)\s*$", re.IGNORECASE)
_CAP = re.compile(r"(?im)^\**\s*Chapter\s+(\d+)\b")
# Rótulos que NÃO são o roteiro EN (versão PT de teste / premissa).
_RE_IGNORAR = re.compile(r"portugu[eê]s|premissa", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Descrição do card (rótulos Título:/Premissa:/Thumb:/TEXTO NA THUMB)
# ---------------------------------------------------------------------------

def _campo(desc, rotulo, ate=None):
    """Extrai o texto após 'Rotulo:' até o próximo rótulo (ou fim). ate = lista de rótulos limite."""
    ate = ate or []
    fim = r"|".join(re.escape(a) for a in ate)
    padrao = r"%s\s*:?\s*(.+?)(?=\n\s*(?:%s)\s*:|\Z)" % (re.escape(rotulo), fim) if fim \
        else r"%s\s*:?\s*(.+)" % re.escape(rotulo)
    m = re.search(padrao, desc, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _parse_descricao(desc):
    rotulos = ["TEXTO NA THUMB", "Título", "Titulo", "Premissa", "Thumb", "Ref"]
    return {
        "titulo":       _campo(desc, "Título", rotulos) or _campo(desc, "Titulo", rotulos),
        "premissa":     _campo(desc, "Premissa", rotulos),
        "thumb_brief":  _campo(desc, "Thumb", rotulos),
        "texto_thumb":  _campo(desc, "TEXTO NA THUMB", rotulos),
    }


# ---------------------------------------------------------------------------
# Comentários -> link do Doc do roteiro EN
# ---------------------------------------------------------------------------

def _achar_doc_roteiro(card_id, log):
    """Varre os comentários (mais recente primeiro) e devolve o ID do Doc do roteiro EN.

    Regra: ignora comentários cujo texto contém 'português' ou 'premissa'; entre os demais,
    prefere o mais recente que aponte pra um Google Doc. Override: env ROTEIRO_DOC_URL."""
    forcado = (os.environ.get("ROTEIRO_DOC_URL") or "").strip()
    if forcado:
        m = _DOC_ID.search(forcado)
        if m:
            log("  roteiro forçado por ROTEIRO_DOC_URL")
            return m.group(1)

    data = clickup_api._get("/task/%s/comment" % card_id)
    comentarios = data.get("comments", []) if isinstance(data, dict) else []
    if not comentarios:
        raise ErroPipeline(
            "Card %s não tem comentários — o roteiro EN precisa estar num Google Doc "
            "linkado nos comentários do card." % card_id)

    # ClickUp devolve do mais novo pro mais antigo; garanto por date desc.
    def _dt(c):
        try:
            return int(c.get("date") or 0)
        except (TypeError, ValueError):
            return 0
    comentarios = sorted(comentarios, key=_dt, reverse=True)

    candidatos = []
    for c in comentarios:
        # texto do comentário + o JSON cru (pega links em bookmark/link_mention)
        txt = c.get("comment_text") or ""
        cru = json.dumps(c.get("comment") or c, ensure_ascii=False)
        blob = txt + "\n" + cru
        ids = _DOC_ID.findall(blob)
        if not ids:
            continue
        rotulo_pt = bool(_RE_IGNORAR.search(txt))
        for did in ids:
            candidatos.append((rotulo_pt, did, txt.strip()[:60]))

    if not candidatos:
        raise ErroPipeline(
            "Nenhum link de Google Doc achado nos comentários do card %s." % card_id)

    # 1º os NÃO-rotulados (roteiro EN), na ordem (mais recente primeiro); PT/premissa por último.
    candidatos.sort(key=lambda x: x[0])
    for rotulo_pt, did, amostra in candidatos:
        tag = " (rotulado PT/premissa — evitado)" if rotulo_pt else ""
        log("  candidato Doc: %s%s %s" % (did[:12], tag, ("| " + amostra) if amostra else ""))
    # Se o MELHOR candidato ainda é PT/premissa, é porque o card NÃO tem o Doc do roteiro EN
    # (sem rótulo) — só o "Roteiro em português" e/ou a "PREMISSA". Avisa ALTO aqui; a trava de
    # idioma em run() vai barrar depois de baixar. (Ação: colar o Doc EN nos comentários do card,
    # como nos cards que saem certos, ou forçar ROTEIRO_DOC_URL=<link EN>.)
    if candidatos[0][0]:
        log("  ⚠ NENHUM Doc do roteiro EM INGLÊS (sem rótulo) nos comentários — só achei "
            "'Roteiro em português'/'Premissa'. O vídeo oficial é em inglês: cole o Doc do "
            "roteiro EN no card (ou use ROTEIRO_DOC_URL). Vou barrar a narração em PT.")
    return candidatos[0][1]


# ---------------------------------------------------------------------------
# Download do Doc (export txt) + separação por abas
# ---------------------------------------------------------------------------

def _baixar_doc_txt(doc_id, log):
    url = "https://docs.google.com/document/d/%s/export?format=txt" % doc_id
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 roteiro-auto"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            txt = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise ErroPipeline(
            "Não consegui baixar o Google Doc %s (HTTP %s). O Doc precisa estar compartilhado "
            "como 'qualquer pessoa com o link'." % (doc_id, e.code))
    except urllib.error.URLError as e:
        raise ErroPipeline("Falha de rede ao baixar o Google Doc %s: %s" % (doc_id, e))
    if len(txt.strip()) < 500:
        raise ErroPipeline("Google Doc %s veio vazio/curto demais (%d chars)." % (doc_id, len(txt)))
    return txt


def _split_por_capitulos_reset(texto):
    """Rede de segurança p/ Doc cuja P2 está numa ABA NATIVA sem 'Tab 2' (o export então
    concatena as duas partes numa aba só). Fatiа onde a numeração de capítulo RESETA
    ('Chapter …6' seguido de 'Chapter 1' de novo) -> (p1, p2). Sem reset -> (texto, "")."""
    caps = list(_CAP.finditer(texto))
    if len(caps) < 2:
        return texto, ""
    for i in range(1, len(caps)):
        if int(caps[i].group(1)) <= int(caps[i - 1].group(1)):  # numeração caiu = começa a P2
            corte = caps[i].start()
            return texto[:corte].strip(), texto[corte:].strip()
    return texto, ""


def _separar_abas(txt):
    """Divide o texto do Doc pelas linhas de guia ('Tab N' ou 'Part N')
    -> {1: texto_p1, 2: texto_p2, ...}.

    Se não houver marcador de aba, tudo vira a aba 1 (P1). Fallback: se só houver a aba 1
    mas a numeração de capítulo resetar no meio, fatia em P1/P2 (P2 em aba nativa sem 'Tab 2')."""
    linhas = txt.replace("\r\n", "\n").split("\n")
    abas = {}
    atual = None
    buf = []
    for ln in linhas:
        m = _TAB.match(ln)
        if m:
            if atual is not None:
                abas[atual] = "\n".join(buf).strip()
            atual = int(m.group(1) or m.group(2))  # 'Tab N' -> g1, 'Part N' -> g2
            buf = []
        else:
            if atual is None:
                atual = 1  # conteúdo antes de qualquer 'Tab' vai pra P1
            buf.append(ln)
    if atual is not None:
        abas[atual] = "\n".join(buf).strip()
    # Só a aba 1, mas com reset de numeração dentro dela = P2 sem marcador 'Tab 2'. Fatia.
    if set(abas) == {1}:
        p1, p2 = _split_por_capitulos_reset(abas[1])
        if p2:
            abas[1], abas[2] = p1, p2
    return abas


def _contar_caps(texto):
    return len(_CAP.findall(texto))


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

def run(proj, log, cancel=None, *, card_id=None, categoria=None, parte="p1", **_):
    if not card_id:
        raise ErroPipeline(
            "Etapa 1 precisa do id do card: rode com --card <task_id> "
            "(ex.: --card 86aj5ex59).")

    # P2-awareness: numa pasta de Parte 2 (slug '…-p2') OU rodando com parte='p2', o roteiro
    # DESTE projeto é a Tab 2 (Parte 2), NÃO a Tab 1. Sem isto, rodar a Etapa 1 apontada pra
    # pasta '-p2' (ex.: um "Continuar/Refazer" que a trate como card P1) sobrescrevia
    # roteiro.txt com a Parte 1 — a montagem então saía com o FORMATO da P2 mas a HISTÓRIA da
    # P1 (bug do card 84, 2026-07-11). A pasta manda: quem nasce '-p2' processa a Tab 2.
    is_p2 = str(parte).lower() == "p2" or proj.dir.name.endswith("-p2")

    log("Lendo card %s no ClickUp…" % card_id)
    task = clickup_api._get("/task/%s" % card_id)
    if not isinstance(task, dict) or not task.get("id"):
        raise ErroPipeline("Card %s não encontrado no ClickUp." % card_id)

    nome_card = task.get("name") or ""
    canal = ((task.get("list") or {}).get("name") or "").strip()
    desc = task.get("markdown_description") or task.get("text_content") or task.get("description") or ""
    campos = _parse_descricao(desc)
    log("  card: %s" % nome_card)
    log("  canal (List): %s" % (canal or "(desconhecido)"))
    log("  título EN: %s" % (campos["titulo"] or "(sem rótulo Título:)"))

    doc_id = _achar_doc_roteiro(card_id, log)
    log("Baixando roteiro do Google Doc %s…" % doc_id)
    txt = _baixar_doc_txt(doc_id, log)
    abas = _separar_abas(txt)
    if 1 not in abas or len(abas[1].strip()) < 500:
        raise ErroPipeline(
            "O Doc não tem conteúdo de P1 (aba 'Tab 1'/'Part 1') utilizável. Abas achadas: %s"
            % sorted(abas))

    roteiro_p1 = abas[1].strip()
    roteiro_p2 = (abas.get(2) or "").strip()

    # Numa pasta '-p2', o roteiro DESTE projeto é a Tab 2. Sem Tab 2 no Doc não há P2 a montar.
    if is_p2 and not roteiro_p2:
        raise ErroPipeline(
            "Este é um projeto de Parte 2 (%s), mas o Doc %s NÃO tem Tab 2 (Parte 2) — só P1. "
            "Não há Parte 2 a gerar para este card." % (proj.dir.name, doc_id))

    # `alvo` = o roteiro que ESTE projeto processa (Tab 2 na P2, Tab 1 na P1). Toda a validação
    # e a escrita de roteiro.txt passam a ser sobre o alvo — assim a pasta '-p2' nunca recebe a P1.
    alvo = roteiro_p2 if is_p2 else roteiro_p1
    rot_alvo = "Tab 2" if is_p2 else "Tab 1"

    # Guarda: o roteiro DEVE estar em inglês com cabeçalhos "Chapter N —" (é o que a
    # esteira parseia). Zero capítulos = Doc errado (quase sempre a versão PT rotulada
    # "Roteiro em português") — falha ALTO aqui, senão a Etapa 3 gera narração do texto
    # errado e a Etapa 4 só quebra bem depois. Override manual: env ROTEIRO_DOC_URL.
    if _contar_caps(alvo) == 0:
        total_caps = _contar_caps(txt)
        raise ErroPipeline(
            "O Doc %s (%s) ficou sem NENHUM capítulo em inglês (linhas 'Chapter N —'). "
            "O Doc inteiro tem %d capítulos e %d abas achadas (%s). %s "
            "Se preciso, force com a env ROTEIRO_DOC_URL=<link do Doc EN> antes da Etapa 1." % (
                doc_id, rot_alvo, total_caps, len(abas), sorted(abas),
                "Provavelmente é a versão em PORTUGUÊS (ou premissa), não o roteiro EN — "
                "confira o comentário do Doc EN no card." if total_caps == 0 else
                "O Doc EN foi baixado certo, mas a separação de abas caiu numa aba vazia — "
                "confira os marcadores de guia do Doc ('Tab 1'/'Tab 2')."))
    # Trava de idioma: mesmo COM cabeçalhos "Chapter N —", o miolo pode estar em português
    # (Doc PT que manteve os títulos em inglês). Narração PT + legenda EN destroçada é o
    # pior dos mundos — TRAVA aqui e exige o Doc EN (decisão do editor 2026-07-09). Só vale
    # no modo inglês; no modo teste (LONGFORM_IDIOMA=pt) o PT é intencional e passa.
    if parece_portugues(alvo):
        raise ErroPipeline(
            "O Doc %s (%s) está em PORTUGUÊS — a esteira produz vídeo em inglês, então a "
            "narração sairia em PT com legenda EN destroçada. Aponte o COMENTÁRIO do roteiro "
            "EN no card (evite os rotulados 'Roteiro em português'/'Premissa') ou force com a "
            "env ROTEIRO_DOC_URL=<link do Doc EN>. (Só pra teste em PT: LONGFORM_IDIOMA=pt.)"
            % (doc_id, rot_alvo))
    # Na P1, avisa também se a Tab 2 vier em PT (o vídeo da P2 sairia narrado em PT).
    if not is_p2 and roteiro_p2 and parece_portugues(roteiro_p2):
        raise ErroPipeline(
            "O Doc %s tem a Tab 2 (Parte 2) em PORTUGUÊS. O vídeo da P2 sairia narrado em PT. "
            "Corrija a Tab 2 do Doc EN (ou aponte o Doc EN certo) antes de rodar." % doc_id)

    proj.roteiro.write_text(alvo, encoding="utf-8")
    log("  %s (%s): %d caps, %d palavras -> %s"
        % ("P2" if is_p2 else "P1", rot_alvo, _contar_caps(alvo), len(alvo.split()), proj.roteiro.name))
    # roteiro_p2.txt (a Tab 2 "crua") só é gravado na P1 — é o que a semeadura da P2
    # (_preparar_projeto_p2) consome. Na pasta '-p2' o alvo JÁ é a Tab 2 em roteiro.txt.
    if not is_p2:
        if roteiro_p2:
            (proj.dir / "roteiro_p2.txt").write_text(roteiro_p2, encoding="utf-8")
            log("  P2 (Tab 2): %d caps, %d palavras -> roteiro_p2.txt"
                % (_contar_caps(roteiro_p2), len(roteiro_p2.split())))
        else:
            log("  (sem Tab 2 — card só tem P1)")

    source = {
        "card_id": card_id,
        "nome_card": ("%s - P2" % nome_card) if is_p2 else nome_card,
        "canal": canal,
        "canal_slug": slugify(canal or "sem-canal", maxlen=40),
        "categoria": categoria or slugify(canal or "sem-canal", maxlen=40),
        "titulo": campos["titulo"],
        "premissa": campos["premissa"],
        "thumb_brief": campos["thumb_brief"],
        "texto_thumb": campos["texto_thumb"],
        "roteiro_doc": "https://docs.google.com/document/d/%s/edit" % doc_id,
        "n_caps_p1": _contar_caps(roteiro_p1),
        "n_caps_p2": _contar_caps(roteiro_p2) if roteiro_p2 else 0,
    }
    if is_p2:
        source["parte"] = "p2"
    proj.source.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
    log("  source.json gravado (canal=%s, parte=%s, caps P1=%d / P2=%d)."
        % (source["canal"], "p2" if is_p2 else "p1", source["n_caps_p1"], source["n_caps_p2"]))


# --- teste standalone: py -3 stages/s1_clickup.py <card_id> [slug] --------------------
if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    cid = sys.argv[1] if len(sys.argv) > 1 else None
    slug = sys.argv[2] if len(sys.argv) > 2 else "_tmp_s1_teste"
    run(projeto_por_slug(slug), print, None, card_id=cid)
