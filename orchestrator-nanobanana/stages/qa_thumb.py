# -*- coding: utf-8 -*-
"""qa_thumb.py — GATE DE QA do Claude (Opus) sobre a CAPA gerada (Etapa 6).

É o "você aprova as thumbs": depois que a Etapa 6 gera `thumbs/thumb_01.png`, um agente
Opus ABRE a imagem (Read) e a julga contra o MODELO MENTAL do canal (emoção #1, Alpha King
cabelo-longo/musculoso, capa clara, limpa SEM texto, reflete a cena/era, rostos glam,
leitura em miniatura) e grava `thumbs/thumb_qa.json`:
    {"approved": bool, "score": 0-10, "issues": [...], "suggestions": [...], "verdict": "..."}

O Python (s6_thumbnails) decide com base nisso: aprovado → segue; reprovado → 1 retry com
as `suggestions` embutidas no prompt; se ainda reprovar, a capa vai pro Gate 2 HUMANO em vez
de auto-confirmar em silêncio. Desligável por env LONGFORM_THUMB_QA=0 (aí vira no-op aprovando).

Custo: gasta uso do Claude (Opus), NÃO crédito do Magnific. Falha do QA nunca derruba a
esteira — em erro, devolve None e o s6 segue (degrada com aviso)."""

import json
import os

from runner import rodar_claude

MODELO_QA = "opus"  # julgamento visual fino — vale Opus (é o "você aprova as thumbs")


def ligado():
    """QA on por padrão; LONGFORM_THUMB_QA=0 desliga."""
    return os.environ.get("LONGFORM_THUMB_QA", "1").strip() not in ("0", "", "false", "no")


def _instr():
    return (
        "Você é o REVISOR DE QUALIDADE (QA) da CAPA (thumbnail 16:9) de um canal de romance. "
        "NÃO peça confirmação. ABRA a imagem `thumbs/thumb_01.png` com a tool Read e avalie-a "
        "contra o PADRÃO do canal. ABRA também `source.json` e LEIA os campos `thumb_brief` "
        "(orientação da capa ESCRITA no card) e `categoria` (ex.: 'mafia', 'selena') — eles "
        "determinam o padrão a aplicar. Use `prompts_thumbnail.txt` só como contexto extra.\n\n"
        "PADRÃO (julgue cada item):\n"
        "1. EMOÇÃO intensa e legível no rosto (prioridade nº 1). Capa morna reprova.\n"
        "2. LEAD MASCULINO — físico e look dependem da CATEGORIA:\n"
        "   • categoria='mafia' (ou 'lena'): CHEFÃO DA MÁFIA — cabelo CURTO A MÉDIO, escuro, "
        "penteado para trás (slicked-back), NÃO longo; barba curta; TATUAGENS visíveis no "
        "pescoço/mãos/antebraços; físico alfa MÁXIMO (extremamente musculoso, peito enorme, "
        "ombros largos); terno/camisa preta aberta. Cabelo LONGO é falha CRÍTICA nesta categoria.\n"
        "   • qualquer outra categoria (default/selena): ALPHA KING — cabelo LONGO (long flowing "
        "hair, ombros ou mais) + físico ultra-musculoso/viril. Cabelo curto é falha CRÍTICA.\n"
        "3. CAPA CLARA/luminosa, rostos bem iluminados — NÃO pode ser escura/abafada.\n"
        "4. LIMPA: SEM nenhum texto/letra/legenda/marca-d'água/logo renderizado na imagem.\n"
        "5. Reflete a CENA/era correta para a categoria (máfia=contemporâneo opulento; "
        "selena=medieval/werewolf cinematográfico) e a ação pretendida.\n"
        "6. BELEZA DIVINA (ambos os leads — padrão deity-level): rosto perfeitamente simétrico, "
        "traços esculpidos, pele luminosa impecável, olhos cativantes e expressivos — os "
        "personagens devem parecer impossivamente bonitos. SEM deformação (mãos, dedos, rosto).\n"
        "7. COMPOSIÇÃO que lê em miniatura: leads grandes em primeiro plano.\n"
        "8. ORIENTAÇÃO DO CARD (`thumb_brief`): se o card trouxe orientação escrita, a capa TEM de "
        "mostrar exatamente a AÇÃO/cena/figurino pedidos (ex.: 'ele cheirando o pescoço dela', "
        "'rosto admirado porém assustado', 'ele meio bêbado abraçado com ela'). Se a capa NÃO "
        "corresponde ao que está escrito, é falha CRÍTICA. (Se `thumb_brief` estiver vazio/ausente, "
        "ignore este item.)\n\n"
        "VEREDITO: approved=false se QUALQUER item CRÍTICO falhar — não cumpre a orientação do "
        "card (8), tem texto na imagem (4), capa escura (3), lead masculino com look errado para "
        "a categoria (2), rosto/mãos deformados ou personagens feios/comuns/envelhecidos (6), "
        "ou emoção morna (1). Senão approved=true. Dê score 0–10, liste `issues` (o que está "
        "errado) e `suggestions` (frases CURTAS e ACIONÁVEIS pra corrigir no prompt da próxima "
        "tentativa — ex.: 'capa mais clara/high-key', 'chefão com cabelo mais curto slicked-back', "
        "'mais emoção/lágrimas no rosto dela', 'rostos mais esculpidos/deity-level beauty').\n\n"
        "SAÍDA: escreva SÓ o arquivo `thumbs/thumb_qa.json` (UTF-8) com Write, EXATAMENTE neste "
        "formato (sem nada antes/depois):\n"
        '{"approved": true, "score": 8, "issues": [], "suggestions": [], "verdict": "<1 linha>"}'
    )


def avaliar(proj, log, cancel=None):
    """Roda o QA Opus sobre thumbs/thumb_01.png e devolve o dict do veredito (ou None).

    None = QA desligado, sem thumb, ou erro (o s6 trata None como 'segue sem bloquear')."""
    thumb = proj.thumbs_dir / "thumb_01.png"
    if not proj.existe(thumb):
        return None
    if not ligado():
        log("    QA da capa DESLIGADO (LONGFORM_THUMB_QA=0) — pulando avaliação.")
        return {"approved": True, "skipped": True}

    # limpa veredito anterior pra não ler um stale se o agente falhar
    try:
        if proj.thumb_qa.exists():
            proj.thumb_qa.unlink()
    except OSError:
        pass

    log("    🔎 QA da capa (Claude/%s) avaliando thumb_01.png contra o padrão do canal..." % MODELO_QA)
    try:
        rodar_claude(_instr(), proj.dir, log, cancel, modelo=MODELO_QA,
                     allowed_tools="Read Write")
    except Exception as e:  # noqa: BLE001 — QA nunca derruba a esteira
        log("    ⚠ QA da capa falhou (%s) — seguindo sem bloquear." % e)
        return None

    if not proj.thumb_qa.exists():
        log("    ⚠ QA não gravou thumb_qa.json — seguindo sem bloquear.")
        return None
    try:
        d = json.loads(proj.thumb_qa.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log("    ⚠ thumb_qa.json ilegível (%s) — seguindo sem bloquear." % e)
        return None

    ap = bool(d.get("approved"))
    score = d.get("score", "?")
    if ap:
        log("    ✓ QA APROVOU a capa (score %s): %s" % (score, d.get("verdict", "")))
    else:
        issues = "; ".join(d.get("issues", []) or []) or d.get("verdict", "")
        log("    ✗ QA REPROVOU a capa (score %s): %s" % (score, issues))
    return d
