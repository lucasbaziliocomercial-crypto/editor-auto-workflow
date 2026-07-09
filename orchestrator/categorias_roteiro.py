# -*- coding: utf-8 -*-
"""categorias_roteiro.py — categorias (boards do ClickUp) + listagem de cards por categoria.

Cada categoria aponta pra uma LISTA do ClickUp (list_id). A GUI usa isto pra preencher o
dropdown de cards. O token REST lê /list/<id>/task (listagem por lista funciona mesmo nos
boards "compartilhados" — o que NÃO funciona é navegar o space/folder).

PENDÊNCIA: preencher os list_id de Mafia/Kay/Lena (o editor vai passar). Rowan já vem
preenchido (é o board do card de teste). Descubra o id de uma lista abrindo um card dela e
rodando: py -3 orchestrator/clickup_api.py --lists   (ou peça o id da URL .../v/li/<ID>).
"""

import clickup_api

# nome exibido -> {list_id}. Ordem = ordem no dropdown. IDs extraídos das URLs de board
# do ClickUp (o número do meio em .../v/b/6-<LIST_ID>-2). Todos validados via REST.
CATEGORIAS = {
    "Lena Principal": {"list_id": "901321530998", "canal": "Lena"},
    "Lena Spice":     {"list_id": "901326666252", "canal": "Lena 2 (Spice)"},
    "Kay":            {"list_id": "901321796470", "canal": "Kay"},
    "Rowan":          {"list_id": "901326639236", "canal": "Rowan"},
}


def canal_de(categoria):
    """Nome do canal (List) da categoria — usado pelo roteador de voz (common.voz_do_canal)."""
    return (CATEGORIAS.get(categoria) or {}).get("canal", categoria or "")

# Status oficial de produção onde os cards aparecem ("voz e edição").
STATUS_VOZ_EDICAO = {"voz/edição", "voz e edição", "voz/edicao", "voz e edicao", "voz/edicão"}


def nomes():
    return list(CATEGORIAS.keys())


def list_id_de(categoria):
    return (CATEGORIAS.get(categoria) or {}).get("list_id", "")


def listar_cards(categoria, so_voz_edicao=False, incluir_concluidos=False):
    """Lista os cards de uma categoria via REST (/list/<id>/task, paginado).

    Devolve [{id, name, status}] (mais novos primeiro). Se `so_voz_edicao`, filtra só os
    cards no status de produção. Levanta ValueError se a categoria não tem list_id."""
    lid = list_id_de(categoria)
    if not lid:
        raise ValueError(
            "Categoria '%s' ainda não tem list_id configurado em categorias_roteiro.py." % categoria)
    out = []
    page = 0
    while True:
        r = clickup_api._get("/list/%s/task" % lid, params={
            "archived": "false", "subtasks": "false",
            "include_closed": "true" if incluir_concluidos else "false",
            "page": page,
        })
        tasks = r.get("tasks", []) if isinstance(r, dict) else []
        for t in tasks:
            st = ((t.get("status") or {}).get("status") or "").strip()
            if so_voz_edicao and st.lower() not in {s.lower() for s in STATUS_VOZ_EDICAO}:
                continue
            out.append({"id": t.get("id"), "name": t.get("name") or "", "status": st})
        if len(tasks) < 100:  # última página
            break
        page += 1
    return out


if __name__ == "__main__":
    import sys, config  # noqa: F401
    cat = sys.argv[1] if len(sys.argv) > 1 else "Rowan (teste)"
    voz = "--voz" in sys.argv
    cards = listar_cards(cat, so_voz_edicao=voz)
    print("%d cards em '%s'%s:" % (len(cards), cat, " (só voz/edição)" if voz else ""))
    for c in cards[:40]:
        print("  [%-16s] %s  (%s)" % (c["status"], c["name"][:50], c["id"]))
