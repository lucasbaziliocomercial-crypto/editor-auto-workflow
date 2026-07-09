# -*- coding: utf-8 -*-
"""testar-conexoes.py — diagnóstico ("doutor") da esteira long-form.

Verifica, em um comando, se tudo que as etapas precisam está LIGADO: config carregada,
executáveis (claude/ffmpeg), Whisper, o MCP do Magnific (Etapas 6/7) e o TTS (Etapa 4).
Use isto pra confirmar o ambiente ANTES de rodar a pipeline inteira.

Uso (da raiz do repo):
    py -3 longform/orchestrator/testar-conexoes.py            # checagens rápidas (sem mídia paga)
    py -3 longform/orchestrator/testar-conexoes.py --tts      # + sintetiza uma frase (prova a Etapa 4)
    py -3 longform/orchestrator/testar-conexoes.py --imagem   # + gera 1 imagem de teste (prova 6/7)
    py -3 longform/orchestrator/testar-conexoes.py --tudo     # tudo acima
"""

import argparse
import sys

import config  # noqa: F401  (liga TTS/Magnific via os.environ)
from common import (ErroPipeline, PROJECTS_DIR, Projeto, achar_claude, achar_ffmpeg,
                    WHISPER_SCRIPT)

OK = "✓"   # ✓
X = "✗"    # ✗


def _print(msg=""):
    print(msg, flush=True)


def _log(msg):
    _print("      " + str(msg))


def _checar_config():
    _print("• Configuração (config.py + longform.env)")
    r = config.resumo()
    for k, v in r.items():
        _print("    %s = %s" % (k, v or "(vazio)"))
    return True


def _checar_exec():
    ok = True
    try:
        _print("  %s claude: %s" % (OK, " ".join(achar_claude())))
    except ErroPipeline as e:
        _print("  %s claude: %s" % (X, e)); ok = False
    try:
        _print("  %s ffmpeg: %s" % (OK, achar_ffmpeg()))
    except ErroPipeline as e:
        _print("  %s ffmpeg: %s" % (X, e)); ok = False
    if WHISPER_SCRIPT.is_file():
        _print("  %s Whisper (SRT): %s" % (OK, WHISPER_SCRIPT))
    else:
        _print("  %s Whisper (SRT) não encontrado: %s (ajuste TINAGO_DIR)" % (X, WHISPER_SCRIPT))
        ok = False
    return ok


def _projeto_diag():
    return Projeto(PROJECTS_DIR / "_diagnostico")


def _checar_magnific(gerar_imagem=False):
    """Confere que um claude -p consegue alcançar o MCP do Magnific (caminho real das Etapas 6/7)."""
    from stages import magnific_seam
    proj = _projeto_diag()
    if not gerar_imagem:
        # checagem barata: lista modelos (não gera mídia, não gasta crédito)
        instr = ("Confira a conexão do Magnific: chame mcp__magnific__images_models_list "
                 "(onlyRecommended=true) e responda apenas 'MAGNIFIC_OK' no fim.")
        _print("  ... consultando modelos via MCP (sem gerar mídia)")
        magnific_seam.gerar(proj, _log, None, instr, modelo="sonnet")
        _print("  %s Magnific alcançável pelo claude -p (modo %s)." % (OK, magnific_seam.modo()))
        return True
    # checagem completa: gera 1 imagem real e baixa o PNG (prova generate->wait->download)
    alvo = proj.thumbs_dir / "diag_test.png"
    alvo.unlink(missing_ok=True)
    instr = ("Gere UMA imagem de teste 16:9 (um pôr do sol sobre o mar, cinematográfico). "
             + magnific_seam.receita(1, "thumbs/diag_test.png"))
    _print("  ... gerando 1 imagem de teste (gasta crédito Magnific)")
    magnific_seam.gerar(proj, _log, None, instr, modelo="sonnet")
    if proj.existe(alvo):
        _print("  %s Imagem de teste gerada: %s" % (OK, alvo))
        return True
    _print("  %s Magnific NÃO gerou a imagem de teste." % X)
    return False


def _checar_tts():
    """Sintetiza uma frase curta pelo provider configurado (prova a Etapa 4 ponta a ponta)."""
    from stages import s4_narracao_srt
    import os
    proj = _projeto_diag()
    proj.roteiro.write_text(
        "This is a short narration test for the long-form pipeline. "
        "If you can hear this sentence, the text-to-speech stage is working.",
        encoding="utf-8",
    )
    proj.narration_mp3.unlink(missing_ok=True)
    prov = os.environ.get("LONGFORM_TTS_PROVIDER", "?")
    _print("  ... sintetizando frase de teste (provider=%s)" % prov)
    s4_narracao_srt.synthesize(proj, _log, None)
    if proj.existe(proj.narration_mp3):
        kb = proj.narration_mp3.stat().st_size / 1024.0
        _print("  %s TTS OK: %s (%.1f KB)" % (OK, proj.narration_mp3, kb))
        return True
    _print("  %s TTS não gerou narration.mp3." % X)
    return False


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--tts", action="store_true", help="sintetiza uma frase (prova a Etapa 4)")
    ap.add_argument("--imagem", action="store_true", help="gera 1 imagem de teste (prova 6/7)")
    ap.add_argument("--tudo", action="store_true", help="roda tudo (--tts + --imagem)")
    args = ap.parse_args()
    quer_tts = args.tts or args.tudo
    quer_img = args.imagem or args.tudo

    _print("=== Diagnóstico da esteira long-form ===\n")
    resultados = {}

    _checar_config(); _print()
    _print("• Executáveis / scripts")
    resultados["execs"] = _checar_exec(); _print()

    _print("• Magnific (Etapas 6/7)")
    try:
        resultados["magnific"] = _checar_magnific(gerar_imagem=quer_img)
    except Exception as e:  # noqa: BLE001
        _print("  %s Magnific falhou: %s" % (X, e)); resultados["magnific"] = False
    _print()

    if quer_tts:
        _print("• TTS (Etapa 4)")
        try:
            resultados["tts"] = _checar_tts()
        except Exception as e:  # noqa: BLE001
            _print("  %s TTS falhou: %s" % (X, e)); resultados["tts"] = False
        _print()
    else:
        _print("• TTS (Etapa 4): pulado (use --tts para sintetizar uma frase de teste)\n")

    _print("=== Resumo ===")
    for nome, ok in resultados.items():
        _print("  %s %s" % (OK if ok else X, nome))
    falhou = [n for n, ok in resultados.items() if not ok]
    if falhou:
        _print("\n%s Pendências: %s" % (X, ", ".join(falhou)))
        sys.exit(1)
    _print("\n%s Tudo conectado. Pode rodar a pipeline." % OK)


if __name__ == "__main__":
    main()
