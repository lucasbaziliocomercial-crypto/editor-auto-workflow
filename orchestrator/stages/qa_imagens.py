# -*- coding: utf-8 -*-
"""qa_imagens.py — GATE DE QA VISUAL do Claude sobre as IMAGENS DO CORPO (Etapa 5).

É o "detector de imagem bugada": depois que a Etapa 5 gera `images/img_NNN.png`, um agente
ABRE cada PNG (Read) e o julga SÓ contra BUGS DUROS de geração (não gosto artístico):
identidade trocada entre os personagens (o rosto/roupa de um no corpo do outro; reflexo de
espelho mostrando pessoa/roupa DIFERENTE da que está na frente — foi o bug do vestido no
espelho), anatomia quebrada (braço/perna/dedo a mais ou a menos, mãos derretidas, duas
cabeças, pessoa duplicada/partida), rosto derretido, ou texto/legenda/marca-d'água queimada
na imagem. Grava `images/img_qa.json`:
    {"bugged": [3, 17], "issues": {"3": "...", "17": "..."}, "total": 30, "ok": 28}

O Python (s5_imagens) decide com base nisso: sem bugs → segue; com bugs → apaga os PNGs
bugados e REGENERA só eles com regra anti-bug reforçada, até ROTEIRO_IMG_QA_MAX_ROUNDS
rodadas. Desligável por env ROTEIRO_IMG_QA=0 (aí vira no-op aprovando tudo).

Custo: gasta uso do Claude (NÃO crédito Magnific). O modelo é barato por padrão (sonnet —
bug grosseiro é visualmente óbvio, não precisa de Opus). Falha do QA nunca derruba a
esteira — em erro devolve None e o s5 segue sem bloquear (degrada com aviso)."""

import json
import os

from runner import rodar_claude


def modelo_qa():
    """Modelo do QA visual. Default sonnet (bug grosseiro é óbvio e são muitas imagens — Opus
    ficaria caro em lote). Sobrescrevível por ROTEIRO_IMG_QA_MODEL=opus se quiser mais rigor."""
    return os.environ.get("ROTEIRO_IMG_QA_MODEL", "sonnet").strip() or "sonnet"


def ligado():
    """QA on por padrão; ROTEIRO_IMG_QA=0 desliga."""
    return os.environ.get("ROTEIRO_IMG_QA", "1").strip() not in ("0", "", "false", "no", "off")


def _instr(nums):
    """Instrução do QA. `nums` = lista de números de imagem (1-based) a inspecionar."""
    lista = ", ".join("img_%03d" % n for n in nums)
    arquivos = ", ".join("images/img_%03d.png" % n for n in nums)
    return (
        "Você é o DETECTOR DE IMAGEM BUGADA (QA) das imagens do corpo de um vídeo de romance. "
        "NÃO peça confirmação; não gere imagem nenhuma. Sua ÚNICA tarefa é ABRIR cada PNG com a "
        "tool Read e dizer se ele tem BUG DURO de geração.\n\n"
        "CONTEXTO (leia com Read antes): `character_bible.txt` — descreve os 2 personagens "
        "principais (rosto, cabelo, roupa). Use-o para saber QUEM é QUEM e detectar troca de "
        "identidade/roupa entre eles.\n\n"
        "ABRA E JULGUE ESTAS IMAGENS (uma por uma, com Read): %s.\n\n"
        "MARQUE COMO BUGADA (bugged) SÓ se tiver um destes BUGS DUROS — são falhas de render, "
        "não questão de gosto:\n"
        "1. IDENTIDADE TROCADA/FUNDIDA entre os personagens: o rosto de um sobre o corpo/roupa "
        "do outro; os dois personagens fundidos numa pessoa só; a roupa de um personagem "
        "vestida no outro. Ex. real: uma mulher de vestido vermelho de frente e o REFLEXO no "
        "espelho mostra o ROSTO DO HOMEM vestindo o vestido dela — BUG.\n"
        "2. ESPELHO/REFLEXO INCOERENTE: qualquer espelho, vidro ou reflexo que mostre uma PESSOA "
        "DIFERENTE, uma ROUPA DIFERENTE ou uma pose incompatível com quem está na frente.\n"
        "3. ANATOMIA QUEBRADA: braço/perna/mão/dedo a mais ou a menos, mãos derretidas ou com "
        "número errado de dedos, membros fundidos/tortos impossíveis, duas cabeças, pessoa "
        "duplicada ou partida ao meio, corpos grudados de forma impossível.\n"
        "4. ROSTO DERRETIDO/DEFORMADO: olhos/boca/nariz distorcidos, rosto assimétrico grotesco, "
        "'uncanny' claramente quebrado (não confundir com só 'feio' — é DEFORMADO).\n"
        "5. TEXTO NA IMAGEM: qualquer letra, legenda, subtítulo, marca-d'água, logo ou número "
        "renderizado dentro da imagem.\n\n"
        "NÃO marque por gosto: iluminação, enquadramento, beleza, pose, cor, fundo simples — "
        "nada disso é bug. Na dúvida entre 'só mediano' e 'quebrado', NÃO marque (só bug CLARO). "
        "Melhor deixar passar uma imagem mediana do que regenerar à toa (custa crédito).\n\n"
        "SAÍDA: escreva SÓ o arquivo `images/img_qa.json` (UTF-8) com Write, EXATAMENTE neste "
        "formato (as chaves de `issues` são o número da imagem como string, descrição CURTA do "
        "bug em português; só entram as bugadas):\n"
        '{"bugged": [3, 17], "issues": {"3": "reflexo do espelho com rosto do homem no vestido '
        'dela", "17": "mao com 6 dedos"}, "checadas": %d}\n'
        "Se NENHUMA estiver bugada: {\"bugged\": [], \"issues\": {}, \"checadas\": %d}. "
        "As imagens a checar são exatamente: %s."
        % (arquivos, len(nums), len(nums), lista)
    )


def avaliar(proj, log, cancel=None, checar=None):
    """Roda o QA visual sobre as imagens `checar` (lista de nums 1-based; None = todas as
    presentes) e devolve o dict do veredito.

    Retorno:
      - {"bugged":[...], "issues":{...}}          — veredito real do agente
      - {"bugged":[], "skipped":True}             — QA desligado (trata como 'sem bug')
      - None                                       — sem imagens / erro (s5 degrada sem bloquear)
    """
    if checar is None:
        checar = sorted(int(p.stem.split("_")[1]) for p in proj.images_dir.glob("img_*.png")
                        if p.stem.split("_")[-1].isdigit())
    checar = [n for n in checar if (proj.images_dir / ("img_%03d.png" % n)).exists()]
    if not checar:
        return None
    if not ligado():
        log("    QA visual das imagens DESLIGADO (ROTEIRO_IMG_QA=0) — pulando.")
        return {"bugged": [], "skipped": True}

    # limpa veredito anterior pra não ler um stale se o agente falhar
    try:
        if proj.img_qa.exists():
            proj.img_qa.unlink()
    except OSError:
        pass

    log("    🔎 QA visual (Claude/%s) checando %d imagem(ns) por bug de geração..."
        % (modelo_qa(), len(checar)))
    try:
        rodar_claude(_instr(checar), proj.dir, log, cancel, modelo=modelo_qa(),
                     allowed_tools="Read Write")
    except Exception as e:  # noqa: BLE001 — QA nunca derruba a esteira
        log("    ⚠ QA visual falhou (%s) — seguindo sem bloquear." % e)
        return None

    if not proj.img_qa.exists():
        log("    ⚠ QA não gravou img_qa.json — seguindo sem bloquear.")
        return None
    try:
        d = json.loads(proj.img_qa.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log("    ⚠ img_qa.json ilegível (%s) — seguindo sem bloquear." % e)
        return None

    # normaliza bugged -> lista de ints válidos dentro do conjunto checado
    checset = set(checar)
    bugged = []
    for n in d.get("bugged", []) or []:
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if n in checset and n not in bugged:
            bugged.append(n)
    d["bugged"] = sorted(bugged)
    d["issues"] = {str(k): v for k, v in (d.get("issues") or {}).items()}
    if bugged:
        detalhe = "; ".join("img_%03d: %s" % (n, d["issues"].get(str(n), "?")) for n in d["bugged"])
        log("    ✗ QA achou %d imagem(ns) BUGADA(S): %s" % (len(bugged), detalhe))
    else:
        log("    ✓ QA: nenhuma imagem bugada nas %d checadas." % len(checar))
    return d
