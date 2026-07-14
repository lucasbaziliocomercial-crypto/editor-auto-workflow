# -*- coding: utf-8 -*-
"""runner.py — motor de execução headless (porta de novela_orquestra.py).

Roda `claude -p --output-format stream-json` na pasta do projeto, com retry/backoff
em erro transitório da API, e roda scripts .py mecânicos (Whisper etc.) transmitindo
a saída pro log. As skills (slash commands) são lidas de ~/.claude/commands e
montadas com um preâmbulo headless.
"""

import os
import re
import json
import time
import subprocess
from pathlib import Path

from common import achar_claude, achar_python, COMMANDS_DIR, ErroPipeline, SUBPROCESS_FLAGS

TIMEOUT_CLAUDE = 3600   # long-form gera roteiro/validação em várias seções — folga maior
TIMEOUT_SCRIPT = 1800

CLAUDE_MAX_TENTATIVAS = 4   # 1 tentativa + até 3 re-tentativas
CLAUDE_BACKOFF_BASE = 8     # 8 -> 16 -> 32 s

_RE_ERRO_TRANSITORIO = re.compile(
    r"overload|rate.?limit|\b429\b|\b529\b"
    r"|internal server error|service unavailable|bad gateway|gateway timeout"
    r"|timed out|\btimeout\b|connection (?:error|reset)|econnreset"
    r"|temporarily|please try again|try again later",
    re.I,
)

# Tiering de modelo por etapa (mesma filosofia do TINAGO: qualidade onde importa).
MODELO_ROTEIRO = "opus"     # geração criativa do roteiro de ~5.000 palavras
MODELO_VALIDAR = "opus"     # validador-gate: nota + gravidade + auto-fix
MODELO_CLICKUP = "sonnet"   # leitura estruturada de card (mecânico)
MODELO_PROMPTS = "sonnet"   # style bible + prompts de thumb
MODELO_IMG_PROMPTS = "sonnet"  # derivar prompts de imagem a partir da thumb
# Humanização = normalização de pontuação/formatação (NÃO muda palavras) → tarefa mecânica;
# Haiku dá conta e é bem mais rápido que o Sonnet (era ~6 min). Override: LONGFORM_MODELO_HUMANIZAR.
MODELO_HUMANIZAR = os.environ.get("LONGFORM_MODELO_HUMANIZAR", "haiku").strip() or "haiku"

# Effort de raciocínio por etapa (--effort: low/medium/high/xhigh/max). None = NÃO passa o flag
# (herda o default da sessão). Só definimos onde há ganho claro e seguro:
#   - roteiro 'low'    → VELOCIDADE. O default da sessão (settings.json effortLevel) era 'low' e foi
#                        o que gerou TODOS os vídeos publicados (~10-11 min, qualidade aceita). Medição
#                        2026-06-19: subir p/ 'high' deixou o roteiro ~6 min MAIS LENTO sem ganho pedido
#                        → revertido p/ 'low'. Suba p/ medium/high/xhigh se quiser enredo mais encorpado
#                        (custo ~+6 min por nível). Override: LONGFORM_EFFORT_ROTEIRO.
#   - humanizar 'low'  → normalização mecânica não precisa de raciocínio.
EFFORT_ROTEIRO = os.environ.get("LONGFORM_EFFORT_ROTEIRO", "low").strip() or None
EFFORT_HUMANIZAR = os.environ.get("LONGFORM_EFFORT_HUMANIZAR", "low").strip() or None


PREAMBULO = """MODO AUTOMÁTICO (headless) — você foi chamado por um orquestrador, sem humano no chat.
- A PASTA DE TRABALHO é o diretório atual (.). Os arquivos de entrada estão aqui e a saída vai aqui.
- NÃO faça perguntas, NÃO pause, NÃO espere validação humana (ela acontece fora, no painel).
- Onde a instrução mandar PARAR e perguntar, ASSUMA o default mais razoável e ANOTE no resumo final.
- Salve o(s) arquivo(s) pedido(s) com Write/Edit. NÃO rode shell a menos que a tarefa peça.

Siga as instruções da skill abaixo:

=== SKILL ===
"""


def ler_skill(nome):
    """Lê a skill `nome`.md de ~/.claude/commands, removendo o frontmatter YAML."""
    p = COMMANDS_DIR / (nome + ".md")
    if not p.is_file():
        raise ErroPipeline("Skill não encontrada: %s" % p)
    txt = p.read_text(encoding="utf-8")
    if txt.lstrip().startswith("---"):
        corpo = txt.lstrip()
        fim = corpo.find("\n---", 3)
        if fim != -1:
            txt = corpo[fim + 4:]
    return txt.strip()


def montar_prompt(skill_nome, extra=""):
    partes = [PREAMBULO, ler_skill(skill_nome)]
    if extra:
        partes.append("\n=== CONTEXTO ADICIONAL DO ORQUESTRADOR ===\n" + extra)
    return "\n".join(partes)


# Sentinela dos slots de PROMPT MESTRE ainda não preenchidos (ex.: categoria nova de Máfia
# criada com o prompt em branco para o usuário colar depois).
SENTINELA_SLOT = "(inserir)"


def skill_slot_vazio(nome):
    """True se a skill `nome` ainda tem um slot de PROMPT MESTRE em branco (sentinela
    "(inserir)") — serve para AVISAR que a categoria existe mas o prompt mestre não foi
    preenchido. Nunca levanta (skill inexistente -> False)."""
    try:
        return SENTINELA_SLOT in ler_skill(nome)
    except ErroPipeline:
        return False


# ---------------------------------------------------------------------------
# Streaming do stream-json -> log legível
# ---------------------------------------------------------------------------

def _checar_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise ErroPipeline("Cancelado pelo usuário.")


def _dormir_cancelavel(segundos, cancel):
    fim = time.monotonic() + segundos
    while True:
        restante = fim - time.monotonic()
        if restante <= 0:
            return
        _checar_cancel(cancel)
        time.sleep(min(0.5, restante))


def _resumir_linha(linha):
    s = linha.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return linha[:200]
    try:
        ev = json.loads(s)
    except Exception:
        return linha[:200]
    tipo = ev.get("type")
    if tipo == "stream_event":
        return None
    if tipo == "rate_limit_event":
        info = ev.get("rate_limit_info") or {}
        status = info.get("status")
        if status and status != "allowed":
            return "⚠ limite de uso (%s): %s" % (info.get("rateLimitType", "?"), status)
        return None
    if tipo == "system":
        return "(sessão iniciada)"
    if tipo == "assistant":
        try:
            for b in ev["message"]["content"]:
                if b.get("type") == "text" and b.get("text", "").strip():
                    return "assistant: " + b["text"].strip().replace("\n", " ")[:160]
                if b.get("type") == "tool_use":
                    return "tool: %s" % b.get("name", "?")
        except Exception:
            pass
        return "(assistant)"
    if tipo == "user":
        return "(tool result)"
    if tipo == "result":
        custo = ev.get("total_cost_usd")
        dur = ev.get("duration_ms")
        partes = []
        if isinstance(dur, (int, float)):
            partes.append("%.0fs" % (dur / 1000.0))
        if isinstance(custo, (int, float)):
            partes.append("$%.4f" % custo)
        return "(fim%s)" % ((" — " + ", ".join(partes)) if partes else "")
    return linha[:200]


def _stream(proc, log, cancel, prefixo="    "):
    linhas = []
    assert proc.stdout is not None
    for linha in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.terminate()
            raise ErroPipeline("Cancelado pelo usuário.")
        linha = linha.rstrip("\n")
        if not linha.strip():
            continue
        linhas.append(linha)
        resumo = _resumir_linha(linha)
        if resumo:
            log(prefixo + resumo)
    proc.wait()
    return linhas


def _extrair_result(linhas):
    for linha in reversed(linhas):
        s = linha.strip()
        if not (s.startswith("{") and s.endswith("}")):
            continue
        try:
            ev = json.loads(s)
        except Exception:
            continue
        if ev.get("type") == "result" or "total_cost_usd" in ev or "result" in ev:
            return ev
    return {}


# ---------------------------------------------------------------------------
# Execução de claude / scripts
# ---------------------------------------------------------------------------

def rodar_claude(prompt, pasta, log, cancel=None, modelo=None,
                 allowed_tools="Read Edit Write", effort=None):
    """Roda `claude -p` headless na pasta. Devolve o dict 'result'.

    `allowed_tools`: string separada por espaço de ferramentas liberadas (a Etapa 1
    estende com as ferramentas do MCP do ClickUp). Re-tenta em erro transitório com
    backoff, mantendo o MESMO modelo (não degrada qualidade).
    `effort`: nível de raciocínio (--effort low/medium/high/xhigh/max). None = herda o
    default da sessão (não passa o flag).
    """
    cmd = achar_claude() + [
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "acceptEdits",
        "--allowedTools", allowed_tools,
    ]
    if modelo:
        cmd += ["--model", modelo]
    if effort:
        cmd += ["--effort", effort]
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # força o login/assinatura do Claude Code

    ultimo_erro = None
    for tent in range(1, CLAUDE_MAX_TENTATIVAS + 1):
        _checar_cancel(cancel)
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(pasta), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True, encoding="utf-8", errors="replace",
                **SUBPROCESS_FLAGS,
            )
        except FileNotFoundError as e:
            raise ErroPipeline("Falha ao iniciar o claude: %s" % e)

        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except Exception:
            pass

        linhas = _stream(proc, log, cancel)
        res = _extrair_result(linhas)
        if proc.returncode == 0 and not res.get("is_error"):
            return res

        msg_res = str(res.get("result", ""))
        if proc.returncode != 0:
            ultimo_erro = "claude retornou código %d (veja o log acima)." % proc.returncode
        else:
            ultimo_erro = "claude reportou erro: %s" % msg_res[:300]

        transitorio = bool(_RE_ERRO_TRANSITORIO.search("\n".join(linhas))) \
            or bool(_RE_ERRO_TRANSITORIO.search(msg_res))
        if transitorio and tent < CLAUDE_MAX_TENTATIVAS:
            espera = CLAUDE_BACKOFF_BASE * (2 ** (tent - 1))
            log("⚠ erro transitório da API (tentativa %d/%d): %s — re-tentando em %ds..."
                % (tent, CLAUDE_MAX_TENTATIVAS, ultimo_erro, espera))
            _dormir_cancelavel(espera, cancel)
            continue
        break

    raise ErroPipeline(ultimo_erro or "claude falhou sem detalhe.")


def rodar_script(args, pasta, log, cancel=None):
    """Roda um script .py (Whisper / build-mapping) transmitindo a saída pro log."""
    _checar_cancel(cancel)
    cmd = achar_python() + [str(a) for a in args]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        cmd, cwd=str(pasta), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, encoding="utf-8", errors="replace",
        **SUBPROCESS_FLAGS,
    )
    _stream(proc, log, cancel, prefixo="    ")
    if proc.returncode != 0:
        raise ErroPipeline("Script falhou (código %d): %s" % (proc.returncode, " ".join(cmd)))
