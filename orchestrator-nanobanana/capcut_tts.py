# -*- coding: utf-8 -*-
"""capcut_tts.py — adaptador entre a Etapa 4 (s4_narracao_srt) e o sidecar kuwacom/CapCut-TTS.

Duas formas de uso:

  A) CLI standalone (legado / debug):
       py -3 capcut_tts.py --text roteiro.txt --voice <id> --out narration.mp3
       py -3 capcut_tts.py --speakers                            # lista vozes
     Internamente: sobe o sidecar (`npm run dev`), POSTa em /v2/synthesize, grava o MP3.

  B) Lib (importado por s4_narracao_srt.py):
       from capcut_tts import garantir_sidecar, sintetizar, RateLimitError
       garantir_sidecar()           # idempotente — sobe se não tá no ar
       sintetizar(texto, voz, mp3)  # POST sincrono; raises RateLimitError se cair em SmartToolRateLimit

A voz Joanne (`XMWzAzwYm487GEok2uG2`) é artista (material_artist) e exige o fluxo
`intelligence/create` — esse endpoint tem rate limit *por conta* (`SmartToolRateLimit`).
O orquestrador trata isso com cadeia de fallback (vozes não-artista) — esta lib só
sinaliza o tipo de erro via `RateLimitError`.

Env (todas opcionais, têm default):
    CAPCUT_TTS_URL       base do sidecar           (default http://127.0.0.1:8080)
    CAPCUT_TTS_VOICE     speaker default           (default XMWzAzwYm487GEok2uG2 = Joanne)
    CAPCUT_TTS_DIR       pasta do repo CapCut-TTS  (default ../tts/CapCut-TTS relativo a este arquivo)
    CAPCUT_TTS_AUTOSTART 1 para auto-subir o Node  (default 1)
    CAPCUT_TTS_SPEED     velocidade (10 = normal)  (default 10)
    CAPCUT_TTS_VOLUME    volume    (10 = normal)    (default 10)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

AQUI = Path(__file__).resolve().parent
DEFAULT_SIDECAR_DIR = (AQUI.parent / "tts" / "CapCut-TTS").resolve()
DEFAULT_URL = os.environ.get("CAPCUT_TTS_URL", "http://127.0.0.1:8080").rstrip("/")
JOANNE = "XMWzAzwYm487GEok2uG2"


class RateLimitError(RuntimeError):
    """Levantada quando o sidecar devolve 502 com 'SmartToolRateLimit' (conta CapCut
    atingiu o limite do fluxo de voz artista). O orquestrador usa isso pra cair pra
    voz não-artista na cadeia de fallback (mesma API CapCut, fluxo multi_platform)."""


def _log(msg):
    print(msg, flush=True)


def _resolver_texto(arg_text):
    """--text pode ser um caminho de arquivo (caso da pipeline) ou o texto literal."""
    p = Path(arg_text)
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        pass
    return arg_text


def _esta_no_ar(base, timeout=3):
    try:
        r = requests.get(base + "/v2/speakers", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _subir_sidecar(sidecar_dir, base, espera_s=180, log=_log):
    """Sobe `npm run dev` destacado e espera o /v2/speakers responder 200 (login + warmup)."""
    env_file = sidecar_dir / ".env"
    if not env_file.is_file():
        raise SystemExit("ERRO: %s não existe. Crie o .env com CAPCUT_EMAIL/CAPCUT_PASSWORD." % env_file)
    txt = env_file.read_text(encoding="utf-8", errors="replace")
    if "CAPCUT_EMAIL=\n" in txt + "\n" or "CAPCUT_EMAIL=\r" in txt:
        import re
        m = re.search(r"^CAPCUT_EMAIL=(.*)$", txt, re.M)
        if m and not m.group(1).strip():
            raise SystemExit(
                "ERRO: CAPCUT_EMAIL/CAPCUT_PASSWORD vazios em %s. Preencha as duas linhas e rode de novo." % env_file
            )

    log("▶ Subindo sidecar CapCut-TTS (npm run dev) em %s ..." % sidecar_dir)
    creation = 0
    if os.name == "nt":
        # CREATE_NO_WINDOW esconde a janela preta de console que o npm/node abriria
        # (DETACHED_PROCESS só desanexa do console do pai, mas o npm.cmd/node ainda
        # popava uma janela cmd.exe própria — feia no meio da automação). CREATE_NEW_PROCESS_GROUP
        # mantém o sidecar num grupo próprio, então ele sobrevive ao Ctrl+C do orquestrador
        # e segue no ar como server. Os logs continuam indo p/ sidecar.log — nada se perde.
        creation = (getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    | subprocess.CREATE_NEW_PROCESS_GROUP)
    log_out = open(sidecar_dir / "sidecar.log", "ab")
    subprocess.Popen(
        "npm run dev",
        cwd=str(sidecar_dir),
        shell=True,
        stdout=log_out,
        stderr=log_out,
        creationflags=creation,
    )

    t0 = time.time()
    while time.time() - t0 < espera_s:
        if _esta_no_ar(base):
            log("    ✓ sidecar no ar (%.0fs)." % (time.time() - t0))
            return
        time.sleep(3)
    raise SystemExit(
        "ERRO: sidecar não respondeu em %ds. Veja %s para o log de login/erros."
        % (espera_s, sidecar_dir / "sidecar.log")
    )


def garantir_sidecar(base=None, autostart=None, sidecar_dir=None, log=_log):
    """Idempotente: se o sidecar já tá em pé, retorna. Caso contrário sobe (ou erra)."""
    base = (base or DEFAULT_URL).rstrip("/")
    if autostart is None:
        autostart = os.environ.get("CAPCUT_TTS_AUTOSTART", "1") not in ("0", "false", "False")
    sidecar_dir = Path(sidecar_dir or os.environ.get("CAPCUT_TTS_DIR", str(DEFAULT_SIDECAR_DIR)))
    if _esta_no_ar(base):
        return base
    if not autostart:
        raise SystemExit(
            "ERRO: sidecar não está no ar em %s e CAPCUT_TTS_AUTOSTART=0. "
            "Suba manualmente: cd %s && npm run dev" % (base, sidecar_dir)
        )
    _subir_sidecar(sidecar_dir, base, log=log)
    return base


def _detect_rate_limit(resp):
    """Reconhece o 502 do sidecar quando a causa raiz é SmartToolRateLimit na CapCut.

    O sidecar embrulha o erro upstream em BAD_GATEWAY ("Failed to synthesize audio"),
    mas o log do servidor contém 'SmartToolRateLimit'. Em runtime aqui só vemos a
    resposta HTTP — então tratamos QUALQUER 502 + 'Failed to synthesize audio' como
    rate-limit potencial para vozes de artista. Outras falhas (rede, 4xx, 5xx fora
    do BAD_GATEWAY) caem como erro genérico.
    """
    if resp.status_code != 502:
        return False
    try:
        body = resp.json()
    except ValueError:
        return False
    return body.get("code") == "BAD_GATEWAY" and "synthesize" in (body.get("message") or "").lower()


def sintetizar(texto, voz, saida, base=None, speed=None, volume=None, log=_log):
    """POST /v2/synthesize sincrono. Levanta RateLimitError em 502/SmartToolRateLimit
    e SystemExit em qualquer outra falha (rede, status inesperado, conteúdo errado).
    """
    base = (base or DEFAULT_URL).rstrip("/")
    if speed is None:
        speed = int(os.environ.get("CAPCUT_TTS_SPEED", "10"))
    if volume is None:
        volume = int(os.environ.get("CAPCUT_TTS_VOLUME", "10"))

    payload = {"text": texto, "speaker": voz, "speed": speed, "volume": volume, "method": "buffer"}
    log("▶ TTS CapCut (voz=%s): %d chars -> %s" % (voz, len(texto), saida))
    try:
        r = requests.post(base + "/v2/synthesize", json=payload, timeout=600)
    except requests.RequestException as e:
        raise SystemExit("ERRO TTS (rede): %s" % e)
    ct = r.headers.get("content-type", "")
    if r.status_code == 200 and "audio" in ct:
        Path(saida).parent.mkdir(parents=True, exist_ok=True)
        Path(saida).write_bytes(r.content)
        kb = len(r.content) / 1024.0
        log("    ✓ %s gravado (%.1f KB)." % (saida, kb))
        return
    if _detect_rate_limit(r):
        raise RateLimitError(
            "voz '%s' bloqueada (SmartToolRateLimit) — conta CapCut atingiu o limite "
            "do fluxo de artistas. Tente uma voz não-artista." % voz
        )
    raise SystemExit(
        "ERRO TTS (status %s, content-type %s): %s"
        % (r.status_code, ct, r.text[:500])
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="caminho do arquivo .txt OU texto literal")
    ap.add_argument("--voice", default=os.environ.get("CAPCUT_TTS_VOICE", JOANNE))
    ap.add_argument("--out", help="caminho do MP3 de saída")
    ap.add_argument("--speakers", action="store_true", help="lista vozes e sai (debug)")
    args = ap.parse_args()

    base = garantir_sidecar()

    if args.speakers:
        r = requests.get(base + "/v2/speakers", timeout=30)
        print(r.text)
        return

    if not args.text or not args.out:
        raise SystemExit("uso: --text <arquivo|texto> --out <saida.mp3> [--voice <speaker>]")

    texto = _resolver_texto(args.text)
    if not texto:
        raise SystemExit("ERRO: texto vazio (verifique %s)." % args.text)
    try:
        sintetizar(texto, args.voice, args.out, base)
    except RateLimitError as e:
        raise SystemExit("ERRO TTS (rate limit): %s" % e)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
