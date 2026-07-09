# -*- coding: utf-8 -*-
"""categorias.py — categorias de produção (= franquia / board do ClickUp).

Cada categoria aponta para UMA List do ClickUp. Escolher a categoria RESTRINGE a
fonte dos cards (dropdown da GUI + busca da Etapa 1) só àquela List — em vez de varrer
o workspace inteiro. É o "você só deixa a LENA/Máfia na lista de cards".

Hoje (2026-06-22):
  - selena (Selena / Alpha King) -> List "Selena (AUTOMAÇÃO)"
  - mafia                        -> List "Máfia (AUTOMAÇÃO)"

Para adicionar/trocar uma categoria, edite só o dicionário CATEGORIAS abaixo.
`aplicar()` injeta LONGFORM_CLICKUP_LIST (e, quando conhecido, LONGFORM_CLICKUP_SPACES)
no ambiente; o clickup_api.py já usa LONGFORM_CLICKUP_LIST para listar SÓ aquela List
(REST e — desde esta mudança — também o fallback pelo login do Claude).
"""

import os

# chave canônica -> configuração da categoria.
#   label     = rótulo exibido na GUI / CLI.
#   list_id   = ID NUMÉRICO da List do ClickUp (sai da URL .../v/li/<ID>). É o que vai pro
#               LONGFORM_CLICKUP_LIST — robusto (independe de acento/nome) e funciona mesmo
#               com a lista em "Shared with me" (o token convidado acessa por ID, não enumera).
#   list_name = nome legível da List (logs + dica de busca da Etapa 1 + fallback sem token).
#   spaces    = CSV de Spaces (backup p/ o fallback sem token; "" = usa o default do clickup_api).
#   aliases   = como o usuário pode escrever a categoria (case/acentos-insensível).
#   skill_roteiro        = skill (~/.claude/commands/<nome>.md) do PROMPT MESTRE do roteiro
#                          (Etapa 2). Default = "longform-roteiro" (Selena/Alpha King).
#   skill_thumb_override = skill com a ESPECIFICAÇÃO DE CAPA específica da categoria, injetada
#                          como OVERRIDE na Etapa 5 (precedência sobre o formato selena da skill
#                          compartilhada). None = usa o formato padrão (Selena) da skill.
CATEGORIAS = {
    "selena": {
        "label": "Selena (Alpha King)",
        "list_id": "901327552227",          # lista "Selena (AUTOMAÇÃO)" (Shared with me)
        "list_name": "Selena (AUTOMAÇÃO)",
        "spaces": "Selena,Selena 2",
        "aliases": ("lena", "selena", "alpha king", "alphaking", "selena automacao",
                    "lena automacao", "selena (automação)"),
        "skill_roteiro": "longform-roteiro",
        "skill_thumb_override": None,
    },
    "mafia": {
        "label": "Máfia",
        "list_id": "901327627550",           # lista "Máfia (AUTOMAÇÃO)" (Shared with me)
        "list_name": "Máfia (AUTOMAÇÃO)",
        "spaces": "",
        "aliases": ("mafia", "máfia", "mafia automacao", "máfia automação",
                    "máfia (automação)"),
        "skill_roteiro": "longform-roteiro-mafia",
        "skill_thumb_override": "longform-thumb-mafia",
    },
}

PADRAO = "selena"


def _norm(s):
    return (s or "").strip().casefold()


def resolver(nome):
    """nome (chave/label/alias, case e acento-insensível) -> chave canônica.
    Vazio ou desconhecido -> PADRAO (nunca quebra)."""
    n = _norm(nome)
    if not n:
        return PADRAO
    if n in CATEGORIAS:
        return n
    for chave, cfg in CATEGORIAS.items():
        if n == _norm(cfg["label"]) or n in {_norm(a) for a in cfg.get("aliases", ())}:
            return chave
    return PADRAO


def config_de(nome):
    return CATEGORIAS[resolver(nome)]


def lista_env(nome):
    """Valor que vai pro LONGFORM_CLICKUP_LIST: o ID numérico (preferido) ou, se ainda não
    houver ID configurado, o NOME da List (o clickup_api resolve por nome quando dá)."""
    cfg = config_de(nome)
    return cfg.get("list_id") or cfg.get("list_name")


def nome_lista_de(nome):
    """Nome legível da List (logs + dica de busca da Etapa 1)."""
    return config_de(nome).get("list_name") or lista_env(nome)


def label_de(nome):
    return config_de(nome)["label"]


def skill_roteiro(nome=None):
    """Skill do PROMPT MESTRE do roteiro (Etapa 2) da categoria (default = a atual no ambiente).
    Default seguro "longform-roteiro" (Selena) p/ categoria sem o campo configurado."""
    cfg = config_de(nome if nome is not None else atual())
    return cfg.get("skill_roteiro") or "longform-roteiro"


def skill_thumb_override(nome=None):
    """Skill com a ESPECIFICAÇÃO DE CAPA específica da categoria, injetada como override na
    Etapa 5. None = sem override (usa o formato padrão Selena da skill compartilhada)."""
    cfg = config_de(nome if nome is not None else atual())
    return cfg.get("skill_thumb_override")


def labels():
    """[(chave, label)] na ordem de declaração — para popular o dropdown da GUI."""
    return [(k, c["label"]) for k, c in CATEGORIAS.items()]


def aplicar(nome):
    """Fixa a categoria no ambiente: restringe a fonte de cards do ClickUp à List dela.

    Override EXPLÍCITO (escolha do usuário) — sobrescreve qualquer LONGFORM_CLICKUP_LIST
    vindo do longform.env. Devolve a chave canônica aplicada."""
    chave = resolver(nome)
    cfg = CATEGORIAS[chave]
    os.environ["LONGFORM_CATEGORIA"] = chave
    os.environ["LONGFORM_CLICKUP_LIST"] = lista_env(chave)
    if cfg.get("spaces"):
        os.environ["LONGFORM_CLICKUP_SPACES"] = cfg["spaces"]
    return chave


def atual():
    """Categoria atualmente fixada no ambiente (default PADRAO)."""
    return resolver(os.environ.get("LONGFORM_CATEGORIA"))
