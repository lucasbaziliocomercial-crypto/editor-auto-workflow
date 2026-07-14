# -*- coding: utf-8 -*-
"""Etapa 5 — Character bible + fichas de referência + prompt da thumbnail.

Usa a skill `longform-prompts-img` (Modo A): lê roteiro.txt (+ thumb_ref.png se houver) e gera
  - style_bible.txt          (CHARACTER BIBLE: DNA visual + ficha de cada personagem)
  - prompts_referencia.txt   (UMA ficha corpo-inteiro/fundo-branco por personagem -> viram
                              *characters* na Library do Magnific na Etapa 6 = lock real)
  - prompts_thumbnail.txt    (1 prompt de thumbnail 16:9 — a CAPA do vídeo)

A thumb (capa) é única: a Etapa 6 gera só ela, o Gate 2 valida, e a Etapa 7 deriva dela as
8 imagens que formam o CORPO do vídeo. Os prompts dessas imagens NÃO saem aqui — são
derivados na Etapa 7, ancorados nas fichas (Library) + thumb confirmada.
"""

import json

import categorias
from common import ErroPipeline
from runner import rodar_claude, montar_prompt, ler_skill, skill_slot_vazio, MODELO_PROMPTS

N_THUMBS = 1


def run(proj, log, cancel=None, n_thumbs=N_THUMBS, **_):
    if not proj.existe(proj.roteiro):
        raise ErroPipeline("Falta roteiro.txt para gerar character bible + fichas + prompts de thumb.")
    if (proj.existe(proj.style_bible) and proj.existe(proj.prompts_referencia)
            and proj.existe(proj.prompts_thumb)):
        log("    style_bible.txt + prompts_referencia.txt + prompts_thumbnail.txt já existem — Etapa 5 pulada.")
        return

    titulo = ""
    thumb_brief = ""
    if proj.existe(proj.source):
        try:
            _src = json.loads(proj.source.read_text(encoding="utf-8"))
            titulo = _src.get("titulo", "") or ""
            thumb_brief = (_src.get("thumb_brief") or "").strip()
        except Exception:
            pass

    # BLOCO "Thumb:" do card (orientação escrita da Heloyse) = O PADRÃO. É a VERDADE da AÇÃO da
    # capa e tem precedência sobre a imagem de referência. Sem ele, a capa seguia só a referência/
    # skill e ignorava o que o card pedia (ex.: card 3 dizia "ele cheirando o pescoço dela" e a
    # capa saiu só os dois se olhando). Por isso injetamos o thumb_brief explicitamente aqui.
    if thumb_brief:
        brief_ctx = (
            "ORIENTAÇÃO DA THUMB ESCRITA NO CARD (rótulo \"Thumb:\") — este é o PADRÃO, "
            "OBEDEÇA À RISCA: «%s». Esta orientação DEFINE a AÇÃO/cena/figurino/clima da capa e "
            "TEM PRECEDÊNCIA sobre a imagem de referência: se a referência mostrar uma ação "
            "diferente da descrita aqui, vale o que está ESCRITO no card. " % thumb_brief
        )
    else:
        brief_ctx = (
            "O card não trouxe rótulo \"Thumb:\" com orientação escrita — siga a referência (se "
            "houver) e o padrão de capa da skill. "
        )

    tem_ref = proj.existe(proj.thumb_ref)
    if tem_ref:
        ref_ctx = (
            "REFERÊNCIA DE THUMB: existe `thumb_ref.png` nesta pasta (anexo do card). ABRA-A "
            "com Read e DESCREVA-A (composição, personagens, enquadramento, cores, expressão, "
            "texto/elementos gráficos). Use-a como base de ESTILO/vibe/composição — mas a AÇÃO da "
            "cena segue a ORIENTAÇÃO ESCRITA no card (acima) quando as duas divergirem —, "
            "adaptada ao TÍTULO e aos personagens do style_bible."
        )
    else:
        ref_ctx = (
            "Não há thumb_ref.png (o card não trouxe anexo). Monte a thumb seguindo a ORIENTAÇÃO "
            "ESCRITA no card (acima); na falta dela, a partir do roteiro + título, no padrão de "
            "thumb da skill."
        )

    extra = (
        "MODO A (Etapa 5). " +
        ("TÍTULO do vídeo (use como contexto do tema da thumb): %s\n\n" % titulo if titulo else "")
        + brief_ctx + "\n\n"
        + "Leia o roteiro.txt desta pasta. " + ref_ctx + "\n\nGere TRÊS arquivos:\n"
        "1) `style_bible.txt` — a CHARACTER BIBLE (VISUAL DNA + ficha de cada personagem: "
        "idade/porte, rosto/cabelo/olhos/pele, figurino padrão com cores/classe social, "
        "personalidade). É a VERDADE de consistência.\n"
        "2) `prompts_referencia.txt` — UMA ficha por personagem principal (corpo inteiro, "
        "fundo branco, pose neutra), começando com a tag [Character N: NAME] — são as imagens "
        "que viram *characters* na Library do Magnific (lock real). Nome idêntico ao da bible.\n"
        "3) `prompts_thumbnail.txt` — UM ÚNICO prompt de thumbnail (a CAPA do vídeo), 16:9, "
        "começando com as tags [Character N: NAME] que aparecem nela. A AÇÃO/cena tem de cumprir "
        "a ORIENTAÇÃO ESCRITA no card (acima); a referência guia só estilo/composição. É a única "
        "thumb — capriche na composição/contraste. NÃO gere os prompts das imagens internas do "
        "vídeo (isso é a Etapa 7)."
    )
    # OVERRIDE DE CAPA por categoria: a skill compartilhada traz o formato da Selena (Alpha
    # King medieval). Categorias com capa diferente (ex.: Máfia) injetam aqui a própria
    # ESPECIFICAÇÃO DE CAPA, COM PRECEDÊNCIA sobre o bloco "FORMATO DAS NOSSAS CAPAS
    # (selena/Alpha King)" e sobre os presets de cenário da skill. Sem override (Selena) =
    # comportamento de sempre. A bible/fichas e a Etapa 7 (skill compartilhada) não mudam.
    skill_capa = categorias.skill_thumb_override()
    if skill_capa:
        if skill_slot_vazio(skill_capa):
            log("    ⚠ A skill de capa da categoria (%s) está com o PROMPT MESTRE em branco "
                "('(inserir)') — preencha-a antes de gerar (a capa cairá no formato Selena)." % skill_capa)
        extra += (
            "\n\n=== OVERRIDE DE CAPA DA CATEGORIA (PRECEDÊNCIA MÁXIMA sobre a skill) ===\n"
            "Para a THUMBNAIL (prompts_thumbnail.txt), IGNORE o bloco 'FORMATO DAS NOSSAS CAPAS "
            "(selena / Alpha King)' e os PRESETS DE CENÁRIO da skill. Use EXCLUSIVAMENTE a "
            "especificação de capa abaixo (cenário/era, formato dos leads, luz/cor). As demais "
            "regras de ouro da capa (sem texto na imagem, anti-moderação, emoção em primeiro "
            "plano, obedecer ao 'Thumb:' do card, ler a referência) continuam valendo.\n\n"
            + ler_skill(skill_capa)
        )
    _marcas = (("orientação do card" if thumb_brief else None),
               ("referência" if tem_ref else None),
               ("capa: %s" % skill_capa if skill_capa else None))
    _marcas = ", ".join(m for m in _marcas if m)
    log("▶ Etapa 5/8 — Character bible + fichas + prompt da thumb%s (%s)..."
        % (" (%s)" % _marcas if _marcas else "", MODELO_PROMPTS))
    rodar_claude(montar_prompt("longform-prompts-img", extra),
                 proj.dir, log, cancel, modelo=MODELO_PROMPTS)
    faltando = [nome for nome, p in (
        ("style_bible.txt", proj.style_bible),
        ("prompts_referencia.txt", proj.prompts_referencia),
        ("prompts_thumbnail.txt", proj.prompts_thumb),
    ) if not proj.existe(p)]
    if faltando:
        raise ErroPipeline("Etapa 5 não gerou: %s." % ", ".join(faltando))
    log("    ✓ style_bible.txt + prompts_referencia.txt + prompts_thumbnail.txt prontos.")
