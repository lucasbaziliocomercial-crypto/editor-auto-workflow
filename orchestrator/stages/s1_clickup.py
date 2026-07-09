# -*- coding: utf-8 -*-
"""Etapa 1 — ClickUp + Roteiro pronto (extrai do Doc linkado nos comentários).

Contrato: run(proj, log, cancel, card_id=..., **_). Idempotente (âncora = source.json).

O que faz (Python puro, sem MCP — via REST do ClickUp + export do Google Docs):
  1. Lê o card (clickup_api REST): nome literal, canal (= nome da List), e os rótulos da
     descrição (Título:, Premissa:, Thumb:, TEXTO NA THUMB).
  2. Lê os COMENTÁRIOS e acha o link do Google Doc do ROTEIRO EN — ignorando os comentários
     rotulados "Roteiro em português" (versão de teste PT) e "PREMISSA:".
  3. Baixa o Doc em texto (endpoint /export?format=txt — funciona com o link compartilhado)
     e separa por abas: `Tab 1` = P1 (isca/YouTube), `Tab 2` = P2 (extensão/plataforma).
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
from common import ErroPipeline, slugify

_DOC_ID = re.compile(r"docs\.google\.com/document/d/([A-Za-z0-9_-]{20,})")
_TAB = re.compile(r"^﻿?\s*Tab\s+(\d+)\s*$", re.IGNORECASE)
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


def _separar_abas(txt):
    """Divide o texto do Doc pelas linhas 'Tab N' -> {1: texto_p1, 2: texto_p2, ...}.

    Se não houver marcador de aba, tudo vira a aba 1 (P1)."""
    linhas = txt.replace("\r\n", "\n").split("\n")
    abas = {}
    atual = None
    buf = []
    for ln in linhas:
        m = _TAB.match(ln)
        if m:
            if atual is not None:
                abas[atual] = "\n".join(buf).strip()
            atual = int(m.group(1))
            buf = []
        else:
            if atual is None:
                atual = 1  # conteúdo antes de qualquer 'Tab' vai pra P1
            buf.append(ln)
    if atual is not None:
        abas[atual] = "\n".join(buf).strip()
    return abas


def _contar_caps(texto):
    return len(re.findall(r"(?im)^\**\s*Chapter\s+\d+\b", texto))


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

def run(proj, log, cancel=None, *, card_id=None, categoria=None, **_):
    if not card_id:
        raise ErroPipeline(
            "Etapa 1 precisa do id do card: rode com --card <task_id> "
            "(ex.: --card 86aj5ex59).")

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
            "O Doc não tem conteúdo de P1 (aba 'Tab 1') utilizável. Abas achadas: %s"
            % sorted(abas))

    roteiro_p1 = abas[1].strip()
    roteiro_p2 = (abas.get(2) or "").strip()
    # Guarda: o roteiro DEVE estar em inglês com cabeçalhos "Chapter N —" (é o que a
    # esteira parseia). Zero capítulos = Doc errado (quase sempre a versão PT rotulada
    # "Roteiro em português") — falha ALTO aqui, senão a Etapa 3 gera narração do texto
    # errado e a Etapa 4 só quebra bem depois. Override manual: env ROTEIRO_DOC_URL.
    if _contar_caps(roteiro_p1) == 0:
        raise ErroPipeline(
            "O Doc %s (Tab 1) não tem NENHUM capítulo em inglês (linhas 'Chapter N —'). "
            "Provavelmente é a versão em PORTUGUÊS (ou premissa), não o roteiro EN. "
            "Confira o link do roteiro EN nos comentários do card e, se preciso, force com "
            "a env ROTEIRO_DOC_URL=<link do Doc EN> antes de rodar a Etapa 1." % doc_id)
    proj.roteiro.write_text(roteiro_p1, encoding="utf-8")
    log("  P1 (Tab 1): %d caps, %d palavras -> %s"
        % (_contar_caps(roteiro_p1), len(roteiro_p1.split()), proj.roteiro.name))
    if roteiro_p2:
        (proj.dir / "roteiro_p2.txt").write_text(roteiro_p2, encoding="utf-8")
        log("  P2 (Tab 2): %d caps, %d palavras -> roteiro_p2.txt"
            % (_contar_caps(roteiro_p2), len(roteiro_p2.split())))
    else:
        log("  (sem Tab 2 — card só tem P1)")

    source = {
        "card_id": card_id,
        "nome_card": nome_card,
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
    proj.source.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
    log("  source.json gravado (canal=%s, caps P1=%d / P2=%d)."
        % (source["canal"], source["n_caps_p1"], source["n_caps_p2"]))


# --- teste standalone: py -3 stages/s1_clickup.py <card_id> [slug] --------------------
if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    cid = sys.argv[1] if len(sys.argv) > 1 else None
    slug = sys.argv[2] if len(sys.argv) > 2 else "_tmp_s1_teste"
    run(projeto_por_slug(slug), print, None, card_id=cid)
