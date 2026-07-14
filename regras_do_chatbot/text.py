"""Utilitários de normalização de texto para compreensão de linguagem natural."""

import re

# ── Mapa de acentos ──
_ACENTOS_TABLE = str.maketrans(
    "ÁÀÃÂÄÉÈÊËÍÌÎÏÓÒÕÔÖÚÙÛÜÇáàãâäéèêëíìîïóòõôöúùûüç",
    "AAAAAEEEEIIIIOOOOOUUUUCaaaaaeeeeiiiiooooouuuuc",
)

# ── Expansão de gírias, abreviações e escrita informal ──
_EXPANSOES = {
    # Negação
    "naum": "não",
    "num": "não",
    "nao": "não",
    # Afirmação
    "ss": "sim",
    "s": "sim",
    "si": "sim",
    "y": "sim",
    "yes": "sim",
    # Pronomes / tratamento
    "vc": "você",
    "vcs": "vocês",
    "ce": "você",
    "cê": "você",
    "sr": "senhor",
    "sra": "senhora",
    "dotô": "doutor",
    "doto": "doutor",
    # Advérbios / conectivos
    "pq": "porque",
    "porq": "porque",
    "tb": "também",
    "tbm": "também",
    "tmb": "também",
    "td": "tudo",
    "ta": "está",
    "tá": "está",
    "tava": "estava",
    "to": "estou",
    "tô": "estou",
    "tou": "estou",
    "tao": "estão",
    "tão": "estão",
    "tamos": "estamos",
    "tavam": "estavam",
    "d+": "demais",
    "mt": "muito",
    "mto": "muito",
    "ctz": "certeza",
    "qh": "quase",
    "qse": "quase",
    "qdo": "quando",
    "qnd": "quando",
    "qnt": "quanto",
    "blz": "beleza",
    "flw": "falou",
    "vlw": "valeu",
    "brigado": "obrigado",
    "brigada": "obrigada",
    "obg": "obrigado",
    "valeu": "obrigado",
    "dps": "depois",
    "dpois": "depois",
    "agr": "agora",
    "hj": "hoje",
    "amanha": "amanhã",
    "ngm": "ninguém",
    "nguem": "ninguém",
    "algm": "alguém",
    "algue": "alguém",
    "pra": "para",
    "pro": "para",
    "neh": "né",
    "ne": "né",
    "qria": "queria",
    "qro": "quero",
    "qremos": "queremos",
    "qrer": "querer",
    "fzr": "fazer",
    "prc": "precisa",
    "prcp": "preciso",
    "prcisa": "precisa",
    "prciso": "preciso",
    "rs": "risos",
    "haha": "risos",
    "kkk": "risos",
    "nunk": "nunca",
    "nunc": "nunca",
    "soh": "só",
    "so": "só",
}

# ── Termos previdenciários escritos sem acento ou com grafia alternativa ──
_TERMOS_SEM_ACENTO = [
    (re.compile(r'\bauxilio\b', re.IGNORECASE), 'auxílio'),
    (re.compile(r'\bdoenca\b', re.IGNORECASE), 'doença'),
    (re.compile(r'\binvalidez\b', re.IGNORECASE), 'invalidez'),
    (re.compile(r'\binvalida\b', re.IGNORECASE), 'inválida'),
    (re.compile(r'\baposentadoria\b', re.IGNORECASE), 'aposentadoria'),
    (re.compile(r'\baposentar\b', re.IGNORECASE), 'aposentar'),
    (re.compile(r'\baposenta\b', re.IGNORECASE), 'aposenta'),
    (re.compile(r'\bhernia\b', re.IGNORECASE), 'hérnia'),
    (re.compile(r'\bobito\b', re.IGNORECASE), 'óbito'),
    (re.compile(r'\bviuva\b', re.IGNORECASE), 'viúva'),
    (re.compile(r'\bviuvo\b', re.IGNORECASE), 'viúvo'),
    (re.compile(r'\bpensao\b', re.IGNORECASE), 'pensão'),
    (re.compile(r'\brevisao\b', re.IGNORECASE), 'revisão'),
    (re.compile(r'\brevisional\b', re.IGNORECASE), 'revisional'),
    (re.compile(r'\brevisar\b', re.IGNORECASE), 'revisar'),
    (re.compile(r'\bjudicial\b', re.IGNORECASE), 'judicial'),
    (re.compile(r'\bjudiciario\b', re.IGNORECASE), 'judiciário'),
    (re.compile(r'\bafastado\b', re.IGNORECASE), 'afastado'),
    (re.compile(r'\bafastamento\b', re.IGNORECASE), 'afastamento'),
    (re.compile(r'\bmedico\b', re.IGNORECASE), 'médico'),
    (re.compile(r'\bmedicao\b', re.IGNORECASE), 'medicação'),
    (re.compile(r'\bcirurgia\b', re.IGNORECASE), 'cirurgia'),
    (re.compile(r'\bcirurgico\b', re.IGNORECASE), 'cirúrgico'),
    (re.compile(r'\binternado\b', re.IGNORECASE), 'internado'),
    (re.compile(r'\binternacao\b', re.IGNORECASE), 'internação'),
    (re.compile(r'\blesao\b', re.IGNORECASE), 'lesão'),
    (re.compile(r'\bdoente\b', re.IGNORECASE), 'doente'),
    (re.compile(r'\bdoenca\b', re.IGNORECASE), 'doença'),
]

# ── Palavras conhecidas de benefícios que podem aparecer grudadas ──
_PALAVRAS_BENEFICIOS = [
    "auxílio", "doença", "auxilio", "doenca",
    "aposentadoria", "aposentar",
    "invalidez", "invalida", "inválida",
    "incapacidade",
    "pensão", "pensao", "morte", "viuva", "viúva", "obito", "óbito",
    "revisão", "revisao",
    "rural", "idade",
    "benefício", "beneficio",
    "judicial", "trabalhador", "trabalhadora",
    "segurado", "especial",
    "auxiliodoença",
]


def remover_acentos(texto: str) -> str:
    """Remove acentos diacríticos de uma string."""
    return texto.translate(_ACENTOS_TABLE)


def expandir_girias(texto: str) -> str:
    """Expande gírias e abreviações informais para a forma padrão."""
    palavras = texto.split()
    expandidas = []
    for p in palavras:
        p_lower = p.lower()
        if p_lower in _EXPANSOES:
            expandidas.append(_EXPANSOES[p_lower])
        else:
            expandidas.append(p)
    return " ".join(expandidas)


def _corrigir_termos_sem_acento(texto: str) -> str:
    """Recompõe acentos em termos previdenciários conhecidos."""
    result = texto
    for pattern, replacement in _TERMOS_SEM_ACENTO:
        result = pattern.sub(replacement, result)
    return result


def _desgrudar_palavras(texto: str) -> str:
    """Tenta separar palavras de benefício que foram escritas grudadas.

    Ex: 'auxiliodoença' -> 'auxílio doença', 'aposentadoriarural' -> 'aposentadoria rural'
    """
    for termo in _PALAVRAS_BENEFICIOS:
        if termo in texto:
            # Só desgruda se o termo aparecer como parte de uma palavra maior
            texto = re.sub(
                rf'(?<=[a-z]){re.escape(termo)}(?=[a-z])',
                f'{termo} ',
                texto,
            )
    return texto


def normalizar_texto(texto: str) -> str:
    """Normalização completa do texto para processamento.

    Etapas:
      1. Expande gírias e abreviações
      2. Recompõe acentos em termos previdenciários
      3. Remove acentos diacríticos
      4. Tenta desgrudar palavras compostas
      5. Normaliza espaços em branco
    """
    if not texto:
        return texto

    t = texto.strip()
    t = expandir_girias(t)
    t = _corrigir_termos_sem_acento(t)
    t = remover_acentos(t)
    t = _desgrudar_palavras(t)
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


def limpar_para_confirmacao(texto: str) -> str:
    """Limpa o texto especificamente para detecção de sim/não."""
    t = texto.strip().lower()
    t = remover_acentos(t)
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t)
    return t.strip()
