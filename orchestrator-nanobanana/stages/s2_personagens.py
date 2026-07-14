# -*- coding: utf-8 -*-
"""Etapa 2 — Personagens/Bible + Library do Magnific.

Contrato: run(proj, log, cancel, **_). Idempotente (âncora = character_bible.txt).

Estratégia de REFERÊNCIA VISUAL (confirmada com a equipe), em ordem de prioridade:
  - Se a EDITORA largou fotos de personagem na pasta do vídeo (projects/<slug>/personagens/,
    pela GUI — junto com o teaser), usamos ESSAS como verdade de aparência (modo "refs_locais").
    São as mesmas fotos que o MCP do Magnific usa de referência na Etapa 5. Têm prioridade.
  - Senão, se o card traz IMAGENS DE PERSONAGEM anexadas (ex.: "82 - Personagem Homem.png",
    "82 - Personagem Mulher.png") — caso dos canais Auroa e afins — usamos ESSAS (modo "refs_do_card").
  - Senão, usamos a CAPA/thumbnail anexada ("CAPA VERTICAL"/"THUMB") como verdade visual e
    derivamos os 2 personagens dela (modo "da_thumb").
A CAPA (estilo visual) é sempre puxada do card quando existir, mesmo no modo "refs_locais".

Esta parte (ingestão) é 100% determinística e testável. A geração do character_bible.txt e o
registro na Library do Magnific vêm como passos seguintes (usam claude -p / MCP).
"""

import os
import re
import json
import shutil
import urllib.request

import clickup_api
from common import ErroPipeline, IMG_EXTS
from runner import rodar_claude, PREAMBULO, MODELO_PROMPTS

# Classificação dos anexos por nome de arquivo.
_RE_PERSONAGEM = re.compile(r"personagem|character|char\b", re.IGNORECASE)
_RE_HOMEM = re.compile(r"homem|masculino|\bman\b|\bmale\b|\bhero\b|galã|galan", re.IGNORECASE)
_RE_MULHER = re.compile(r"mulher|feminino|\bwoman\b|\bfemale\b|hero[íi]na|heroine", re.IGNORECASE)
_RE_CAPA = re.compile(r"\bcapa\b|thumb|cover|vertical", re.IGNORECASE)


def _baixar(url, destino, log):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 roteiro-auto"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        destino.write_bytes(resp.read())
    log("    baixado: %s (%d KB)" % (destino.name, destino.stat().st_size // 1024))


def _eh_imagem(a):
    """True se o anexo é imagem — checa mimetype, extension, e por fim título/URL.

    (O título pode NÃO ter extensão, ex.: 'CAPA VERTICAL' com extension='png' à parte.)"""
    if (a.get("mimetype") or "").lower().startswith("image/"):
        return True
    if ("." + (a.get("extension") or "").lower().lstrip(".")) in IMG_EXTS:
        return True
    alvo = (a.get("title") or "") + " " + (a.get("url") or "")
    return any(alvo.lower().endswith(e) or (e + "?") in alvo.lower() or (e in alvo.lower())
               for e in IMG_EXTS)


def _genero_por_nome(nome):
    """'homem/male' -> 'm', 'mulher/female' -> 'f', indefinido -> 'x' (pelo nome do arquivo)."""
    if _RE_HOMEM.search(nome):
        return "m"
    if _RE_MULHER.search(nome):
        return "f"
    return "x"


def _personagens_locais(proj, log):
    """Fotos de personagem que a EDITORA largou em projects/<slug>/personagens/ (via GUI).

    Copia cada imagem para referencias/ (nome ref_<genero>_<n>.png, como os anexos do card) e
    devolve [{genero, arquivo}]. Vazio se a pasta não existir ou não tiver imagem. O gênero é
    deduzido do NOME do arquivo (homem/mulher/male/female); indefinido = 'x' (a bible descobre
    quem é quem pelo roteiro)."""
    d = proj.personagens_dir
    if not d.is_dir():
        return []
    imgs = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS)
    if not imgs:
        return []
    refs_dir = proj.referencias_dir
    refs_dir.mkdir(parents=True, exist_ok=True)
    personagens = []
    for i, src in enumerate(imgs, 1):
        genero = _genero_por_nome(src.name)
        arq = refs_dir / ("ref_%s_%d.png" % (
            {"m": "homem", "f": "mulher", "x": "personagem"}[genero], i))
        shutil.copy2(str(src), str(arq))
        log("    personagem local: %s -> %s" % (src.name, arq.name))
        personagens.append({"genero": genero, "arquivo": arq.name})
    return personagens


def ingerir_refs(proj, log):
    """Reúne e classifica as referências visuais. Devolve o manifesto (dict) e grava referencias.json.

    Prioridade: fotos LOCAIS (projects/<slug>/personagens/, largadas pela editora na GUI) >
    anexos de personagem do card > capa/thumb do card. Manifesto:
    {modo, personagens:[{genero, arquivo}], capa, card_id}."""
    if not proj.source.is_file():
        raise ErroPipeline("source.json não existe — rode a Etapa 1 antes da 2.")
    source = json.loads(proj.source.read_text(encoding="utf-8"))
    card_id = source.get("card_id")
    if not card_id:
        raise ErroPipeline("source.json sem card_id.")

    refs_dir = proj.referencias_dir
    refs_dir.mkdir(parents=True, exist_ok=True)

    # (1) Personagens LOCAIS têm prioridade — é o caminho "já mandei as fotos junto do teaser".
    personagens = _personagens_locais(proj, log)
    usou_local = bool(personagens)
    if usou_local:
        log("  %d foto(s) de personagem local(is) em personagens/ — ignorando anexos de personagem do card." % len(personagens))

    # (2) Card: sempre tenta a CAPA (estilo visual); as fotos de personagem do card só entram
    # se NÃO houver fotos locais (fallback do comportamento antigo).
    task = clickup_api._get("/task/%s" % card_id)
    anexos = [a for a in (task.get("attachments") or []) if _eh_imagem(a)]
    capa = None
    for a in anexos:
        titulo = a.get("title") or ""
        url = a.get("url")
        if not url:
            continue
        if _RE_PERSONAGEM.search(titulo):
            if usou_local:
                continue  # já temos os personagens locais — não sobrescreve
            genero = "m" if _RE_HOMEM.search(titulo) else ("f" if _RE_MULHER.search(titulo) else "x")
            arq = refs_dir / ("ref_%s_%s.png" % (
                {"m": "homem", "f": "mulher", "x": "personagem"}[genero], len(personagens) + 1))
            _baixar(url, arq, log)
            personagens.append({"genero": genero, "arquivo": arq.name})
        elif _RE_CAPA.search(titulo) and capa is None:
            capa = proj.thumb_ref
            _baixar(url, capa, log)

    # Ordem de modo: local > card > da_thumb. Sem NENHUMA referência (nem local, nem card) falha.
    if not personagens and not capa:
        raise ErroPipeline(
            "Sem referência visual pra ancorar as imagens: nem fotos de personagem em "
            "projects/%s/personagens/ (largue pela GUI), nem anexos de personagem/capa no "
            "card %s. Anexos do card: %s"
            % (proj.dir.name, card_id, [a.get("title") for a in anexos]))
    modo = "refs_locais" if usou_local else ("refs_do_card" if personagens else "da_thumb")

    manifesto = {
        "card_id": card_id,
        "modo": modo,
        "personagens": personagens,
        "capa": (proj.thumb_ref.name if capa else None),
    }
    proj.referencias_json.write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    log("  modo de referência: %s | personagens: %d | capa: %s"
        % (modo, len(personagens), "sim" if capa else "não"))
    return manifesto


_ROTULO_GENERO = {"m": "male lead", "f": "female lead", "x": "supporting character"}


def _prompt_bible(manifesto):
    """Monta o prompt (PT) que faz o Claude LER as refs + roteiro e escrever character_bible.txt (EN)."""
    linhas_refs = []
    local = manifesto.get("modo") == "refs_locais"
    for p in manifesto["personagens"]:
        # Fotos locais de gênero indefinido são os PROTAGONISTAS (a editora largou os leads),
        # não figurantes — o roteiro decide quem é homem/mulher.
        rotulo = _ROTULO_GENERO.get(p["genero"], "character")
        if p["genero"] == "x" and local:
            rotulo = "main character / lead (descubra o gênero e o papel pelo roteiro)"
        linhas_refs.append("  - referencias/%s  → %s" % (p["arquivo"], rotulo))
    if manifesto.get("capa"):
        linhas_refs.append("  - %s  → CAPA/thumbnail (estilo visual e clima da história)" % manifesto["capa"])
    refs_txt = "\n".join(linhas_refs) or "  (sem imagens — derive do roteiro)"

    return PREAMBULO + """(sem skill — siga estas instruções diretamente)

TAREFA: escrever o arquivo character_bible.txt (EM INGLÊS) — a CHARACTER BIBLE deste vídeo.
É a VERDADE VISUAL dos personagens, usada depois pra ancorar os prompts de imagem (Etapa 4/5)
e manter consistência. NÃO invente traços que contrariem as imagens de referência.

ENTRADAS (leia com a ferramenta Read):
{refs}
  - roteiro.txt  → a história completa em inglês (use pra descobrir NOMES e papéis dos personagens)

PASSOS:
1. Leia as imagens de referência acima (Read em cada .png). Elas são a VERDADE de aparência.
2. Leia roteiro.txt e identifique os personagens PRINCIPAIS (herói/heroína) e quem é quem nas fotos
   (a foto masculina = o protagonista homem; a feminina = a protagonista mulher).
3. Escreva character_bible.txt EM INGLÊS, com este formato:

VISUAL STYLE
<2-4 linhas: estética cinematográfica, iluminação, paleta e clima — ancorado na CAPA.>

[Character 1: NOME] — <papel na história (ex.: the billionaire hero)>
- Face/Hair/Eyes/Skin: <travado na foto de referência — específico e reutilizável>
- Age/Build: <...>
- Signature wardrobe: <o figurino da foto (ex.: black tuxedo, bow tie)>
- Ref image: referencias/<arquivo>

[Character 2: NOME] — <papel>
- ... (mesma estrutura)

(inclua personagens SECUNDÁRIOS relevantes do roteiro só com uma linha de descrição, sem foto.)

REGRAS: inglês; descrições concretas e visuais (servem de prompt); NÃO contradiga as fotos;
salve SOMENTE character_bible.txt na pasta atual. No resumo final, liste os personagens e a foto de cada um.
""".format(refs=refs_txt)


def _gerar_bible(proj, log, cancel, manifesto):
    if proj.character_bible.is_file() and proj.character_bible.stat().st_size > 0:
        log("  character_bible.txt já existe — pulando geração.")
        return
    if not proj.roteiro.is_file():
        raise ErroPipeline("roteiro.txt não existe — rode a Etapa 1 antes da 2.")
    log("  gerando character_bible.txt (Claude lendo as refs + roteiro)…")
    rodar_claude(_prompt_bible(manifesto), proj.dir, log, cancel,
                 modelo=MODELO_PROMPTS, allowed_tools="Read Write")
    if not (proj.character_bible.is_file() and proj.character_bible.stat().st_size > 0):
        raise ErroPipeline(
            "A geração não produziu character_bible.txt. Confira o log do Claude acima.")
    log("  character_bible.txt gravado (%d chars)." % proj.character_bible.stat().st_size)


def run(proj, log, cancel=None, **_):
    log("Ingerindo referências visuais do card…")
    manifesto = ingerir_refs(proj, log)
    _gerar_bible(proj, log, cancel, manifesto)
    log("  refs prontas p/ ancorar as imagens (Etapa 5) — modo=%s, %d personagem(ns)."
        % (manifesto["modo"], len(manifesto["personagens"])))


# --- teste standalone: py -3 stages/s2_personagens.py <slug> --------------------------
if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    slug = sys.argv[1] if len(sys.argv) > 1 else "82-faxineira-bilionario"
    run(projeto_por_slug(slug), print, None)
