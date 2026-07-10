# -*- coding: utf-8 -*-
"""SEAM do Magnific (geração de imagem via MCP) — VERIFICADO 2026-06-16.

Contrato real do MCP (servidor `mcp__magnific`):
  - mcp__magnific__images_generate(prompt, mode, aspectRatio, count)
      -> { creations: [{ identifier, status, expectTime, tool, webUrl, mode }], instruction }
      É ASSÍNCRONO: cada creation começa "queued/processing".
  - mcp__magnific__creations_wait(...) -> faz polling até "completed".
  - O resultado final vem como URL (`webUrl`) — baixar com `Bash curl` e salvar o PNG.
  - mcp__magnific__images_models_list / images_simulate_cost -> modelos / custo.

Modelo padrão: flux-2-klein (Flux.2 Klein). ECONÔMICO — 10 créditos/img, o mais barato do
catálogo que ainda suporta reference type=character (lock de personagem via Library, exigido
pelas Etapas 6/7) e 16:9 nativo. Trocado de imagen-nano-banana-2-flash (75 créditos/img, o
que drenou os 45k créditos da conta) para cá em 2026-06-18 a pedido da usuária. O antigo
recraft-v4-1 só aceita reference type=style e quebraria a consistência. Sobrescrevível por env
LONGFORM_MAGNIFIC_MODE. NENHUM modelo é ilimitado via MCP — todos cobram crédito (confirmado
na doc oficial; o "∞" do Premium+ vale só no painel web).
Ative o seam com env LONGFORM_MAGNIFIC_MCP=mcp__magnific (config.py já faz isso de fábrica).
"""

import os

from common import ErroPipeline
from runner import rodar_claude

# Modelo do CORPO/FICHAS — flux-2-klein é o character-capable mais barato (10 créditos/img).
# Os Nano Banana (75/img) zeraram a conta; nenhum modelo é ilimitado via MCP.
DEFAULT_MODE = "flux-2-klein"
# Modelo SÓ da thumb — credibilidade da capa importa mais que character lock perfeito
# (Nano Banana envelhece/escurece). GPT 2 é SOTA-rank-1 no Magnific e suporta 16:9 +
# character refs, então o lock da Library segue funcionando. Sobrescrevível por env
# LONGFORM_MAGNIFIC_THUMB_MODE (config.py já injeta de fábrica).
DEFAULT_THUMB_MODE = "gpt-2"
# Qualidade da thumb (GPT-2 aceita low|medium|high). Default 'medium': 3 variações custam
# ~450 créditos vs. 1200 em 'high' (~3×). Sobrescrevível por LONGFORM_MAGNIFIC_THUMB_QUALITY.
DEFAULT_THUMB_QUALITY = "medium"
# Modelo de REFINO da thumb (o "Nano Banana 2" do prompt mestre — não tem o filtro da OpenAI).
# REGRA DURA (feedback da usuária, 2026-06-18): é PROIBIDO gerar a thumb do ZERO no Nano Banana
# 2. A capa nasce SEMPRE no GPT 2 (look glam/claro do canal). O Nano Banana 2 só entra para
# EDITAR/REFINAR uma base que o GPT 2 já gerou (fluxo de 2 passos da PARTE C) — nunca uma
# geração cold/fresh (foi o que deixou a capa escura e sem graça). ATENÇÃO de custo: o
# "ilimitado" do Nano Banana 2 vale só no painel web; via MCP ele COBRA ~75 créditos/img.
# Sobrescrevível por LONGFORM_MAGNIFIC_THUMB_REFINE_MODE.
DEFAULT_THUMB_REFINE_MODE = "imagen-nano-banana-2-flash"

# Aspect ratio TRAVADO para body/thumb (Etapa 6 passo 2 e Etapa 7). YouTube long-form é
# sempre 16:9 — as fichas (passo 1 da E6) usam 2:3 propositalmente (corpo inteiro vertical).
ASPECT_BODY = "16:9"
ASPECT_FICHA = "2:3"

# ── Seletor de modelos para o CORPO do vídeo (Etapa 7) ────────────────────────
# Whitelist curada de modelos rápidos do catálogo Magnific que (a) suportam
# `references[].type=character` (sem isso o lock da Library não funciona) e (b) renderizam
# 16:9 nativo. Identifiers conferidos no catálogo `mcp__magnific__images_models_list`
# (rode o `testar-conexoes.py --imagem --listar` se algum identifier sair de moda).
#
# Default = `flux-2-klein`: 10 créditos/img (o + barato com character refs), ~5s, 2K — ideal
# pro batch da Etapa 7. Custo é o critério nº 1 (nenhum modelo é ilimitado via MCP). Acima do
# econômico ficam as opções de qualidade, liberadas por run pelo seletor da GUI ou por env.
# TODOS os slugs abaixo foram VALIDADOS no catálogo + `simulate_cost` (9:16, 1 img) em
# 2026-07-08 e suportam `reference type=character`:
#   flux-2-klein=10 · seedream-4=50 · flux-2(Pro)=50 · nano-banana-2-flash=75 · flux-kontext=100.
# (O antigo alias `imagen-nano-banana-flash` foi APOSENTADO pelo Magnific — o MCP rejeita como
# "not a valid model" — por isso saiu daqui. `recraft-v4-1` só aceita type=style e quebraria o
# lock de personagem.)
MODOS_BODY = {
    "flux-2-klein":  ("flux-2-klein",               "~5s",  "10 créd/img — ECONÔMICO (padrão): + barato com character refs, 2K"),
    "seedream-4":    ("seedream-4",                 "~14s", "50 créd/img — QUALIDADE (recomendado): character consistency forte, fotorreal"),
    "flux-2":        ("flux-2",                     "~21s", "50 créd/img — Flux.2 Pro: alta fidelidade, mesma família do Klein"),
    "nano-banana-2": ("imagen-nano-banana-2-flash", "~40s", "75 créd/img — Google Nano Banana 2 (topo; caro — já zerou a conta)"),
    "flux-kontext":  ("flux-kontext",               "~14s", "100 créd/img — Kontext Pro: foco em edição de refs (mais caro)"),
}

# Ferramentas do Magnific que o pipeline usa (enumeradas — mais robusto que wildcard
# no --allowedTools). Bash entra para baixar a URL final do creation.
_MAGNIFIC_TOOLS = (
    "images_models_list", "images_simulate_cost",
    "images_generate", "images_variations", "creations_wait",
    "images_relight",   # clarear/realçar thumbs que saírem escuras
    "library_create",   # registra ficha como PERSONAGEM na Library (lock real)
    "library_list",     # consulta personagens já registrados (idempotência)
    "audio_tts",        # Etapa 4 (provider=magnific): narração TTS
    "audio_voices_list",  # consulta o catálogo de vozes do TTS
    # Upload de arquivo local como creation (necessário p/ Etapa 7 anexar
    # thumb_selected.png como reference type=image em images_generate):
    "creations_request_upload",  # pede a URL pré-assinada de upload
    "creations_upload_file",     # sobe o binário
    "creations_upload_image",    # variante p/ imagens (alguns clientes usam essa)
    "creations_finalize_upload", # finaliza o upload e devolve o identifier
    "creations_get",             # consulta um creation pelo identifier (se preciso)
    "creations_show",            # idem (UI-capable, mas inofensivo no allowlist)
)

# Passo de realce — roda SÓ na capa ESCOLHIDA (thumb_selected.png), depois do Gate 2, e não
# nas 3 variações da Etapa 6: o relight do Magnific cobra crédito por chamada e 2 das 3 capas
# são descartadas no gate, então reluminá-las era desperdício. images_relight é assíncrono
# (creations_wait) e exige um creationIdentifier — por isso o arquivo local sobe primeiro.
def instr_relight_arquivo(arquivo):
    """Instrução para CLAREAR uma única imagem local (a capa confirmada no Gate 2).

    Como `arquivo` é um PNG local (não um creation), ele precisa ser SUBIDO antes —
    images_relight só aceita `creationIdentifier`. Sobrescreve o mesmo arquivo no fim."""
    return (
        "REALCE/CLAREAR A CAPA ESCOLHIDA (a usuária pediu capas MAIS CLARAS): ABRA `%s` com Read "
        "e avalie a luminosidade. A meta é uma capa LUMINOSA, bem exposta, com os rostos bem "
        "iluminados. Se ela JÁ estiver claramente bem iluminada e luminosa, NÃO mexa (não gaste "
        "crédito à toa) e só avise que manteve. Se estiver escura, abafada, sem contraste OU "
        "apenas mediana/poderia ficar mais clara:\n"
        "1) SUBA o arquivo (images_relight só aceita creationIdentifier): "
        "mcp__magnific__creations_request_upload {filename:\"%s\", contentType:\"image/png\"} → "
        "guarde uploadUrl e identifier; `Bash curl -X PUT -H \"Content-Type: image/png\" "
        "--data-binary @%s \"<uploadUrl>\"`; mcp__magnific__creations_finalize_upload "
        "{identifier:<o do passo anterior>}.\n"
        "2) Chame mcp__magnific__images_relight {creationIdentifier:<esse identifier>, "
        "lights:[{azimuth:0, elevation:45, type:\"neutral\", intensity:6}], resolution:\"2k\"} "
        "para CLAREAR (levantar a luz nos rostos) — uma luz frontal/de cima, suave. Espere com "
        "mcp__magnific__creations_wait.\n"
        "3) Baixe o webUrl com `Bash curl -L -o \"%s\" \"<webUrl>\"`, SOBRESCREVENDO o mesmo "
        "arquivo. Não estoure/desbote a imagem. No fim, imprima se reluminou ou manteve."
        % (arquivo, arquivo, arquivo, arquivo)
    )

# Direção de arte da CAPA — lições do fluxo de thumbnails "my stories" (doc 2026-06),
# adaptadas: aqui o gerador padrão é o Nano Banana, que tende a ENVELHECER e ESCURECER as
# pessoas. Estas regras são PREVENÇÃO no prompt (a RELIGHT corrige só o que escapar depois).
# Bullet "lead masculino" é trocado por categoria via thumb_direcao(); o resto é universal.
_THUMB_DIRECAO_UNIVERSAL = (
    "DIREÇÃO DE ARTE DA CAPA (regras fixas da thumb):\n"
    "• IMAGEM LIMPA, SEM TEXTO: nenhuma letra, título, legenda, marca-d'água, logo ou número "
    "renderizado na imagem. O título serve só para entender a cena — NUNCA vai escrito nela. "
    "Composição limpa, personagens em destaque.\n"
    "• EMOÇÃO INTENSA E PROFUNDA (prioridade nº 1): a capa tem que SENTIR. Leia a emoção da "
    "cena no prompt e AMPLIFIQUE-a no rosto dos personagens — não uma expressão morna. Se a "
    "personagem está chorando, ENFATIZE o choro: 'tears streaming down her face, eyes "
    "glistening and red-rimmed, raw heartbreak, trembling lips' — a dor tem que ser visível e "
    "comover. Para outras emoções use o equivalente máximo: anseio/longing intenso, fúria "
    "contida, paixão arrebatadora, desespero, esperança. Olhos expressivos, micro-expressão "
    "legível mesmo na miniatura. Emoção crua > pose bonita neutra.\n"
    "{bullet_lead_masculino}"
    "• BELEZA DIVINA / DEITY-LEVEL (ambos os leads — NÃO NEGOCIÁVEL): os dois personagens têm "
    "de parecer impossivamente bonitos, como deuses — o espectador deve sentir 'essas pessoas "
    "não são reais de tão perfeitas'. Descritores OBRIGATÓRIOS em todo prompt:\n"
    "  — Lead MASCULINO: perfectly symmetrical divine face, strong chiseled jaw, high "
    "cheekbones, piercing mesmerizing eyes, flawless luminous skin, impossibly handsome, "
    "deity-level masculine beauty.\n"
    "  — Lead FEMININA: perfectly symmetrical ethereal face, delicate sculpted features, high "
    "cheekbones, captivating mesmerizing eyes, flawless luminous porcelain skin, goddess-level "
    "beauty, impossibly beautiful.\n"
    "  — Geral: otherworldly stunning appearance, god-like, airbrushed perfection — o gerador "
    "tende a envelhecer/escurecer, CONTRABALANCE pedindo 'early 30s, radiant flawless skin'.\n"
    "• MESMO FORMATO DAS NOSSAS CAPAS (referência fixa do canal): lead em PRIMEIRO PLANO grande "
    "ocupando a maior parte do quadro (close/meio-corpo, não plano aberto) + a cena ao fundo — "
    "essa leitura (lead na frente, cena atrás) é o que lê bem na miniatura. Emoção no rosto em "
    "primeiro lugar; objeto-gancho quando a cena pedir (ex.: o anel, a carta) reforça o clique. "
    "{bullet_cenario}"
    "• BALANÇO DE COR pela vibe da cena: se for íntima/sensual/romance, tom dourado QUENTE é "
    "bem-vindo (mantém o clima). Para TODO O RESTO, use 'bright, clean, balanced neutral white "
    "balance, natural true-to-life color, crisp, cinematic' — brilhante e nítido, SEM amarelar "
    "demais. Não deixe TODA capa amarela.\n"
    "• CAPA BEM CLARA (a usuária pediu MAIS claras ainda): erre forte para o lado CLARO. A "
    "imagem final tem que sair LUMINOSA, bem exposta, alto brilho, rostos bem iluminados, cena "
    "clara mesmo quando for noite — nunca escura/abafada. Peça 'very bright, well-lit, "
    "luminous, high-key lighting, bright key light on the faces, airy, clear visibility, "
    "elevated exposure'.\n"
    "• Direção geral: glamouroso, intenso, chamativo; contraste forte; rostos legíveis mesmo "
    "em miniatura."
)

_BULLET_LEAD_SELENA = (
    "• ALPHA KING (lead masculino): SEMPRE cabelo LONGO (long flowing hair, até os ombros ou "
    "mais). Físico de alfa MÁXIMO — EXTREMAMENTE musculoso e poderoso: peito enorme e "
    "definido, abdômen marcado, braços e ombros muito largos e volumosos, pescoço forte, "
    "veias/músculos visíveis, corpo de guerreiro/fisiculturista. Mandíbula marcada, presença "
    "imponente e viril, dominante no porte (sem virar gatilho de moderação). Quanto mais "
    "musculoso e forte, melhor — é o código do gênero.\n"
)

_BULLET_LEAD_MAFIA = (
    "• CHEFÃO DA MÁFIA (lead masculino): cabelo CURTO A MÉDIO, escuro, penteado para trás "
    "(slicked-back) ou levemente ondulado/bagunçado — NÃO longo, NÃO medieval. Barba curta "
    "(short stubble). TATUAGENS VISÍVEIS no pescoço, mãos e antebraços (e peito quando a "
    "camisa está aberta). Físico de alfa MÁXIMO IDÊNTICO ao Alpha King — extremamente musculoso "
    "e poderoso: peito enorme e definido, ombros muito largos, abdômen marcado, corpo de "
    "guerreiro. Terno sob medida impecável OU camisa preta aberta no peito. Relógio de ouro "
    "(Rolex). Presença dominante, viril, imponente.\n"
)

_BULLET_CENARIO_SELENA = (
    "Cenário fantasia-medieval/werewolf cinematográfico ao fundo (floresta com tochas, castelo, "
    "salão real), lobos quando combinarem com a cena, profundidade e iluminação dramática que "
    "destaca os rostos. Belíssimo, intenso, premium.\n"
)

_BULLET_CENARIO_MAFIA = (
    "Cenário de máfia contemporânea opulento (penthouse de mármore, gala de black-tie com "
    "lustres de cristal, escritório de arranha-céu à noite, escadaria de mansão, bar de mármore "
    "escuro, interior de limusine), profundidade de campo de cinema, superfícies brilhantes. "
    "Guarda-costas de terno ao fundo (desfocados) quando a cena pedir tensão. Premium.\n"
)


def thumb_direcao(categoria=None):
    """Retorna a direção de arte da capa adaptada para a categoria.

    Troca os bullets de 'lead masculino' e 'cenário' conforme a categoria — o resto é universal.
    `categoria`: chave canônica ('mafia', 'selena', etc.) ou None (usa o default selena)."""
    if categoria == "mafia":
        lead = _BULLET_LEAD_MAFIA
        cenario = _BULLET_CENARIO_MAFIA
    else:
        lead = _BULLET_LEAD_SELENA
        cenario = _BULLET_CENARIO_SELENA
    return _THUMB_DIRECAO_UNIVERSAL.format(
        bullet_lead_masculino=lead,
        bullet_cenario=cenario,
    )


# Compatibilidade retroativa: THUMB_DIRECAO permanece como constante (categoria default/selena)
# para código que ainda a usa diretamente. Prefira thumb_direcao(categoria) nos novos usos.
THUMB_DIRECAO = thumb_direcao()


def modo_fallback():
    """Modo mais permissivo p/ re-tentar geração bloqueada por moderação (opcional, via env)."""
    return os.environ.get("LONGFORM_MAGNIFIC_MODE_FALLBACK", "").strip()


def instr_moderacao(refino_modelo=None):
    """Resiliência à moderação — PARTE C do prompt mestre + REGRA DURA da usuária (2026-06-18).

    REGRA INEGOCIÁVEL: a capa nasce SEMPRE no GPT 2 (look glam/claro do canal). É PROIBIDO gerar
    a thumb do ZERO no Nano Banana 2 — gerar cold no Nano Banana deixa a capa escura/sem graça
    (foi o erro da capa 03). O Nano Banana 2 SÓ entra para EDITAR/REFINAR uma BASE que o GPT 2 já
    produziu (fluxo de 2 passos da PARTE C).

    Ordem ao tomar moderação no GPT 2 ('Moderated'/'blocked by provider'/'NSFW'):
      (1) reframe (confronto/'parecer amigos, sem decote') tirando gatilhos + re-roll no GPT 2;
      (2) se ainda travar, ALIVIE o prompt (versão mais leve/segura da cena) e gere a BASE — ainda
          no GPT 2 — clara e glam, que PASSE;
      (3) só então REFINE essa base no Nano Banana 2 como EDIÇÃO (passando a base do GPT 2 como
          reference type=image), trazendo de volta o que a cena pedia em linguagem simples. NUNCA
          uma geração cold/fresh no Nano Banana 2.

    `refino_modelo`: identifier do modelo de refino (ex.: 'imagen-nano-banana-2-flash'). None →
    cai p/ LONGFORM_MAGNIFIC_MODE_FALLBACK (genérico). Vazio → sem passo 3 (só GPT 2)."""
    rf = refino_modelo if refino_modelo is not None else modo_fallback()
    passo3 = (
        "(3) Só então REFINE no Nano Banana 2 — NUNCA gere do zero nele. Suba a BASE do GPT 2 "
        "(creations_request_upload → curl PUT → creations_finalize_upload) e chame "
        "mcp__magnific__images_generate {mode:\"%s\", references:[{type:\"image\", "
        "identifier:<a base do GPT 2>}], aspectRatio:\"16:9\", prompt:<em linguagem simples, "
        "TRAGA de volta o que a cena pedia — ex.: 'make him lean toward the side of her neck, "
        "eyes closed' — MANTENDO os mesmos personagens/rostos/roupas/cenário e a luz CLARA e "
        "glam, 16:9 widescreen}. O Nano Banana 2 não tem o filtro da OpenAI, então a edição "
        "passa. ATENÇÃO de custo: 'ilimitado' só vale no painel web; via MCP ele COBRA "
        "(~75 créditos/img). " % rf
    ) if rf else ""
    return (
        "RESILIÊNCIA À MODERAÇÃO (filtro do GPT 2/OpenAI — o mais rígido). REGRA DURA: a capa "
        "nasce SEMPRE no GPT 2; é PROIBIDO gerar a thumb do ZERO no Nano Banana 2 (gerar cold "
        "nele sai escuro/sem graça). Se a geração for bloqueada ('Moderated', 'blocked by "
        "provider', 'NSFW'), NÃO desista nem pule pro Nano Banana:\n"
        "(1) REENQUADRE a MESMA cena como CONFRONTO / queda de braço — fórmula da Heloyse que "
        "PASSA no GPT 2: enquadre o par como 'two people simply talking, like companions', SEM "
        "decote (gola alta/modesto), sérios/sem sorrir, iluminação mais clara, conversacional; a "
        "heroína ergue o queixo, desafiadora (ou cautelosa). TIRE os gatilhos (corners, traps, "
        "dominant, submissive, sensual, possessive, jealousy, lips parted, breath caught, "
        "charged) e MANTENHA 'intense eye contact, charged tension, glamorous'. Tente DE NOVO "
        "no GPT 2 (a moderação é parcialmente aleatória; crédito de GPT 2 bloqueado costuma ser "
        "reembolsado).\n"
        "(2) Se AINDA travar, ALIVIE o prompt — gere uma BASE mais leve/segura da cena (casal "
        "junto, tenso/tenro, sem o gatilho explícito), ainda NO GPT 2, clara e glam, que passe. "
        "%s"
        "Só falhe a etapa se nem a base leve do GPT 2 passar." % passo3
    )


def _prefixo():
    pref = os.environ.get("LONGFORM_MAGNIFIC_MCP", "").strip()
    if not pref:
        raise ErroPipeline(
            "Magnific não conectado. Conecte o MCP do Magnific e exporte "
            "LONGFORM_MAGNIFIC_MCP (ex.: 'mcp__magnific'). Depois rode a etapa de novo."
        )
    return pref


def modo():
    return os.environ.get("LONGFORM_MAGNIFIC_MODE", DEFAULT_MODE)


def thumb_modo():
    """Modelo dedicado da thumb (Etapa 6 passo 2). Default GPT 2 (SOTA)."""
    return os.environ.get("LONGFORM_MAGNIFIC_THUMB_MODE", DEFAULT_THUMB_MODE)


def thumb_qualidade():
    """Qualidade da thumb GPT-2 (low|medium|high). Default 'medium' (high custa ~3× = drena
    crédito). Sobrescrevível por env LONGFORM_MAGNIFIC_THUMB_QUALITY."""
    return os.environ.get("LONGFORM_MAGNIFIC_THUMB_QUALITY", DEFAULT_THUMB_QUALITY).strip()


def thumb_modo_refino():
    """Modelo de REFINO da thumb (Nano Banana 2, sem o filtro OpenAI). Só EDITA uma base que o
    GPT 2 já gerou — NUNCA gera a capa do zero (é proibido). Default DEFAULT_THUMB_REFINE_MODE;
    sobrescrevível por LONGFORM_MAGNIFIC_THUMB_REFINE_MODE. String vazia desliga o refino (a
    Etapa 6 fica só no reframe+re-roll+aliviar-prompt do GPT 2)."""
    return os.environ.get("LONGFORM_MAGNIFIC_THUMB_REFINE_MODE", DEFAULT_THUMB_REFINE_MODE).strip()


# ── TRAVA DE SEGURANÇA DE CRÉDITO (2026-06-18) ────────────────────────────────
# Confirmado na doc oficial: NENHUM modelo é ilimitado via MCP — todos cobram crédito (o
# "∞" do Premium+ vale só no painel web). O default do corpo agora é o mais BARATO
# (flux-2-klein = 10 créditos/img), mas como o corpo/fichas geram em lote e a conta já foi
# zerada uma vez (nano-banana-2-flash = 75/img), a geração do CORPO (Etapa 7) e das FICHAS
# (passo 1 da Etapa 6) segue BLOQUEADA por padrão — opt-in explícito por run. A CAPA (thumb,
# GPT-2) NÃO passa por aqui e segue liberada.
# Para liberar o corpo: confirme que tem crédito na conta e exporte
# LONGFORM_MAGNIFIC_CORPO_OK=1 (em longform/longform.env ou no shell).
def garantir_corpo_liberado():
    """Levanta ErroPipeline se a geração de corpo/fichas não estiver liberada.

    Liberação explícita: env `LONGFORM_MAGNIFIC_CORPO_OK=1`. Sem isso, a etapa para com
    uma mensagem acionável em vez de queimar crédito em lote (mesmo no modelo barato)."""
    if os.environ.get("LONGFORM_MAGNIFIC_CORPO_OK", "0").strip() != "1":
        raise ErroPipeline(
            "TRAVA DE CRÉDITO: geração do CORPO/FICHAS bloqueada. O modelo atual '%s' COBRA "
            "crédito por imagem (nenhum modelo é ilimitado via MCP — só no painel web). O "
            "default já é o + barato com character refs (flux-2-klein = 10 créd/img). "
            "Confirme que a conta tem crédito e exporte LONGFORM_MAGNIFIC_CORPO_OK=1 em "
            "longform/longform.env para liberar. A capa (thumb/GPT-2) continua funcionando "
            "normalmente." % modo()
        )


def listar_modos_body():
    """Devolve lista formatada [(alias, identifier, latência, nota)] dos modelos
    curados para o CORPO do vídeo. Usado por logs/diagnóstico e pelo painel."""
    return [(alias, ident, lat, nota) for alias, (ident, lat, nota) in MODOS_BODY.items()]


def modo_body_atual():
    """Resolve o `LONGFORM_MAGNIFIC_MODE` atual para um descritor amigável (alias + nota).
    Se o env var não bate com nenhum alias da whitelist, devolve o identifier cru
    + nota '(custom)' — não bloqueia, só sinaliza. O `images_models_list` valida de fato."""
    atual = modo()
    for alias, (ident, lat, nota) in MODOS_BODY.items():
        if ident == atual:
            return "%s (%s, %s)" % (alias, lat, nota)
    return "%s (custom — fora da whitelist MODOS_BODY)" % atual


def allowed_tools():
    """--allowedTools liberando Read/Write/Bash + as ferramentas do Magnific."""
    pref = _prefixo()
    magnific = " ".join("%s__%s" % (pref, t) for t in _MAGNIFIC_TOOLS)
    return "Read Write Bash " + magnific


# Ferramentas de VÍDEO do Magnific — usadas SÓ pela ABERTURA da P2 (image->video do thumbnail,
# 2026-07-10). Separadas do allowlist de imagem porque nenhuma outra etapa gera vídeo. video_generate
# é assíncrono (creations_wait); o thumbnail sobe como creation (request/finalize_upload) e entra como
# keyframe inicial.
_MAGNIFIC_VIDEO_TOOLS = (
    "video_models_list", "video_generate", "creations_wait",
    "creations_request_upload", "creations_upload_file", "creations_upload_image",
    "creations_finalize_upload", "creations_get", "creations_show",
)


def allowed_tools_video():
    """--allowedTools da ABERTURA (image->video): Read/Write/Bash + as tools de vídeo do Magnific."""
    pref = _prefixo()
    tools = " ".join("%s__%s" % (pref, t) for t in _MAGNIFIC_VIDEO_TOOLS)
    return "Read Write Bash " + tools


# Trecho de instrução reaproveitado pelas etapas 6 e 7 (o padrão verificado).
def receita(n, alvo_desc, mode_override=None, quality=None):
    """Instrução do passo Magnific. `alvo_desc` descreve onde salvar os PNGs.

    `mode_override` força um modelo específico (ex.: a thumb usa GPT 2 em vez do default
    flux-2-klein). Quando None, usa `modo()`. `quality` (opcional, ex.: 'medium' p/ GPT-2)
    fixa a qualidade no images_generate — controla custo nos modelos quality-aware; omitido
    deixa o default do modelo."""
    mode = mode_override or modo()
    q = (', quality:"%s"' % quality) if quality else ""
    return (
        "PADRÃO MAGNIFIC (verificado) — GERE EM LOTE/PARALELO, NUNCA uma de cada vez. "
        "images_generate é ASSÍNCRONO: ele só ENFILEIRA (volta na hora com o creation) e quem "
        "espera concluir é creations_wait. Por isso faça em 3 FASES e NÃO intercale o download "
        "no meio da geração:\n"
        "FASE 1 — DISPARE TODAS DE UMA VEZ: num ÚNICO turno (várias tool calls juntas), chame "
        "mcp__magnific__images_generate %d vezes, UMA por prompt, cada uma com {prompt:<o prompt "
        "daquela imagem>, mode=\"%s\", aspectRatio=\"%s\", count=1%s}. ASPECT RATIO TRAVADO em %s "
        "— NUNCA mude (YouTube long-form). Guarde o `identifier` de TODOS os creations.\n"
        "FASE 2 — ESPERE TODAS: use mcp__magnific__creations_wait nos creations (passe os "
        "identifiers de uma vez quando der) até TODAS concluírem; pegue o webUrl de cada uma.\n"
        "FASE 3 — BAIXE TODAS: `Bash curl -L -o <arquivo> <webUrl>` salvando como %s (pode "
        "disparar os curl juntos).\n"
        "REGRA DE OURO: a FASE 1 inteira ANTES de qualquer creations_wait — assim as %d rodam "
        "CONCORRENTES no servidor, não em fila. Se UMA falhar, re-tente só ela. No fim, imprima "
        "quantos PNGs salvou."
        % (n, mode, ASPECT_BODY, q, ASPECT_BODY, alvo_desc, n)
    )


def receita_referencia(n, alvo_desc):
    """Instrução para gerar as N FICHAS DE REFERÊNCIA (corpo inteiro, fundo branco) e
    registrá-las como PERSONAGENS na Library do Magnific — é o lock REAL de consistência.

    Cada ficha vira um personagem reutilizável (references[].type=character) usado depois
    nas thumbs (Etapa 6) e nas imagens do vídeo (Etapa 7). O mapa nome->id sai em
    `referencias.json`. `alvo_desc` descreve onde salvar os PNG das fichas."""
    return (
        "PADRÃO MAGNIFIC — FICHAS DE REFERÊNCIA + LIBRARY (lock de personagem):\n"
        "ANTES de gerar, chame mcp__magnific__library_list {type:\"character\"} para ver se já "
        "existem personagens com esses nomes (não duplique — reaproveite o `id`).\n"
        "Para CADA linha de `prompts_referencia.txt` (começa com [Character N: NAME]):\n"
        "1) Gere a ficha: mcp__magnific__images_generate {prompt, mode=\"%s\", aspectRatio=\"%s\", "
        "count=1} — corpo inteiro, fundo branco, pose neutra (o próprio prompt já pede isso).\n"
        "2) Espere concluir com mcp__magnific__creations_wait e guarde o `identifier` do creation.\n"
        "3) Baixe o webUrl com `Bash curl -L -o <arquivo> <webUrl>` salvando como %s.\n"
        "4) REGISTRE na Library: mcp__magnific__library_create {name:<NAME, só A-Z 0-9 _ - , máx 50>, "
        "type:\"character\", images:[{creationIdentifier:<identifier do passo 2>}]} e ANOTE o `id` numérico.\n"
        "Gere as %d fichas em paralelo quando possível (várias chamadas de tool numa só vez). "
        "No FIM, grave referencias.json (com Write) — uma lista "
        "[{\"tag\":\"Character N\",\"name\":\"NAME\",\"libraryId\":<id>,\"file\":\"<arquivo>\"}] — "
        "é o mapa que liga cada tag [Character N: NAME] à sua ficha na Library. Imprima quantas fichas registrou."
        % (modo(), ASPECT_FICHA, alvo_desc, n)
    )


def instr_char_lock(ref_imagem_extra=""):
    """Como aplicar o lock de personagem (Library) ao gerar thumbs/imagens.

    `ref_imagem_extra` (opcional): caminho de uma imagem a anexar como references[].type=image
    em TODOS os prompts (ex.: thumb_selected.png na Etapa 7), além dos personagens da Library."""
    extra = ""
    if ref_imagem_extra:
        extra = (
            "Anexe TAMBÉM `%s` como reference {type:\"image\", identifier:<creation>} em todo prompt. "
            "UPLOAD (faça UMA vez no início e reaproveite o identifier em todos os prompts): "
            "(a) mcp__magnific__creations_request_upload {filename:\"%s\", contentType:\"image/png\"} → "
            "guarde a `uploadUrl` e o `identifier` retornados; "
            "(b) `Bash curl -X PUT -H \"Content-Type: image/png\" --data-binary @%s \"<uploadUrl>\"` "
            "para subir o binário; "
            "(c) mcp__magnific__creations_finalize_upload {identifier:<o do passo a>} para confirmar. "
            "O `identifier` resultante é o que entra em references[].identifier. " % (
                ref_imagem_extra, ref_imagem_extra, ref_imagem_extra
            )
        )
    return (
        "LOCK DE PERSONAGEM (Library): leia `referencias.json` no início. Para CADA prompt, detecte as "
        "tags [Character N: NAME] presentes e monte references=[{type:\"character\", identifier:\"<libraryId>\"}] "
        "com o id de CADA personagem citado (o número como string). %s"
        "Passe esse `references` no mcp__magnific__images_generate junto com "
        "{prompt, mode, aspectRatio:\"%s\", count} — aspectRatio FICA EM %s sempre (não troque). "
        "Assim rosto/cabelo/look ficam idênticos às fichas. Se referencias.json não existir, gere só pelo prompt."
        % (extra, ASPECT_BODY, ASPECT_BODY)
    )


def instr_refs_estilo(paths):
    """Anexa as thumbs de referência do CANAL como base de estilo de TODA thumb (Etapa 6).

    `paths` = lista de caminhos ABSOLUTOS das imagens (a usuária dropa em
    longform/assets/thumb_ref_estilo/). Sobe cada uma como creation e a injeta como
    references[].type=image em toda geração — assim a thumb segue o estilo/formato dessas
    capas. Vazio -> string vazia (a thumb sai só pelo prompt + direção de arte)."""
    if not paths:
        return ""
    listagem = "; ".join('"%s"' % p for p in paths)
    return (
        "BASE DE ESTILO DA CAPA (referências FIXAS do canal — a usuária pediu para SEGUIR o "
        "estilo/formato destas capas em QUALQUER thumb): use estas imagens como referência "
        "visual de composição, enquadramento, iluminação (bem clara), porte musculoso do alfa "
        "e estética geral: %s.\n"
        "UPLOAD (faça UMA vez no início e reaproveite os identifiers em todas as gerações): "
        "para CADA arquivo, (a) mcp__magnific__creations_request_upload {filename:<nome>, "
        "contentType:\"image/png\" ou \"image/jpeg\" conforme a extensão} → guarde uploadUrl e "
        "identifier; (b) `Bash curl -X PUT -H \"Content-Type: <tipo>\" --data-binary @<arquivo> "
        "\"<uploadUrl>\"`; (c) mcp__magnific__creations_finalize_upload {identifier:<o do passo a>}.\n"
        "Some esses references {type:\"image\", identifier:<cada um>} ao array `references` de "
        "TODA chamada de images_generate da thumb (junto com os personagens da Library, se "
        "houver). SIGA o estilo/formato/luz destas referências; NÃO copie os personagens delas "
        "— os personagens vêm do prompt/Library. Se o modelo da thumb recusar reference "
        "type=image, IGNORE as referências de estilo e gere só pelo prompt (NÃO falhe a etapa)."
        % listagem
    )


# ── Fluxo determinístico da CAPA (Etapa 6) — 3 instruções de 1 job cada ───────
# O Python (s6_thumbnails) é que SEQUENCIA estes passos e CHECA o artefato entre eles —
# o agente não decide a escalada (era o que dava drift). Ordem: A (GPT 2 cheio) → se moderar,
# B (base leve no GPT 2) → C (refino editando no Nano Banana 2). NUNCA cold no Nano Banana.

def instr_thumb_principal(mode, lock, estilo, alvo_arq, status_arq, quality, sugestoes="",
                           categoria=None):
    """PASSO A — gera a capa NO GPT 2 (mode) a partir de prompts_thumbnail.txt. Se moderar, PARA
    e sinaliza (não troca de modelo) — o Python decide a escalada.

    `categoria`: chave canônica da categoria ('mafia', 'selena', etc.) — adapta a direção de arte
    do lead masculino e cenário. None usa o default (selena/Alpha King)."""
    aj = ("\n\nAJUSTES OBRIGATÓRIOS (corrija isto da tentativa anterior, reprovada pelo QA): %s"
          % sugestoes) if sugestoes else ""
    direcao = thumb_direcao(categoria)
    return (
        "Você é a Etapa 6 (thumbnail/capa) de uma esteira de vídeo. NÃO peça confirmação.\n"
        "Leia `prompts_thumbnail.txt` (tem 1 prompt da capa 16:9 — a CAPA do vídeo).\n\n"
        "%s\n\n%s\n\n%s\n\n"
        "GERE 1 ÚNICA capa NO GPT 2 (modelo da capa): mcp__magnific__images_generate {prompt:<o de "
        "prompts_thumbnail.txt%s>, mode:\"%s\", aspectRatio:\"%s\", count:1, quality:\"%s\"}. "
        "É ASSÍNCRONO — espere com mcp__magnific__creations_wait, pegue o webUrl e baixe com "
        "`Bash curl -L -o %s \"<webUrl>\"`.\n"
        "REGRA DURA: se a geração for MODERADA/NSFW/bloqueada pelo provedor, NÃO troque de modelo, "
        "NÃO use o Nano Banana, NÃO alivie nada por conta própria — apenas PARE e escreva `%s` com "
        "Write: {\"generated\": false, \"moderated\": true, \"model\": \"%s\"}. Se a capa salvar "
        "OK, escreva `%s`: {\"generated\": true, \"moderated\": false, \"model\": \"%s\", "
        "\"file\": \"thumb_01.png\"}. Imprima no fim se GEROU ou se MODEROU."
        % (lock, estilo, direcao,
           (" — e aplique os AJUSTES abaixo" if sugestoes else ""),
           mode, ASPECT_BODY, quality, alvo_arq, status_arq, mode, status_arq, mode)
    ) + aj


def instr_thumb_base_leve(mode, lock, estilo, base_arq, status_arq, sugestoes="",
                           categoria=None):
    """PASSO B — a cena cheia moderou no GPT 2. Gera uma BASE leve/segura, AINDA no GPT 2 (mode).

    `categoria`: chave canônica da categoria — adapta a direção de arte do lead masculino."""
    aj = ("\n\nAJUSTES do QA a respeitar: %s" % sugestoes) if sugestoes else ""
    direcao = thumb_direcao(categoria)
    lead_desc = ("chefão da máfia cabelo-curto-slicked-back/tatuagens/musculoso"
                 if categoria == "mafia" else "Alpha King cabelo-longo/musculoso")
    return (
        "Você é a Etapa 6 (thumbnail/capa). A cena cheia FOI MODERADA no GPT 2. NÃO peça "
        "confirmação. Releia `prompts_thumbnail.txt` e ALIVIE o prompt: MESMA cena/era/"
        "personagens, mas versão SEGURA que passe no GPT 2 — casal apenas JUNTO, sério/tenro, SEM "
        "decote (gola alta/modesto), SEM pose dominante/encurralando; TIRE as palavras-gatilho "
        "(corners, traps, dominant, submissive, sensual, possessive, jealousy, lips parted, breath "
        "caught, charged) e MANTENHA 'intense eye contact, charged tension, glamorous'. Capa CLARA/"
        "glam, %s.\n\n%s\n\n%s\n\n%s\n\n"
        "GERE essa BASE NO GPT 2: mcp__magnific__images_generate {prompt:<a versão aliviada>, "
        "mode:\"%s\", aspectRatio:\"%s\", count:1, quality:\"medium\"}; espere "
        "(creations_wait), baixe e salve `%s`. Escreva `%s`: {\"base_generated\": true/false, "
        "\"model\": \"%s\"}. NÃO gere NADA no Nano Banana aqui — só a base no GPT 2."
        % (lead_desc, lock, estilo, direcao, mode, ASPECT_BODY, base_arq, status_arq, mode)
    ) + aj


def instr_thumb_refino(base_arq, alvo_arq, status_arq, refino_modelo):
    """PASSO C — refina a BASE (que o GPT 2 gerou) no Nano Banana 2 como EDIÇÃO. Nunca cold."""
    return (
        "Você é a Etapa 6 (thumbnail/capa). Existe `%s` — uma BASE clara/glam que o GPT 2 gerou e "
        "que PASSOU na moderação. Sua tarefa é REFINAR essa base no Nano Banana 2 como EDIÇÃO — "
        "NUNCA gerar do zero. Leia `prompts_thumbnail.txt` p/ saber o que a cena ORIGINAL pedia. "
        "NÃO peça confirmação.\n"
        "1) SUBA a base: mcp__magnific__creations_request_upload {filename:\"%s\", "
        "contentType:\"image/png\"} → guarde uploadUrl e identifier; `Bash curl -X PUT -H "
        "\"Content-Type: image/png\" --data-binary @%s \"<uploadUrl>\"`; "
        "mcp__magnific__creations_finalize_upload {identifier:<o do passo 1>}.\n"
        "2) EDITE no Nano Banana 2: mcp__magnific__images_generate {mode:\"%s\", references:"
        "[{type:\"image\", identifier:<o identifier da base>}], aspectRatio:\"%s\", prompt:<em "
        "LINGUAGEM SIMPLES, traga de volta o que a cena original pedia (ex.: 'make him lean toward "
        "the side of her neck, eyes closed'), MANTENDO os mesmos personagens/rostos/roupas/cenário "
        "e a luz CLARA e glam, 16:9 widescreen, no text>}.\n"
        "3) Espere (creations_wait), baixe o webUrl e salve `%s` (sobrescreve). Escreva `%s`: "
        "{\"refined\": true/false, \"model\": \"%s\"}. ATENÇÃO: o Nano Banana 2 COBRA crédito via "
        "MCP — faça UMA chamada de edição só."
        % (base_arq, base_arq, base_arq, refino_modelo, ASPECT_BODY, alvo_arq, status_arq,
           refino_modelo)
    )


def receita_tts(texto_arquivo, voz, saida, modelo_tts):
    """Instrução do passo TTS via Magnific (Etapa 4, provider=magnific).

    `texto_arquivo` e `saida` são nomes RELATIVOS à pasta do projeto (o claude roda lá).
    Sintetiza UM bloco de texto (o chunking em blocos é feito no Python, em s4)."""
    return (
        "PADRÃO MAGNIFIC TTS (narração): leia TODO o texto do arquivo `%s` com a tool Read. "
        "Chame mcp__magnific__audio_tts com {text:<o conteúdo COMPLETO do arquivo>, voiceId:%s, "
        "model:\"%s\", speed:1.0}. É ASSÍNCRONO — use mcp__magnific__creations_wait até concluir. "
        "Pegue a URL final do áudio (campo url/webUrl do creation) e BAIXE com "
        "`Bash curl -L -o \"%s\" \"<url>\"`. Confira que `%s` existe e tem tamanho > 0. "
        "NÃO peça confirmação; não gere nada além desse MP3."
        % (texto_arquivo, voz, modelo_tts, saida, saida)
    )


def gerar(proj, log, cancel, instrucoes, modelo="sonnet"):
    """Roda um claude -p com o MCP do Magnific seguindo `instrucoes`."""
    return rodar_claude(instrucoes, proj.dir, log, cancel,
                        modelo=modelo, allowed_tools=allowed_tools())
