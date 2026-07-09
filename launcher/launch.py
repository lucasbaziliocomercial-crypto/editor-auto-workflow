# -*- coding: utf-8 -*-
"""Launcher do Editor Auto.

Executavel leve: NAO embute a esteira. Ele so localiza a pasta do projeto
(relativa ao proprio .exe) e sobe o painel `orchestrator/editor-auto.py`
com o Python do sistema (pyw/pythonw), preservando todas as libs instaladas
(ClickUp, Magnific, ffmpeg, Whisper, etc.).
"""
import os
import sys
import shutil
import subprocess


def _base_dir():
    """Pasta onde o .exe (ou este .py) esta."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _erro(msg):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "Editor Auto", 0x10)
    except Exception:
        print(msg)


def main():
    root = _base_dir()
    orch = os.path.join(root, "orchestrator")
    script = os.path.join(orch, "editor-auto.py")

    # Se rodar de dentro de launcher/ durante dev, sobe um nivel.
    if not os.path.exists(script):
        alt = os.path.join(os.path.dirname(root), "orchestrator", "editor-auto.py")
        if os.path.exists(alt):
            orch = os.path.dirname(alt)
            script = alt

    if not os.path.exists(script):
        _erro("Nao encontrei orchestrator\\editor-auto.py.\n\n"
              "Deixe o Editor Auto.exe na raiz da pasta "
              "'AUTOMACAO EDICAO - WORKFLOW'.")
        return

    # Python janela (sem console). Prefere pyw (-3) e cai pra pythonw.
    pyw = shutil.which("pyw")
    if pyw:
        cmd = [pyw, "-3", script]
    else:
        pythonw = shutil.which("pythonw")
        if not pythonw:
            _erro("Python nao encontrado no PATH.\n\n"
                  "Instale o Python 3 (python.org) e marque "
                  "'Add Python to PATH'.")
            return
        cmd = [pythonw, script]

    flags = 0x08000000  # CREATE_NO_WINDOW
    try:
        subprocess.Popen(cmd, cwd=orch, creationflags=flags)
    except Exception as e:  # noqa: BLE001
        _erro("Falha ao abrir o painel:\n%s" % e)


if __name__ == "__main__":
    main()
