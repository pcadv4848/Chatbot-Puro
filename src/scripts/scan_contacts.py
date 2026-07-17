"""Script para varrer contatos/chats do WhatsApp e adicionar à tabela attended_clients.

Uso:
    python -m src.scripts.scan_contacts

Requer o OpenWA conectado e acessível via API.
"""
import asyncio
import logging
import re
import unicodedata
from difflib import SequenceMatcher

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

from src.config import settings

LISTA_ALVOS = [
    "NAIR DOS SANTOS DE JESUS",
    "CLAUDOMIRO DA SILVA CRUZ",
    "REGINALDO FERREIRA QUEIROZ",
    "DIONATHAS GIMENNYS BATISTA REIS",
    "JUDITE GONÇALVES DE ALMEIDA",
    "ELENI DOS SANTOS",
    "MARCOS ANTÔNIO DE ABREU",
    "ERICO DOS SANTOS DE JESUS",
    "LUCINETE DA SILVA BPC",
    "CICERO JAILSON AUX ACIDENTE",
    "EDSON DE MATOS SANTOS BPC",
    "CLAUDOMIRO DA SILVA CRUZ",
    "MARIA DA CONCEICAO DA CRUZ MONTEIRO",
    "MARIVALDA DA SILVA DANTAS",
    "VANESSA BORGES BPC DO FILHO",
    "VALDY MOURA BPC FILHO",
    "VANESSA SALARIO MATERNIDADE",
    "VERONICE SANTANA DE ARAUJO BPC",
    "ALANA GOMES PAIM",
    "WESLEY VINICIUS",
    "CELIA APARECIDA MINCAPELLE BRITO",
    "MARIA EDUARDA MONTEIRO DE FIGUEIREDO",
    "YASMIN PEREIRA DE OLIVEIRA",
    "CLÁUDIA LOPES DA SILVA",
    "EDILMA SANTOS DE OLIVEIRA",
]

STOPWORDS = {"DE", "DA", "DO", "DOS", "DAS", "E"}
TAGS = re.compile(
    r'\b(BPC|AUX\s*ACIDENTE|SALARIO\s*MATERNIDADE|DO\s*FILHO|'
    r'BPC\s*DO\s*FILHO|CLIENTE\s*PC|CLIENTE)\b',
    re.IGNORECASE,
)
TAG_TOKEN = re.compile(r'^(BPC|ACIDENTE|FILHO|MATERNIDADE|CLIENTE|PC|ESCRITORIO|ESCOLA|BAIANO)$', re.IGNORECASE)


def _limpar(nome: str) -> str:
    n = unicodedata.normalize('NFKD', nome.upper())
    n = n.encode('ascii', 'ignore').decode('ascii')
    n = TAGS.sub('', n)
    n = re.sub(r'[^A-Z0-9\s]', '', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def _tokenizar(nome: str) -> list[str]:
    return [t for t in _limpar(nome).split() if t not in STOPWORDS and len(t) > 1]


def _tokenizar_sem_tag(nome: str) -> list[str]:
    return [t for t in _tokenizar(nome) if not TAG_TOKEN.match(t)]


def _primeiro_nome(nome: str) -> str:
    tokens = _tokenizar_sem_tag(nome)
    return tokens[0] if tokens else ""


def _subtokenizar(tokens: list[str]) -> set[str]:
    subs = set()
    for t in tokens:
        for i in range(len(t) - 2):
            subs.add(t[i:i+3])
    return subs


def _match_score(alvo: str, candidato: str) -> float:
    alvo_clean = _limpar(alvo)
    cand_clean = _limpar(candidato)

    if not alvo_clean or not cand_clean:
        return 0.0

    tokens_alvo = _tokenizar_sem_tag(alvo)
    tokens_cand = _tokenizar_sem_tag(candidato)

    if not tokens_alvo or not tokens_cand:
        return 0.0

    set_alvo = set(tokens_alvo)
    set_cand = set(tokens_cand)

    primeiro_alvo = tokens_alvo[0]
    primeiro_cand = tokens_cand[0]

    if not primeiro_alvo or not primeiro_cand:
        return 0.0

    score_primeiro = SequenceMatcher(None, primeiro_alvo, primeiro_cand).ratio()

    if score_primeiro < 0.7:
        return score_primeiro * 0.3

    comuns = set_alvo & set_cand
    total_unicos = len(set_alvo | set_cand)
    score_tokens = len(comuns) / total_unicos if total_unicos > 0 else 0

    subs_alvo = _subtokenizar(tokens_alvo[1:])
    subs_cand = _subtokenizar(tokens_cand[1:])
    sub_comuns = subs_alvo & subs_cand if subs_alvo and subs_cand else set()
    score_sub = len(sub_comuns) / max(len(subs_alvo | subs_cand), 1) if (subs_alvo or subs_cand) else 0

    score_contem = 0.0
    if len(tokens_alvo) >= 2 and len(tokens_cand) >= 2:
        menor = set_alvo if len(set_alvo) <= len(set_cand) else set_cand
        maior = set_cand if len(set_alvo) <= len(set_cand) else set_alvo
        if menor and menor.issubset(maior):
            score_contem = 0.9

    penalidade_curto = 1.0
    if len(set_alvo) <= 2 and len(set_cand) <= 2:
        penalidade_curto = 0.6

    score = (
        score_primeiro * 0.40 +
        score_tokens * 0.25 +
        score_sub * 0.10 +
        max(score_contem * 0.25, 0)
    ) * penalidade_curto

    return min(score, 1.0)


def _extrair_numero(jid: str) -> str | None:
    if not jid:
        return None
    if '@lid' in jid:
        return None
    parte = jid.split('@')[0]
    parte = re.sub(r'\D', '', parte)
    return parte if parte else None


async def _fetch_all(client: httpx.AsyncClient, url: str, headers: dict, params: dict | None = None) -> list[dict]:
    items = []
    offset = 0
    limit = 1000
    while True:
        try:
            p = {"limit": limit, "offset": offset}
            if params:
                p.update(params)
            resp = await client.get(url, headers=headers, params=p, timeout=30)
            if resp.status_code != 200:
                logger.error("Erro %s: %s", resp.status_code, resp.text[:200])
                break
            data = resp.json()
            if not isinstance(data, list):
                logger.warning("Resposta inesperada: %s", type(data))
                break
            if not data:
                break
            items.extend(data)
            if len(data) < limit:
                break
            offset += limit
        except Exception as e:
            logger.error("Erro na requisição: %s", e)
            break
    return items


async def main():
    logger.info("=" * 60)
    logger.info("VARREDURA DE CONTATOS WHATSAPP")
    logger.info("=" * 60)

    from src.services.whatsapp_openwa import _get_session_id_garantido
    session_id = await _get_session_id_garantido()
    base = settings.openwa_api_url
    headers = {
        "X-API-Key": settings.openwa_api_key,
        "Content-Type": "application/json",
    }

    logger.info("Session ID: %s", session_id)

    async with httpx.AsyncClient(timeout=30) as client:
        logger.info("Buscando chats...")
        chats = await _fetch_all(client, f"{base}/sessions/{session_id}/chats", headers)
        logger.info("  Total chats: %d", len(chats))

        logger.info("Buscando contatos...")
        contacts = await _fetch_all(client, f"{base}/sessions/{session_id}/contacts", headers)
        logger.info("  Total contatos: %d", len(contacts))

    candidatos: dict[str, dict] = {}

    for chat in chats:
        if chat.get("isGroup"):
            continue
        jid = chat.get("id", "")
        nome = (chat.get("name") or "").strip()
        numero = _extrair_numero(jid)
        if not numero or not nome:
            continue
        if numero not in candidatos:
            candidatos[numero] = {"jid": jid, "numero": numero, "nome": nome, "fonte": "chat"}

    for ct in contacts:
        jid = ct.get("id", "")
        nome = (ct.get("name") or ct.get("pushName") or "").strip()
        numero = ct.get("number") or _extrair_numero(jid)
        if not numero or not nome:
            continue
        if numero not in candidatos:
            candidatos[numero] = {"jid": jid, "numero": numero, "nome": nome, "fonte": "contato"}
        else:
            existente = candidatos[numero]["nome"]
            if len(nome) > len(existente):
                candidatos[numero]["nome"] = nome

    logger.info("Candidatos únicos com nome: %d", len(candidatos))
    logger.info("")

    from src.services.attended_clients import mark_attended, is_attended

    resultados: list[dict] = []

    # ── PRIMEIRA PASSAGEM: fuzzy matching (sem dedup) ──
    for alvo in LISTA_ALVOS:
        melhor_score = 0.0
        melhor_cand = None

        for num, cand in candidatos.items():
            score = _match_score(alvo, cand["nome"])
            if score > melhor_score:
                melhor_score = score
                melhor_cand = cand

        if melhor_score >= 0.55 and melhor_cand:
            resultados.append({
                "alvo": alvo,
                "nome_encontrado": melhor_cand["nome"],
                "numero": melhor_cand["numero"],
                "score": round(melhor_score, 3),
                "fonte": melhor_cand["fonte"],
                "passo": 1,
            })

    # ── SEGUNDA PASSAGEM: primeiro nome exato p/ candidatos com nome curto ──
    alvos_matchados_p1 = {r["alvo"] for r in resultados}
    for alvo in LISTA_ALVOS:
        if alvo in alvos_matchados_p1:
            continue
        primeiro_alvo = _primeiro_nome(alvo)
        if not primeiro_alvo:
            continue

        for num, cand in candidatos.items():
            # Evitar readicionar mesmo número já matchado (em QUALQUER alvo)
            if any(r["numero"] == num for r in resultados):
                continue
            primeiro_cand = _primeiro_nome(cand["nome"])
            if not primeiro_cand:
                continue
            if primeiro_alvo != primeiro_cand:
                continue

            tokens_sem_tag = _tokenizar_sem_tag(cand["nome"])
            if len(tokens_sem_tag) > 1:
                continue

            resultados.append({
                "alvo": alvo,
                "nome_encontrado": cand["nome"],
                "numero": num,
                "score": 0.60,
                "fonte": cand["fonte"],
                "passo": 2,
            })
            break

    # ── EXIBE RESULTADOS ──
    logger.info("=" * 60)
    logger.info("MATCHES ENCONTRADOS")
    logger.info("=" * 60)

    adicionados = 0
    ja_existiam = 0

    for r in sorted(resultados, key=lambda x: (x["passo"], -x["score"])):
        simbolo = "✅" if r["score"] >= 0.7 else "🔷"

        logger.info(
            "%s [%.0f%%|passo%d] %s",
            simbolo, r["score"] * 100, r["passo"], r["alvo"],
        )
        logger.info(
            "   → '%s' (%s) fonte=%s",
            r["nome_encontrado"], r["numero"], r["fonte"],
        )

        ja_era = await is_attended(r["numero"])
        if ja_era:
            logger.info("   → Já estava em attended_clients")
            ja_existiam += 1
        else:
            await mark_attended(r["numero"])
            logger.info("   → ADICIONADO ao attended_clients")
            adicionados += 1

    # ── SEM MATCH (mostra dados para revisão manual) ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("SEM MATCH — REVISAO MANUAL NECESSARIA")
    logger.info("=" * 60)

    alvos_matchados = {r["alvo"] for r in resultados}
    for alvo in LISTA_ALVOS:
        if alvo in alvos_matchados:
            continue
        logger.info("")
        logger.info("❌ %s", alvo)
        # Mostra top 3 candidatos mais próximos para review manual
        top = sorted(
            [
                (num, cand["nome"], _match_score(alvo, cand["nome"]))
                for num, cand in candidatos.items()
            ],
            key=lambda x: -x[2],
        )[:3]
        for num, nome, score in top:
            if score > 0:
                logger.info("   [%.0f%%] '%s' (%s)", score * 100, nome, num)
            else:
                break

    logger.info("")
    logger.info("=" * 60)
    logger.info("RESUMO")
    logger.info("  Total na lista alvo: %d", len(LISTA_ALVOS))
    logger.info("  Chats (não-grupo): %d", len(chats))
    logger.info("  Contatos: %d", len(contacts))
    logger.info("  Candidatos únicos: %d", len(candidatos))
    logger.info("  Match (passo 1 - fuzzy): %d", sum(1 for r in resultados if r["passo"] == 1))
    logger.info("  Match (passo 2 - nome exato): %d", sum(1 for r in resultados if r["passo"] == 2))
    logger.info("  Total matches: %d", len(resultados))
    logger.info("  Já existiam: %d", ja_existiam)
    logger.info("  Novos adicionados: %d", adicionados)
    logger.info("  Sem match: %d", len(LISTA_ALVOS) - len(resultados))
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
