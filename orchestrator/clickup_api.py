# -*- coding: utf-8 -*-
"""clickup_api.py — cliente REST do ClickUp (sem dependências externas).

Usado para LISTAR os vídeos disponíveis (cards não-concluídos) de UMA List do ClickUp,
para popular o dropdown da GUI. É o caminho "API de verdade": uma chamada HTTP direta,
determinística e instantânea — não usa IA nem o conector headless.

Config (via ambiente / longform.env):
    LONGFORM_CLICKUP_TOKEN  -> token pessoal do ClickUp (pk_...). OBRIGATÓRIO.
    LONGFORM_CLICKUP_LIST   -> a List dos vídeos long-form. Pode ser:
                                 - o ID numérico da List (recomendado — sai da URL da List), ou
                                 - o NOME exato da List (resolvido varrendo o workspace).

Um card é "concluído" (e some da lista) quando o TIPO do seu status é "done" ou "closed".
Os demais (to do / in progress / custom abertos) são os "disponíveis".

CLI de teste (confirme que o token funciona antes de mexer na GUI):
    py -3 longform/orchestrator/clickup_api.py --list
    py -3 longform/orchestrator/clickup_api.py --lists   # mostra as Lists do workspace + IDs
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from common import ErroPipeline, PROJECTS_DIR

API = "https://api.clickup.com/api/v2"

# Tipos de status que contam como CONCLUÍDO (o card some do dropdown).
STATUS_CONCLUIDO = {"done", "closed"}

# Spaces (boards) padrão de onde vêm os vídeos long-form. Sobrescrevível por env
# (LONGFORM_CLICKUP_SPACES, CSV de NOMES ou IDs). A usuária produz a partir de Selena + Selena 2.
SPACES_PADRAO = "Selena,Selena 2"

# Status que NÃO são de vídeo (cards de gestão do canal) — escondidos do dropdown.
# CSV de nomes de status (case-insensitive). Sobrescrevível por LONGFORM_CLICKUP_SKIP_STATUS.
SKIP_STATUS_PADRAO = "canal,infoproduto"

# Prefixo do conector ClickUp do login do Claude (mesmo que a Etapa 1 usa) — só p/ o
# fallback SEM token, via `claude -p` headless.
CLICKUP_MCP = os.environ.get("LONGFORM_CLICKUP_MCP", "mcp__claude_ai_ClickUp")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _token():
    tok = (os.environ.get("LONGFORM_CLICKUP_TOKEN") or "").strip()
    if not tok:
        raise ErroPipeline(
            "Token do ClickUp não configurado. Crie um token pessoal em "
            "ClickUp → Settings → Apps → API Token (começa com 'pk_') e coloque em "
            "longform/longform.env como:  LONGFORM_CLICKUP_TOKEN=pk_xxxxxxxx"
        )
    return tok


def _list_ref():
    return (os.environ.get("LONGFORM_CLICKUP_LIST") or "").strip()


def _csv(valor):
    return [s.strip() for s in (valor or "").split(",") if s.strip()]


def _spaces_ref():
    """Spaces de onde listar (CSV de nomes/IDs). Default: Selena + Selena 2."""
    return _csv(os.environ.get("LONGFORM_CLICKUP_SPACES") or SPACES_PADRAO)


def _tem_token():
    return bool((os.environ.get("LONGFORM_CLICKUP_TOKEN") or "").strip())


def _skip_status():
    return {s.casefold() for s in _csv(os.environ.get("LONGFORM_CLICKUP_SKIP_STATUS") or SKIP_STATUS_PADRAO)}


def _pular_status(item):
    """True se o card é de gestão do canal (status fora de produção) — some do dropdown."""
    return (item.get("status") or "").casefold() in _skip_status()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _get(path, params=None):
    """GET autenticado no /api/v2. Devolve o JSON já parseado (dict)."""
    return _req("GET", path, params=params)


def _req(metodo, path, params=None, body=None):
    """Requisição autenticada ao /api/v2 (GET/PUT/POST). Devolve o JSON parseado (dict)."""
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    dados = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo, headers={
        "Authorization": _token(),
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            corpo = resp.read().decode("utf-8")
            return json.loads(corpo) if corpo.strip() else {}
    except urllib.error.HTTPError as e:
        corpo = ""
        try:
            corpo = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        if e.code in (401, 403):
            raise ErroPipeline(
                "ClickUp recusou o token (HTTP %s). Confira LONGFORM_CLICKUP_TOKEN em "
                "longform.env (token pessoal 'pk_...', com acesso ao workspace). %s"
                % (e.code, corpo)
            )
        raise ErroPipeline("Erro do ClickUp (HTTP %s) em %s: %s" % (e.code, path, corpo))
    except urllib.error.URLError as e:
        raise ErroPipeline("Sem conexão com o ClickUp (%s). Verifique a internet." % e.reason)


# ---------------------------------------------------------------------------
# Resolução da List (ID direto ou por nome)
# ---------------------------------------------------------------------------

def listar_lists():
    """Varre o workspace e devolve [(nome_list, id_list, caminho)] de TODAS as Lists.
    Usado para resolver a List por nome e para o CLI --lists (descobrir o ID)."""
    out = []
    teams = _get("/team").get("teams", [])
    for team in teams:
        tid = team["id"]
        for space in _get("/team/%s/space" % tid, {"archived": "false"}).get("spaces", []):
            sp = space.get("name", "?")
            # Lists dentro de Folders
            for folder in _get("/space/%s/folder" % space["id"], {"archived": "false"}).get("folders", []):
                fn = folder.get("name", "?")
                for lst in folder.get("lists", []):
                    out.append((lst.get("name", "?"), lst["id"], "%s / %s" % (sp, fn)))
            # Lists soltas (sem Folder)
            for lst in _get("/space/%s/list" % space["id"], {"archived": "false"}).get("lists", []):
                out.append((lst.get("name", "?"), lst["id"], sp))
    return out


def resolver_list_id(ref=None):
    """ref = ID numérico (devolve como está) ou NOME da List (resolve varrendo o workspace)."""
    ref = (ref or _list_ref()).strip()
    if ref.isdigit():
        return ref
    alvo = ref.casefold()
    candidatas = [(n, i, c) for (n, i, c) in listar_lists() if n.casefold() == alvo]
    if not candidatas:
        disponiveis = listar_lists()
        nomes = ", ".join(sorted({n for n, _, _ in disponiveis})) or "(nenhuma)"
        raise ErroPipeline(
            "List '%s' não encontrada no workspace. Lists disponíveis: %s. "
            "Dica: use o ID numérico da List em LONGFORM_CLICKUP_LIST (sai da URL)." % (ref, nomes)
        )
    return candidatas[0][1]


# ---------------------------------------------------------------------------
# Listagem de vídeos disponíveis (não-concluídos)
# ---------------------------------------------------------------------------

def resolver_spaces():
    """Devolve (team_id, [space_ids]) p/ os Spaces em LONGFORM_CLICKUP_SPACES (nomes ou IDs).

    Casa no PRIMEIRO team (workspace) que contiver algum dos Spaces pedidos e devolve
    todos os que casarem ali. A maioria das contas tem um só workspace."""
    refs = _spaces_ref()
    ids = {r for r in refs if r.isdigit()}
    nomes = {r.casefold() for r in refs if not r.isdigit()}
    teams = _get("/team").get("teams", [])
    todos = []
    for team in teams:
        achados = []
        for sp in _get("/team/%s/space" % team["id"], {"archived": "false"}).get("spaces", []):
            todos.append(sp.get("name", "?"))
            if sp["id"] in ids or sp.get("name", "").casefold() in nomes:
                achados.append(sp["id"])
        if achados:
            return team["id"], achados
    raise ErroPipeline(
        "Spaces %s não encontrados no ClickUp. Spaces disponíveis: %s. Ajuste "
        "LONGFORM_CLICKUP_SPACES no longform.env (nomes ou IDs, separados por vírgula)."
        % (", ".join(refs), ", ".join(sorted(set(todos))) or "(nenhum)")
    )


def _normalizar(t):
    st = t.get("status") or {}
    return {
        "id": t.get("id"),
        "name": (t.get("name") or "").strip(),
        "url": t.get("url"),
        "status": st.get("status"),
        "status_type": st.get("type"),
        "space": (t.get("space") or {}).get("name") or (t.get("list") or {}).get("name"),
    }


def _concluido(item):
    return (item.get("status_type") or "").lower() in STATUS_CONCLUIDO


def _listar_por_list(list_id):
    itens, pagina = [], 0
    while True:
        dados = _get("/list/%s/task" % list_id, {
            "archived": "false", "include_closed": "false",
            "subtasks": "false", "page": str(pagina),
        })
        tarefas = dados.get("tasks", [])
        itens += [_normalizar(t) for t in tarefas]
        if dados.get("last_page", True) or not tarefas:
            break
        pagina += 1
    return itens


def _listar_por_spaces():
    team_id, space_ids = resolver_spaces()
    itens, pagina = [], 0
    while True:
        dados = _get("/team/%s/task" % team_id, {
            "space_ids[]": space_ids,    # doseq -> repete a chave p/ cada Space
            "include_closed": "false", "subtasks": "false", "page": str(pagina),
        })
        tarefas = dados.get("tasks", [])
        itens += [_normalizar(t) for t in tarefas]
        if dados.get("last_page", True) or not tarefas:
            break
        pagina += 1
    return itens


def listar_videos_disponiveis():
    """Cards NÃO-concluídos via API REST. Por padrão varre os Spaces (Selena + Selena 2);
    se LONGFORM_CLICKUP_LIST estiver definido, restringe àquela List.

    Cada item: {"id", "name", "url", "status", "status_type", "space"}.
    Cards cujo status é do tipo 'done'/'closed' são OMITIDOS. Dedup por id."""
    if not _tem_token():
        raise ErroPipeline(
            "Token do ClickUp não configurado. Coloque LONGFORM_CLICKUP_TOKEN=pk_... em "
            "longform/longform.env (ClickUp → Settings → Apps → API Token), ou deixe sem "
            "token para listar pelo login do Claude (mais lento)."
        )
    brutos = _listar_por_list(resolver_list_id()) if _list_ref() else _listar_por_spaces()
    vistos, out = set(), []
    for it in brutos:
        if _concluido(it) or _pular_status(it) or not it.get("id") or it["id"] in vistos:
            continue
        vistos.add(it["id"])
        out.append(it)
    out.sort(key=lambda v: (v.get("name") or "").casefold())
    return out


# ---------------------------------------------------------------------------
# Fallback SEM token: lista pelo conector do login do Claude (mesmo da Etapa 1)
# ---------------------------------------------------------------------------

_CONNECTOR_TOOLS = " ".join(CLICKUP_MCP + "__" + t for t in (
    "clickup_get_workspace_hierarchy", "clickup_get_list", "clickup_get_folder",
    "clickup_filter_tasks", "clickup_get_task", "clickup_search",
))
_ALLOWED_CONNECTOR = "Read Write " + _CONNECTOR_TOOLS


def _prompt_connector(spaces):
    lista = ", ".join('"%s"' % s for s in spaces)
    return f"""Você lista cards do ClickUp em MODO AUTOMÁTICO (headless, sem perguntas). Use as
ferramentas do MCP do ClickUp. A pasta de trabalho é o diretório atual (.).

OBJETIVO: listar TODAS as tarefas NÃO-CONCLUÍDAS dos Spaces: {lista}.

PASSOS:
1. clickup_get_workspace_hierarchy (max_depth=2) para localizar esses Spaces e as Lists dentro deles.
2. Para cada List desses Spaces, use clickup_filter_tasks (subtasks=false) e colete as tarefas.
3. EXCLUA as concluídas: status cujo TIPO é "done" ou "closed" (ex.: Concluído, Completo, Done,
   Closed, Arquivado). Na dúvida sobre um status, INCLUA a tarefa.

SAÍDA (obrigatória): salve com Write um arquivo chamado EXATAMENTE `videos.json` na pasta atual,
JSON válido UTF-8, NADA além disso:
{{"videos": [{{"id": "...", "name": "...", "url": "...", "status": "...", "space": "..."}}]}}
Ordene por "name". Se um Space não existir, ignore-o. Imprima só um resumo de 1 linha (quantos cards)."""


def _prompt_connector_list(list_name):
    """Variante do prompt do conector restrita a UMA List por nome (categoria escolhida)."""
    return f"""Você lista cards do ClickUp em MODO AUTOMÁTICO (headless, sem perguntas). Use as
ferramentas do MCP do ClickUp. A pasta de trabalho é o diretório atual (.).

OBJETIVO: listar TODAS as tarefas NÃO-CONCLUÍDAS da List chamada EXATAMENTE "{list_name}"
(e SÓ dessa List — ignore qualquer outra List/Space).

PASSOS:
1. clickup_get_workspace_hierarchy (max_depth=3) e localize a List cujo nome é "{list_name}".
2. Use clickup_filter_tasks nessa List (subtasks=false) e colete as tarefas.
3. EXCLUA as concluídas: status cujo TIPO é "done" ou "closed" (ex.: Concluído, Completo, Done,
   Closed, Arquivado). Na dúvida sobre um status, INCLUA a tarefa.

SAÍDA (obrigatória): salve com Write um arquivo chamado EXATAMENTE `videos.json` na pasta atual,
JSON válido UTF-8, NADA além disso:
{{"videos": [{{"id": "...", "name": "...", "url": "...", "status": "...", "space": "..."}}]}}
Ordene por "name". Se a List não existir, salve {{"videos": []}}. Imprima só um resumo de 1 linha."""


def listar_videos_via_connector(log=None, cancel=None):
    """Lista os vídeos via `claude -p` + conector ClickUp do login (sem token REST)."""
    from common import PROJECTS_DIR
    import runner

    _log = log or (lambda *_a, **_k: None)
    cache = PROJECTS_DIR / "_tmp_clickup"          # prefixo _tmp_ -> ignorado por projeto_mais_recente
    cache.mkdir(parents=True, exist_ok=True)
    saida = cache / "videos.json"
    try:
        saida.unlink()
    except OSError:
        pass

    # Se uma List foi fixada (categoria escolhida), restringe o conector a ELA; senão
    # cai para a varredura por Spaces (comportamento legado).
    prompt = _prompt_connector_list(_list_ref()) if _list_ref() else _prompt_connector(_spaces_ref())
    runner.rodar_claude(prompt, cache, _log, cancel,
                        modelo=runner.MODELO_CLICKUP, allowed_tools=_ALLOWED_CONNECTOR)
    if not saida.exists():
        raise ErroPipeline(
            "Não consegui listar pelo conector do ClickUp (videos.json não foi gerado). "
            "Confirme que o ClickUp está conectado no Claude Code, ou configure "
            "LONGFORM_CLICKUP_TOKEN para usar a API REST."
        )
    try:
        dados = json.loads(saida.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ErroPipeline("videos.json inválido vindo do conector: %s" % e)

    vistos, out = set(), []
    for v in dados.get("videos", []):
        vid, nome = v.get("id"), (v.get("name") or "").strip()
        if not nome or vid in vistos or _pular_status(v):
            continue
        vistos.add(vid)
        out.append({"id": vid, "name": nome, "url": v.get("url"),
                    "status": v.get("status"), "status_type": None, "space": v.get("space")})
    out.sort(key=lambda x: x["name"].casefold())
    return out


# ---------------------------------------------------------------------------
# Cache da lista (mostra na hora ao abrir; atualiza em 2º plano só quando velho)
# ---------------------------------------------------------------------------

def _cache_path():
    return PROJECTS_DIR / "_tmp_clickup" / "cache.json"


def cache_ttl():
    """Idade máx. do cache (s) antes de atualizar em 2º plano. Default 600 (10 min).
    LONGFORM_CLICKUP_CACHE_TTL=0 força atualizar sempre."""
    try:
        return int(os.environ.get("LONGFORM_CLICKUP_CACHE_TTL") or "600")
    except ValueError:
        return 600


def cache_salvar(videos):
    try:
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"ts": time.time(), "videos": videos}, ensure_ascii=False),
                     encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass  # cache é best-effort; nunca quebra a listagem


def cache_ler():
    """Devolve (videos, idade_em_segundos) da última listagem boa, ou ([], None)."""
    p = _cache_path()
    if not p.is_file():
        return [], None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("videos", []), max(0.0, time.time() - float(d.get("ts", 0)))
    except Exception:  # noqa: BLE001
        return [], None


def listar_videos(log=None, cancel=None):
    """Entrada única usada pela GUI: REST se houver token (instantâneo), senão cai para o
    conector do login do Claude (mais lento). Salva o resultado no cache."""
    if _tem_token():
        vids = listar_videos_disponiveis()
    else:
        if log:
            log("   (sem LONGFORM_CLICKUP_TOKEN — listando pelo seu login do Claude; "
                "pode levar alguns segundos. Para ficar instantâneo, configure o token no longform.env.)")
        vids = listar_videos_via_connector(log, cancel)
    cache_salvar(vids)
    return vids


# ---------------------------------------------------------------------------
# Marcar card como CONCLUÍDO (auto, no fim da Etapa 8)
# ---------------------------------------------------------------------------

def auto_done_ligado():
    """True se a auto-conclusão do card está ligada (default ON). Desliga com
    LONGFORM_CLICKUP_AUTO_DONE=0 no longform.env."""
    return (os.environ.get("LONGFORM_CLICKUP_AUTO_DONE") or "1").strip().lower() not in ("0", "false", "no", "off")


def marcar_concluido_rest(card_id):
    """Move o card para o status de tipo done/closed da SUA List. Idempotente."""
    t = _get("/task/%s" % card_id)
    st = t.get("status") or {}
    if (st.get("type") or "").lower() in STATUS_CONCLUIDO:
        return st.get("status")  # já concluído
    list_id = (t.get("list") or {}).get("id")
    if not list_id:
        raise ErroPipeline("Card %s sem List associada — não dá pra achar o status de concluído." % card_id)
    statuses = _get("/list/%s" % list_id).get("statuses", [])
    done = next((s for s in statuses if (s.get("type") or "").lower() in STATUS_CONCLUIDO), None)
    if not done:
        raise ErroPipeline(
            "A List do card %s não tem status do tipo done/closed — defina um status de "
            "'Concluído' nessa List no ClickUp, ou desligue LONGFORM_CLICKUP_AUTO_DONE." % card_id)
    _req("PUT", "/task/%s" % card_id, body={"status": done["status"]})
    return done["status"]


def _prompt_marcar(card_id):
    return f"""Você marca UMA tarefa do ClickUp como CONCLUÍDA, em MODO AUTOMÁTICO (headless, sem
perguntas). Use as ferramentas do MCP do ClickUp.

TAREFA: id "{card_id}".

PASSOS:
1. clickup_get_task(id="{card_id}") — descubra a List da tarefa e o status atual.
2. Se o status atual JÁ for de tipo "done"/"closed" (concluído), NÃO faça nada e termine.
3. Senão, descubra na List dessa tarefa o status cujo TIPO é "done" (ou "closed") — é o de
   CONCLUÍDO (use clickup_get_list se precisar ver os status e seus tipos).
4. clickup_update_task(id="{card_id}", status="<nome EXATO desse status concluído>").

Imprima só 1 linha confirmando o novo status (ou que já estava concluído). NÃO crie arquivos."""


def marcar_concluido_connector(card_id, log=None, cancel=None):
    from common import PROJECTS_DIR
    import runner

    _log = log or (lambda *_a, **_k: None)
    pasta = PROJECTS_DIR / "_tmp_clickup"
    pasta.mkdir(parents=True, exist_ok=True)
    tools = "Read " + " ".join(CLICKUP_MCP + "__" + t for t in (
        "clickup_get_task", "clickup_get_list", "clickup_update_task"))
    runner.rodar_claude(_prompt_marcar(card_id), pasta, _log, cancel,
                        modelo=runner.MODELO_CLICKUP, allowed_tools=tools)
    return "concluído"


def marcar_concluido(card_id, log=None, cancel=None):
    """Marca o card como concluído no ClickUp (REST se houver token, senão pelo login do Claude)."""
    if not card_id:
        return None
    if _tem_token():
        return marcar_concluido_rest(card_id)
    return marcar_concluido_connector(card_id, log, cancel)


# ---------------------------------------------------------------------------
# Anexar arquivo ao card (ex.: a capa aprovada, após o Gate 2)
# ---------------------------------------------------------------------------

def auto_anexar_thumb_ligado():
    """True se o anexo automático da capa no card está ligado (default ON). Desliga com
    LONGFORM_CLICKUP_ATTACH_THUMB=0 no longform.env."""
    return (os.environ.get("LONGFORM_CLICKUP_ATTACH_THUMB") or "1").strip().lower() \
        not in ("0", "false", "no", "off")


def _post_multipart(path, campo, nome_arquivo, conteudo, content_type):
    """POST multipart/form-data ao /api/v2 (o /attachment NÃO aceita JSON). Devolve o JSON
    parseado. Boundary fixo por requisição (uuid4 — não usa relógio nem aleatório fraco)."""
    import uuid
    boundary = "----longformBoundary" + uuid.uuid4().hex
    pre = (
        "--%s\r\n"
        'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
        "Content-Type: %s\r\n\r\n" % (boundary, campo, nome_arquivo, content_type)
    ).encode("utf-8")
    post = ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    body = pre + conteudo + post
    req = urllib.request.Request(API + path, data=body, method="POST", headers={
        "Authorization": _token(),
        "Content-Type": "multipart/form-data; boundary=%s" % boundary,
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            corpo = resp.read().decode("utf-8")
            return json.loads(corpo) if corpo.strip() else {}
    except urllib.error.HTTPError as e:
        corpo = ""
        try:
            corpo = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        if e.code in (401, 403):
            raise ErroPipeline(
                "ClickUp recusou o token ao anexar (HTTP %s). Confira LONGFORM_CLICKUP_TOKEN. %s"
                % (e.code, corpo))
        raise ErroPipeline("Erro do ClickUp ao anexar (HTTP %s) em %s: %s" % (e.code, path, corpo))
    except urllib.error.URLError as e:
        raise ErroPipeline("Sem conexão com o ClickUp ao anexar (%s)." % e.reason)


def anexar_arquivo_rest(card_id, caminho):
    """Anexa um arquivo local ao card via POST /task/{id}/attachment (multipart)."""
    import mimetypes
    p = Path(caminho)
    ct = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return _post_multipart("/task/%s/attachment" % card_id, "attachment",
                           p.name, p.read_bytes(), ct)


def _prompt_anexar(card_id, caminho):
    return f"""Você anexa UM arquivo a uma tarefa do ClickUp, em MODO AUTOMÁTICO (headless, sem
perguntas). Use a ferramenta de anexo do MCP do ClickUp.

TAREFA: id "{card_id}".
ARQUIVO LOCAL (já existe no disco): "{caminho}".

PASSOS:
1. Anexe esse arquivo à tarefa usando a ferramenta de anexo do ClickUp (ex.:
   clickup_attach_task_file), passando o caminho local e o nome do arquivo.
2. Se a ferramenta só aceitar o conteúdo em base64, leia o arquivo (Bash + base64) e converta.

Imprima só 1 linha confirmando o anexo (ou o erro). NÃO crie outros arquivos."""


def anexar_arquivo_connector(card_id, caminho, log=None, cancel=None):
    """Fallback sem token: anexa pelo conector do login do Claude (mesmo da Etapa 1)."""
    import runner

    _log = log or (lambda *_a, **_k: None)
    pasta = PROJECTS_DIR / "_tmp_clickup"
    pasta.mkdir(parents=True, exist_ok=True)
    tools = "Read Bash " + " ".join(CLICKUP_MCP + "__" + t for t in (
        "clickup_get_task", "clickup_attach_task_file", "clickup_create_task_attachment"))
    runner.rodar_claude(_prompt_anexar(card_id, str(caminho)), pasta, _log, cancel,
                        modelo=runner.MODELO_CLICKUP, allowed_tools=tools)
    return "anexado"


def anexar_arquivo(card_id, caminho, log=None, cancel=None):
    """Anexa um arquivo ao card (REST se houver token, senão pelo login do Claude).
    No-op silencioso se faltar card_id ou o arquivo não existir."""
    if not card_id or not caminho or not Path(caminho).is_file():
        return None
    if _tem_token():
        return anexar_arquivo_rest(card_id, caminho)
    return anexar_arquivo_connector(card_id, caminho, log, cancel)


# ---------------------------------------------------------------------------
# CLI de teste
# ---------------------------------------------------------------------------

def _main():
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    import config  # noqa: F401  (carrega longform.env -> os.environ)

    args = sys.argv[1:]
    try:
        if "--lists" in args:
            print("Lists do workspace (nome — ID — caminho):")
            for nome, lid, caminho in listar_lists():
                print("  %-40s  %s  [%s]" % (nome, lid, caminho))
            return
        fonte = ("Lists=" + _list_ref()) if (_tem_token() and _list_ref()) else \
                ("Spaces=" + ", ".join(_spaces_ref())) if _tem_token() else "login do Claude (sem token)"
        print("Fonte: %s" % fonte)
        vids = listar_videos(log=print)
        print("Vídeos disponíveis (não-concluídos): %d" % len(vids))
        for v in vids:
            sp = (" [%s]" % v["space"]) if v.get("space") else ""
            print("  • %-50s%s  (%s)" % (v["name"], sp, v.get("status")))
    except ErroPipeline as e:
        print("\n❌ %s" % e)
        sys.exit(1)


if __name__ == "__main__":
    _main()
