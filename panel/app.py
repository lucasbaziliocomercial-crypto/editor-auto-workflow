# -*- coding: utf-8 -*-
"""Painel web local dos gates de decisão (sem dependências externas — usa http.server).

run_gate(proj, kind) abre um servidor local, abre o navegador e BLOQUEIA até o usuário
decidir. kind:
  - "roteiro": mostra roteiro.txt + resumo da validação; botão Aprovar (o usuário pode
    editar o roteiro.txt à mão antes e clicar Aprovar). Recarrega o texto ao abrir.
  - "thumb": mostra a thumb (capa) gerada; clicar valida -> copia para thumb_selected.png.

Retorna o dict da decisão e grava decision_<kind>.json na pasta do projeto.
"""

import json
import shutil
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs


def _html_roteiro(proj):
    val = ""
    vfile = proj.dir / "roteiro_validacao.json"
    if vfile.exists():
        try:
            d = json.loads(vfile.read_text(encoding="utf-8"))
            linhas = "".join(
                "<li><b>[%s]</b> %s — %s</li>" % (
                    str(i.get("gravidade", "?")).upper(),
                    (i.get("erro") or i.get("problema") or ""),
                    (i.get("corrigido") and "corrigido" or i.get("motivo") or ""))
                for i in d.get("itens", []))
            cab = "<p><b>Score:</b> %s · <b>POV:</b> %s · <b>finalizada:</b> %s</p><p>%s</p>" % (
                d.get("score", "?"), d.get("pov", "?"), d.get("historia_finalizada", "?"),
                d.get("resumo", ""))
            val = cab + ("<ul>%s</ul>" % linhas if linhas else "<p>Sem erros estruturais.</p>")
        except Exception:
            val = "<p>(validação ilegível)</p>"
    return """<!doctype html><meta charset=utf-8><title>Gate 1 — Roteiro</title>
<style>body{font:15px/1.6 system-ui;margin:0;background:#111;color:#eee}
header{position:sticky;top:0;background:#1a1a1a;padding:14px 20px;border-bottom:1px solid #333;display:flex;gap:12px;align-items:center}
button{font:600 15px system-ui;padding:10px 22px;border:0;border-radius:8px;background:#2e7d32;color:#fff;cursor:pointer}
main{max-width:860px;margin:0 auto;padding:24px}
pre{white-space:pre-wrap;background:#161616;padding:18px;border-radius:10px;border:1px solid #333}
.val{background:#161616;padding:12px 18px;border-radius:10px;border:1px solid #333;margin-bottom:18px}</style>
<header><b>Gate 1 — Roteiro</b><span style=color:#999>Revise/edite o roteiro.txt e clique Aprovar.</span>
<span style=flex:1></span><button onclick=aprovar()>✓ Aprovar roteiro</button></header>
<main><div class=val>""" + val + """</div><pre id=t>carregando…</pre></main>
<script>
fetch('/asset?path=roteiro.txt').then(r=>r.text()).then(t=>document.getElementById('t').textContent=t);
function aprovar(){fetch('/decide',{method:'POST',body:JSON.stringify({approved:true})}).then(()=>{document.body.innerHTML='<main><h2>✓ Roteiro aprovado. Pode fechar esta aba.</h2></main>'})}
</script>"""


def _qa_banner(proj):
    """Banner do veredito do QA do Claude (só aparece quando o QA REPROVOU a capa)."""
    qfile = proj.dir / "thumbs" / "thumb_qa.json"
    if not qfile.exists():
        return ""
    try:
        d = json.loads(qfile.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if d.get("approved"):
        return ""
    issues = "".join("<li>%s</li>" % i for i in (d.get("issues") or []))
    sugg = "".join("<li>%s</li>" % s for s in (d.get("suggestions") or []))
    return ("<div class=qa><b>⚠ O QA do Claude sinalizou esta capa</b> "
            "(score %s): %s%s%s</div>" % (
                d.get("score", "?"), d.get("verdict", ""),
                ("<br><u>Problemas:</u><ul>%s</ul>" % issues) if issues else "",
                ("<u>Sugestões:</u><ul>%s</ul>" % sugg) if sugg else ""))


def _html_thumb(proj):
    thumbs = sorted(p.name for p in (proj.dir / "thumbs").glob("thumb_*.png"))
    cards = "".join(
        "<figure onclick=escolher('%s')><img src='/asset?path=thumbs/%s'><figcaption>%s</figcaption></figure>" % (t, t, t)
        for t in thumbs)
    return """<!doctype html><meta charset=utf-8><title>Gate 2 — Thumbnail</title>
<style>body{font:15px/1.6 system-ui;margin:0;background:#111;color:#eee}
header{position:sticky;top:0;background:#1a1a1a;padding:14px 20px;border-bottom:1px solid #333}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px;padding:20px}
figure{margin:0;cursor:pointer;border:2px solid #333;border-radius:10px;overflow:hidden;transition:.15s}
figure:hover{border-color:#2e7d32;transform:translateY(-3px)}
img{width:100%;display:block;aspect-ratio:16/9;object-fit:cover}
figcaption{padding:8px 12px;color:#aaa;font-size:13px}
.qa{margin:14px 20px;padding:12px 18px;background:#3a2317;border:1px solid #7a4a1a;border-radius:10px;color:#ffd9b3}
.qa ul{margin:6px 0 0;padding-left:20px}</style>
<header><b>Gate 2 — Valide a thumbnail (capa)</b> <span style=color:#999>(clique nela para confirmar — a Etapa 7 deriva dela as imagens do corpo)</span></header>
""" + _qa_banner(proj) + """<main>""" + cards + """</main>
<script>
function escolher(t){fetch('/decide',{method:'POST',body:JSON.stringify({choice:t})}).then(()=>{document.body.innerHTML='<main><h2>✓ Thumb '+t+' validada. Pode fechar esta aba.</h2></main>'})}
</script>"""


def run_gate(proj, kind, log=print, cancel=None):
    decisao = {}
    pronto = threading.Event()
    base = Path(proj.dir).resolve()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silencia o log padrão do http.server
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                self._send(200, _html_roteiro(proj) if kind == "roteiro" else _html_thumb(proj))
            elif u.path == "/asset":
                rel = (parse_qs(u.query).get("path") or [""])[0]
                alvo = (base / rel).resolve()
                if not str(alvo).startswith(str(base)) or not alvo.is_file():
                    self._send(404, "not found", "text/plain"); return
                ctype = "image/png" if alvo.suffix.lower() == ".png" else "text/plain; charset=utf-8"
                self._send(200, alvo.read_bytes(), ctype)
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self):
            if urlparse(self.path).path != "/decide":
                self._send(404, "no", "text/plain"); return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                payload = {}
            decisao.update(payload)
            self._send(200, json.dumps({"ok": True}), "application/json")
            pronto.set()

    srv = HTTPServer(("127.0.0.1", 0), H)
    porta = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % porta
    log("    🌐 Painel aberto: %s  (aguardando sua decisão…)" % url)
    try:
        webbrowser.open(url)
    except Exception:
        pass

    # bloqueia até decidir (ou cancelar)
    while not pronto.wait(0.3):
        if cancel is not None and cancel.is_set():
            srv.shutdown()
            raise RuntimeError("Cancelado pelo usuário.")
    srv.shutdown()

    if kind == "thumb" and decisao.get("choice"):
        origem = base / "thumbs" / decisao["choice"]
        shutil.copyfile(origem, base / "thumb_selected.png")
    (base / ("decision_%s.json" % kind)).write_text(
        json.dumps(decisao, ensure_ascii=False), encoding="utf-8")
    return decisao
