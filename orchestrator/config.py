# -*- coding: utf-8 -*-
"""config.py — liga a esteira "de fábrica", ANTES de qualquer etapa rodar.

Resolve a causa-raiz dos travamentos das Etapas 4 (TTS) e 6/7 (Magnific): os seams
sempre estiveram prontos, mas dependiam de variáveis de ambiente que o usuário tinha de
exportar À MÃO toda sessão (ex.: `. .\\longform\\tts\\set-tts-env.ps1`). Bastava esquecer
uma e a etapa "travava" pedindo a env. Aqui essas variáveis ganham DEFAULTS automáticos
+ um arquivo editável (longform.env), e nada é sobrescrito se já estiver no ambiente.

Precedência (maior vence): ambiente real do shell > longform.env > defaults embutidos.

Basta IMPORTAR no topo dos pontos de entrada (efeito colateral popula os.environ):
    import config  # noqa: F401
"""

import os

from common import LONGFORM_DIR, ORCH_DIR, LONGFORM_INSTALL

ENV_FILE = LONGFORM_DIR / "editor-auto.env"
_CAPCUT_ADAPTER = ORCH_DIR / "capcut_tts.py"

# O SIDECAR CapCut-TTS é COMPARTILHADO com a instalação do long-form (não duplicamos o
# repo Node aqui). Aponta o adaptador capcut_tts.py para lá, a menos que já haja override.
os.environ.setdefault("CAPCUT_TTS_DIR", str(LONGFORM_INSTALL / "tts" / "CapCut-TTS"))

# Defaults "funciona de fábrica" — aplicados só se a chave ainda NÃO existir no ambiente.
DEFAULTS = {
    # --- Etapas 6/7 (Magnific) -------------------------------------------------
    # O MCP do Magnific já está conectado neste ambiente (servidor `mcp__magnific`).
    "LONGFORM_MAGNIFIC_MCP": "mcp__magnific",
    # Modelo do CORPO do vídeo (Etapa 7) — também usado nas fichas (passo 1 da E6).
    # Precisa suportar `reference type=character` (lock de personagem via Library) e 16:9.
    # NENHUM modelo é ilimitado via MCP — todos cobram crédito; por isso o default é o
    # mais BARATO com character refs. Whitelist curada em `magnific_seam.MODOS_BODY`
    # (custos por img via images_simulate_cost, 16:9):
    #   - flux-2-klein                (DEFAULT, 10 créd/img, ~5s, 2K)
    #   - imagen-nano-banana-flash    (50 créd/img, ~12s)
    #   - imagen-nano-banana-2-flash  (75 créd/img, ~40s — zerou a conta)
    #   - flux-kontext                (100 créd/img, ~13s)
    # Trocar via `longform.env` — basta um `LONGFORM_MAGNIFIC_MODE=imagen-nano-banana-flash`.
    # ATENÇÃO: o antigo `recraft-v4-1` só aceita reference type=style — quebraria
    # silenciosamente a consistência de personagem. Por isso saiu do default.
    "LONGFORM_MAGNIFIC_MODE": "flux-2-klein",
    # Modelo SÓ DA THUMB (Etapa 6 passo 2). Nano Banana envelhece/escurece os rostos e
    # derruba credibilidade da CAPA — separamos a thumb num modelo top de linha (GPT 2),
    # que é SOTA-rank-1 no catálogo do Magnific, suporta 16:9 e character refs.
    # As FICHAS (passo 1) e a Etapa 7 continuam no LONGFORM_MAGNIFIC_MODE.
    "LONGFORM_MAGNIFIC_THUMB_MODE": "gpt-2",
    # Qualidade da thumb GPT-2 (low|medium|high). 'medium' = ~450 créd nas 3 variações do
    # Gate 2; 'high' = ~1200 (NUNCA use). 'low' = ~45 se precisar apertar ainda mais.
    "LONGFORM_MAGNIFIC_THUMB_QUALITY": "medium",
    # Modelo de REFINO da thumb (Nano Banana 2). REGRA DURA: a capa nasce SEMPRE no GPT 2; é
    # PROIBIDO gerar a thumb do ZERO no Nano Banana 2 (sai escura/sem graça). O Nano Banana 2
    # SÓ edita/refina uma base que o GPT 2 já gerou, quando a cena moderou (fluxo de 2 passos).
    # ATENÇÃO: "ilimitado" só vale no painel web; via MCP o Nano Banana 2 COBRA ~75 créd/img.
    # Deixe vazio ("") para desligar o refino e ficar 100% no GPT 2.
    "LONGFORM_MAGNIFIC_THUMB_REFINE_MODE": "imagen-nano-banana-2-flash",
    # Gate de QA do Claude (Opus) sobre a capa (Etapa 6): o Claude ABRE a thumb e julga contra
    # o padrão do canal; se reprovar, a Etapa 6 regenera com as sugestões (LONGFORM_THUMB_QA_RETRY
    # vezes) e, se ainda reprovar, manda pro Gate 2 humano em vez de auto-confirmar. Gasta uso do
    # Claude (não crédito Magnific). "0" desliga (volta ao auto-confirm silencioso).
    "LONGFORM_THUMB_QA": "1",
    "LONGFORM_THUMB_QA_RETRY": "1",

    # --- Idioma do conteúdo (roteiro + narração + legendas) --------------------
    # 'en' (default — conversão original do canal) | 'pt' (MODO TESTE: vídeo em
    # português pra equipe avaliar a HISTÓRIA). Ligado pela caixa "Vídeo em português"
    # da GUI. As imagens (Etapas 5/7) ficam SEMPRE em inglês (direção visual do Magnific).
    "LONGFORM_IDIOMA": "en",
    # Voz(es) da narração no MODO PORTUGUÊS (só valem quando LONGFORM_IDIOMA=pt). IDs de
    # voz são da SUA conta CapCut — liste com:
    #   py -3 longform/orchestrator/capcut_tts.py --speakers
    # Se ambos ficarem vazios, a Etapa 4 reaproveita a cadeia EN (narra com sotaque) e avisa.
    "LONGFORM_TTS_VOICE_PT": "",            # voz primária pt-BR (ex.: uma feminina BR)
    "LONGFORM_TTS_VOICE_FALLBACK_PT": "",   # CSV de fallbacks pt-BR (mesmo papel da cadeia EN)

    # --- Etapa 4 (TTS) ---------------------------------------------------------
    # Provider padrão = sidecar CapCut (voz Joanne + cadeia de fallback dentro do PRÓPRIO
    # CapCut). Para mudar para Magnific (créditos), use "magnific". O config.py já liga
    # o CapCut por padrão; o .env de longform/tts/CapCut-TTS tem o login da usuária.
    "LONGFORM_TTS_PROVIDER": "capcut",
    "LONGFORM_TTS_VOICE": "XMWzAzwYm487GEok2uG2",  # Joanne (CapCut, artista EN feminina)
    # Cadeia de fallback CSV — usada SE a voz primária bater SmartToolRateLimit (limite
    # por conta no fluxo intelligence/create das vozes de ARTISTA da CapCut). Estas duas
    # NÃO são artistas (vão pelo multi_platform, outro pool de quota) — então a Etapa 4
    # se mantém 100% CapCut sem precisar de Magnific.
    #   cool_lady = ICL_en_female_guanggao (EN feminina calma — ideal romance/audiobook)
    #   labebe    = ICL_en_female_jiaoao   (EN feminina brilhante)
    "LONGFORM_TTS_VOICE_FALLBACK": "cool_lady,labebe",
    # Tamanho-alvo de cada bloco de texto enviado ao TTS CapCut (chars). Blocos menores
    # reduzem o blast-radius de um rate-limit (só 1 bloco precisa cair pra fallback).
    "LONGFORM_TTS_CHUNK_CHARS": "1500",
    # Backoff quando a cadeia INTEIRA de vozes cai em rate-limit num bloco (a conta CapCut
    # atingiu o limite global — pega multi_platform E intelligence/create). Em vez de
    # abortar um run de dezenas de blocos, a Etapa 4 ESPERA e tenta a cadeia de novo.
    # Rate-limit é transitório; só falha de vez após _RETRIES esperas. Espera = min(
    # _WAIT * 2**n, _WAIT_MAX) — default 45,90,180,300,300,300s (~17min de tolerância).
    "LONGFORM_TTS_RATELIMIT_RETRIES": "6",
    "LONGFORM_TTS_RATELIMIT_WAIT": "45",       # base da espera (s), dobra a cada tentativa
    "LONGFORM_TTS_RATELIMIT_WAIT_MAX": "300",  # teto de cada espera (s)
    # Legado: shell-out template (não é mais o caminho principal — synthesize_capcut
    # chama o adapter como lib). Mantido p/ debug / testes manuais via PowerShell.
    "LONGFORM_TTS_CMD": 'py -3 "%s" --text "{texto}" --voice "{voz}" --out "{saida}"' % _CAPCUT_ADAPTER,

    # Fallback provider=magnific: Rhea Sterling (631) — voz EN ideal p/ romance/audiobook.
    "LONGFORM_TTS_MAGNIFIC_VOICE": "631",
    "LONGFORM_TTS_MAGNIFIC_MODEL": "eleven_turbo_v2_5",  # aguenta blocos de até ~10k chars
    "LONGFORM_TTS_CHUNK": "9000",  # tamanho-alvo de cada bloco de texto (chars) p/ o TTS Magnific

    # === roteiro-auto (novo) ================================================
    # FORMATO DO VÍDEO — DEFAULT HORIZONTAL (16:9, YouTube/isca). Todos os stages visuais
    # (prompts, imagens, capas, montagem) leem daqui. Toda a esteira Editor Auto hoje produz
    # isca horizontal; o vertical (9:16) era resquício do antigo Romance Maker (app/TikTok) e
    # virou OPT-IN: um canal que precise de vertical declara em common.CANAL_FORMATO (ou via
    # env ROTEIRO_W/H/ASPECT). Assim nenhum canal — atual ou novo — sai vertical por acidente.
    # Mudado 2026-07-10: card 84 (Rowan) saiu vertical porque caía neste default (então 9:16);
    # a raiz era o default, não só o mapa por canal. Ver decisoes-changelog.
    "ROTEIRO_W": "1920",
    "ROTEIRO_H": "1080",
    "ROTEIRO_ASPECT": "16:9",     # aspectRatio do Magnific (Etapa 5) — casa com W×H
    # Nº de imagens por capítulo (Etapa 4/5). Config do editor = 15; roteirista citou 8.
    # PARAMETRIZÁVEL — o editor confirma (pendência do plano).
    "ROTEIRO_IMAGES_PER_CHAPTER": "8",
    # QA VISUAL das imagens do corpo (Etapa 5): depois de gerar, o Claude ABRE cada img_NNN.png
    # e detecta BUG de geração (identidade trocada entre os personagens, reflexo de espelho
    # incoerente, anatomia quebrada, texto queimado na imagem). As bugadas são APAGADAS e
    # REGERADAS com regra anti-bug reforçada, até ROTEIRO_IMG_QA_MAX_ROUNDS rodadas. Gasta uso do
    # Claude (não crédito Magnific). Vale P1 e P2 e todas as categorias. "0" desliga o QA.
    "ROTEIRO_IMG_QA": "1",
    "ROTEIRO_IMG_QA_MAX_ROUNDS": "2",     # rodadas de regeneração das imagens bugadas
    "ROTEIRO_IMG_QA_MODEL": "sonnet",     # modelo do QA visual (opus = mais rigor, + caro em lote)
    # Capas de capítulo (Etapa 6) — specs do config_alves.json → covers.
    "ROTEIRO_COVER_DURACAO_S": "5",       # duração da capa quando NÃO há narração de título (0.8–8)
    # PISO da capa quando o título é narrado (a duração vira max(piso, fala+respiro), teto 8s).
    # 5s desde 2026-07-10 (pedido: 'intro de capítulo 5s na tela') — título curto não some antes de
    # dar pra ler. Vale P1 e P2.
    "ROTEIRO_COVER_DUR_MIN": "5",
    "ROTEIRO_COVER_FPS": "30",
    "ROTEIRO_COVER_FADE_IN_S": "0.6",
    "ROTEIRO_COVER_ZOOM": "1.1",          # Ken-Burns leve
    "ROTEIRO_COVER_FONT_SIZE": "84",
    "ROTEIRO_COVER_OVERLAY_ALPHA": "0.45",# escurecimento sobre o fundo
    # Fonte da capa (.ttf/.otf) — PENDÊNCIA: o editor precisa entregar. Vazio = fonte do sistema.
    "ROTEIRO_COVER_FONT": "",
    # Áudio da montagem (Etapa 7): música de fundo e SFX de troca de capítulo.
    "ROTEIRO_MUSICA_DB": "-10",           # ganho da música de fundo (dB)
    "ROTEIRO_TEASER_MAX_S": "60",         # teaser drop-in no início (≤60s)
    "ROTEIRO_PAUSA_TROCA_S": "0.5",       # pausa no início e nas trocas de capítulo
    # Render da montagem (Etapa 7) — specs portadas da esteira-modelo (long-form).
    # ENCODER: 'auto' (default) detecta o melhor de HARDWARE que a máquina realmente suporta,
    # via smoke-test cacheado (montagem_vertical._encoder): nvenc (NVIDIA) → qsv (Intel Quick
    # Sync) → amf (AMD) → libx264 (CPU). Assim CADA máquina do time usa a aceleração dela sem
    # config manual (PC RTX 2060 → nvenc; notebook Intel → qsv; notebook AMD → amf). Um valor
    # explícito (ex.: 'h264_nvenc', 'libx264') é respeitado sem detectar.
    "ROTEIRO_ENCODER": "auto",            # auto = nvenc/qsv/amf/libx264 conforme a máquina
    "ROTEIRO_CRF": "18",                  # qualidade libx264 (18 = transparente)
    "ROTEIRO_X264_PRESET": "fast",
    "ROTEIRO_NVENC_PRESET": "p4",         # preset NVENC p1(+rápido)..p7(+qualidade); p4≈p5 visual
    "ROTEIRO_NVENC_CQ": "21",             # qualidade NVENC (menor=melhor; 21 ≈ CRF 18)
    "ROTEIRO_QSV_PRESET": "veryfast",     # preset Intel QSV (veryfast..veryslow)
    "ROTEIRO_QSV_GQ": "23",               # qualidade QSV ICQ (menor=melhor; ~= CRF 18-20)
    "ROTEIRO_AMF_QUALITY": "balanced",    # AMD AMF: speed|balanced|quality
    "ROTEIRO_AMF_QP": "22",               # AMD AMF qp constante (menor=melhor)
    # Super-amostragem do Ken Burns: canvas que o zoompan lê antes do zoom. NÃO é só anti-serrilhado
    # — é o ANTI-TREMIDO nº1: o zoompan arredonda o pan/zoom p/ pixel INTEIRO desse canvas, então o
    # "pulo" residual em px de saída = 1/supersample (1.0→~1px = TREMIDO visível; 2.0→0,5px = liso).
    # A 1.0 (o valor antigo, escolhido só pensando em nitidez) a câmera "segura e pula" = a tremedeira
    # que a editora reclamou. 1.5 + o tmix (ROTEIRO_MOTIONBLUR, ligado por padrão no código) fica TÃO
    # liso quanto 4x/2x a MENOS custo (medição da esteira long-form) — 4px→2.25px = ~40% menos no filtro
    # mais pesado da Etapa 7. NÃO volte p/ 1.0 (reintroduz o tremido). Custo: é o maior peso de CPU do
    # corpo (canvas maior). Default REDUZIDO de 2.0→1.5 em 2026-07-10 (gargalo da montagem, qualidade
    # neutra pelo tmix — ver decisoes-changelog). Ver tb decisoes-changelog 2026-07-09 (port long-form).
    "ROTEIRO_KB_SUPERSAMPLE": "1.5",
    # Amplitude do Ken Burns (env-tunável; defaults espelham a long-form — a editora pediu p/ SENTIR
    # o movimento). ROTEIRO_KENBURNS_ZOOM = 1.0→1.0+z (zoom); ROTEIRO_KENBURNS_PAN = fração da margem
    # livre usada no pan (0.90 = quase até a borda, sem abrir preto). ROTEIRO_MOTIONBLUR: frames de
    # motion-blur (tmix) que fundem o judder — sem valor aqui, o código usa 3 em ≤30fps / 2 acima
    # (0/1 desliga). ROTEIRO_KB_FADE: fade-preto entre imagens do corpo (0=off; 12 = look long-form).
    "ROTEIRO_KENBURNS_ZOOM": "0.28",
    "ROTEIRO_KENBURNS_PAN": "0.90",
    # Quantos CORPOS (Ken Burns) renderizar AO MESMO TEMPO. O zoompan do FFmpeg é single-thread, então
    # em fila cada corpo usa ~2-3 núcleos e sobra CPU — o gargalo da montagem. N em paralelo ocupa os
    # núcleos livres SEM mudar o output (mesma cadeia de filtros por capítulo). Teto 4 (limite de
    # sessões NVENC simultâneas em placa consumer). 1 = sequencial (comportamento antigo).
    # Default SUBIDO de 3→4 em 2026-07-10 (PC RTX/12 núcleos tinha CPU ociosa). ATENÇÃO 8GB: 4 corpos
    # podem swapar em notebook 8GB — os .env dos notebooks fixam 3 explicitamente (ver configs-maquinas).
    "ROTEIRO_CORPO_PARALELO": "4",
    "ROTEIRO_AUDIO_NIVEL": "media",       # limpeza da narração: leve|media|forte|off
    "ROTEIRO_AUDIO_LUFS": "-14",          # loudnorm alvo (padrão da casa)
    "ROTEIRO_AUDIO_TP": "-1.5",           # true peak
    # Legenda queimada (vertical). Re-transcreve o áudio já montado (Whisper) p/ sincronia
    # exata (a montagem insere capas/silêncio que desincronizam a narration.srt original).
    "ROTEIRO_LEGENDA": "1",               # 0 desliga
    # Legenda queimada por cima da CENA DE ABERTURA (cena animada no começo, P1 e P2). 0 (padrão,
    # 2026-07-10) = sem legenda na abertura (cold-open cinematográfico limpo); 1 = legenda de volta.
    "ROTEIRO_ABERTURA_LEGENDA": "0",
    # Legenda queimada por cima das CAPAS de troca de capítulo (o cartão 'Chapter N — Título'). 0
    # (padrão, 2026-07-10, pedido da editora: 'vídeo mais limpo nas trocas') = sem legenda na capa
    # (ela já mostra o título escrito); 1 = legenda re-transcrita de volta por cima da capa.
    "ROTEIRO_CAPA_LEGENDA": "0",
    # Convivência legenda × QR. 0 (padrão, 2026-07-10, pedido da editora): a legenda fica EMBAIXO e
    # CENTRALIZADA e o QR convive por cima no canto — como nos vídeos oficiais. 1 = modo legado que
    # SOBE a legenda pra acima do QR (jogava ela pro meio da tela quando o card do QR era grande).
    "ROTEIRO_QR_LEGENDA_EVITAR": "0",
    # ROTEIRO_CAPTION_FONTSIZE / ROTEIRO_CAPTION_MARGINV: default derivado da altura (~3.3% / ~16%).
    # Sincronia narração x roteiro (anti-dessincronia — o problema do 256).
    "ROTEIRO_TTS_STALE": "regen",         # narration.mp3 mais velho que roteiro.txt: regen|warn|off
    "ROTEIRO_TTS_STALE_TOL": "2",         # folga (s) no mtime p/ evitar falso-positivo
    "ROTEIRO_SYNC_GUARD": "1",            # Etapa 7 falha alto se 0 âncoras casam (0 = desliga)
    "ROTEIRO_AUTO_REBUILD": "1",          # código mudou desde o artefato: Etapas 3/6/7 refazem sozinhas (local/grátis), 2/4/5 só avisam; 0 = idempotência pura
}


def _parse_env_file(path):
    """Lê um arquivo KEY=VALUE simples (ignora linhas em branco e comentários `#`)."""
    dados = {}
    if not path.is_file():
        return dados
    for linha in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = linha.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        chave, _, valor = s.partition("=")
        chave = chave.strip()
        valor = valor.strip().strip('"').strip("'")
        if chave:
            dados[chave] = valor
    return dados


def carregar():
    """Aplica longform.env (se existir) e depois os defaults — sem sobrescrever o ambiente."""
    # 1) o arquivo do usuário tem prioridade sobre os defaults embutidos…
    for chave, valor in _parse_env_file(ENV_FILE).items():
        os.environ.setdefault(chave, valor)
    # 2) …e os defaults embutidos preenchem o que ainda faltar.
    for chave, valor in DEFAULTS.items():
        os.environ.setdefault(chave, valor)
    return os.environ


def salvar_env(chave, valor):
    """Grava/atualiza uma chave no editor-auto.env E aplica na sessão atual (os.environ).

    Usado pelo seletor de modelo da GUI: como o pipeline roda NO MESMO processo e lê a env ao
    vivo (`magnific_seam.modo()` → `os.environ.get`), a sobrescrita imediata já vale para a
    próxima rodada; a persistência no arquivo faz a escolha sobreviver ao fechar o painel.
    Faz UPSERT preservando comentários e demais linhas; sobrescreve (não `setdefault`)."""
    chave, valor = str(chave).strip(), str(valor).strip()
    if not chave:
        return
    os.environ[chave] = valor  # vence os defaults/arquivo na sessão atual
    linhas = []
    if ENV_FILE.is_file():
        linhas = ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    nova = "%s=%s" % (chave, valor)
    achou = False
    for i, linha in enumerate(linhas):
        s = linha.strip()
        if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == chave:
            linhas[i] = nova
            achou = True
            break
    if not achou:
        linhas.append(nova)
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def resumo():
    """Devolve as chaves relevantes já resolvidas (para o diagnóstico / logs)."""
    chaves = ("LONGFORM_IDIOMA", "LONGFORM_TTS_VOICE_PT",
              "LONGFORM_TTS_PROVIDER", "LONGFORM_TTS_CMD", "LONGFORM_TTS_VOICE",
              "LONGFORM_TTS_MAGNIFIC_VOICE", "LONGFORM_TTS_MAGNIFIC_MODEL",
              "LONGFORM_MAGNIFIC_MCP", "LONGFORM_MAGNIFIC_MODE",
              "LONGFORM_MAGNIFIC_THUMB_MODE", "LONGFORM_MAGNIFIC_THUMB_QUALITY",
              "LONGFORM_CLICKUP_LIST", "TINAGO_DIR")
    return {k: os.environ.get(k, "") for k in chaves}


# Efeito colateral no import: a esteira fica "ligada" só por importar config.
carregar()
