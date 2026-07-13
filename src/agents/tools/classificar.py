"""Classificação do tipo de benefício previdenciário por palavras-chave.

Usada pelo agente classificador para determinar qual documento gerar.
Pode ser substituída futuramente por uma call ao Claude para maior precisão.

Nota sobre nomes de templates:
  Os nomes em BENEFICIOS correspondem EXATAMENTE aos nomes dos arquivos
  .docx em src/templates/. "ADMNISTRATIVA" (sem I) e "AXULIO" (sem U)
  não são erros de digitação — são os nomes originais dos documentos de
  origem e devem permanecer como estão para que o sistema encontre os
  arquivos corretos.
"""
from difflib import SequenceMatcher

from src.utils.text import normalizar_texto, remover_acentos

# Palavras que podem ser ignoradas na comparação difusa
_PALAVRAS_IGNORAR = {
    "de", "da", "do", "das", "dos", "em", "para", "com", "um", "uma",
    "uns", "umas", "o", "a", "os", "as", "no", "na", "por", "pra",
    "que", "é", "são", "estou", "está", "meu", "minha", "quero",
    "queria", "preciso", "gostaria", "pode", "como", "vou", "eu",
    "ele", "ela", "me", "se", "te", "nós", "você", "voce", "sim",
    "não", "nao", "oi", "ola", "olá", "tudo", "bem", "mais",
    "mas", "aqui", "ali", "lá", "isso", "disso", "nisso",
    "senhor", "senhora", "filho", "filha", "meu", "minha",
    "bom", "dia", "tarde", "noite", "fala", "diz", "então",
    "entao", "só", "so", "já", "ja", "agora", "depois",
    "doutor", "dotô", "doto", "ajuda", "ajude",
}

# ── conteúdo de BENEFICIOS e PALAVRAS_CHAVE omitido por brevidade ──

# Mapeamento completo dos tipos de benefício para documentos necessários
BENEFICIOS = {
    "incapacidade": {
        "nome": "Benefício por Incapacidade",
        "subtipos": {
            "auxilio_doenca": "Auxílio-Doença",
            "aposentadoria_invalidez": "Aposentadoria por Invalidez",
        },
        "documentos": {
            "adm": [
                "CONTRATO ADM BENEFICIO POR INCAPACIDADE 30% MENSAL",
                "PROCURAÇÃO ADMNISTRATIVA",
            ],
            "judicial": [
                "CONTRATO 30% AXULIO DOENÇA",
                "PROCURAÇÃO JUDICIAL 2",
            ],
        },
    },
    "idade_rural": {
        "nome": "Aposentadoria por Idade Rural",
        "subtipos": {},
        "documentos": {
            "adm": [
                "CONTRATO ADM",
                "PROCURAÇÃO ADMNISTRATIVA",
                "MODELO INICIAL ADMINISTRATIVA",
            ],
            "judicial": [
                "CONTRATO 6-2",
                "PROCURAÇÃO JUDICIAL 2",
            ],
        },
    },
    "revisao": {
        "nome": "Revisão de Benefício",
        "subtipos": {},
        "documentos": {
            "adm": ["CONTRATO ADM", "PROCURAÇÃO ADMNISTRATIVA"],
            "judicial": ["CONTRATO 30%", "PROCURAÇÃO JUDICIAL 2"],
        },
    },
    "pensao": {
        "nome": "Pensão por Morte",
        "subtipos": {},
        "documentos": {
            "adm": ["CONTRATO ADM", "PROCURAÇÃO ADMNISTRATIVA"],
            "judicial": ["CONTRATO 30%", "PROCURAÇÃO JUDICIAL 2"],
        },
    },
    "outro": {
        "nome": "Outro Benefício",
        "subtipos": {},
        "documentos": {
            "adm": ["CONTRATO ADM", "PROCURAÇÃO ADMNISTRATIVA"],
            "judicial": ["CONTRATO 6-2", "PROCURAÇÃO JUDICIAL 2"],
        },
    },
}

# Declarações complementares disponíveis para TODOS os tipos de benefício
DECLARACOES = [
    "DECLARAÇÃO DE INSUFICIÊNCIA",
    "DECLARAÇÃO DE RESIDENCIA",
]

# Palavras-chave organizadas por tipo → esfera → lista de termos
# A ordem importa: o primeiro match encontrado é retornado.
PALAVRAS_CHAVE = {
    "incapacidade": {
        # Judicial primeiro (mais específico) para evitar falsos positivos com 'adm'
        "judicial": [
            "auxílio-doença judicial",
            "ação de auxílio-doença",
            "ação auxílio-doença",
            "processo de auxílio-doença",
            "processo auxílio-doença",
            "ação por incapacidade",
            "judicial incapacidade",
        ],
        "adm": [
            "auxílio-doença", "auxilio doenca", "auxílio doença",
            "incapacidade", "doente", "doença", "licença médica",
            "afastado", "cirurgia", "invalidez", "inválida", "invalida",
            "não consigo trabalhar", "nao consigo trabalhar",
            "não posso trabalhar", "nao posso trabalhar",
            "coluna", "costa", "hérnia", "hernia", "problema de saúde",
            "cirurgia", "internado", "operar", "lesão", "lesao",
            # Gírias / escrita informal
            "inválido", "inválida", "aleijado", "aleijada",
            "quebrei", "quebrou", "quebrado",
            "acidentado", "acidente", "atropelado",
            "de baixa", "baixa médica", "baixa medica",
            "doenca do trabalho", "doença do trabalho",
            "insalubre", "insalubridade",
            "perícia", "pericia", "perici",
            "incapaz",
            "auxilio doença", "auxiliodoenca", "auxiliodoença",
            # Dialeto informal / rural
            "tô doente", "to doente", "tou doente",
            "não aguento mais", "nao aguento mais",
            "não dou mais conta", "nao dou mais conta",
            "não consigo mais trabalhar", "nao consigo mais trabalhar",
            "não consigo trabalhar mais", "nao consigo trabalhar mais",
            "problema na coluna", "problema de coluna",
            "hérnia de disco", "hernia de disco", "hérnia na coluna",
            "coluna travou", "coluna ruim",
            "tive um derrame",
            "derrame",
            "artrose",
            "reumatismo", "reumatismo",
            "fazia bico", "trabalhava de bico",
            "sem carteira", "sem registro", "nunca assinei carteira",
            "tendinite", "bursite",
        ],
    },
    "idade_rural": {
        # Judicial primeiro (mais específico) para evitar falsos positivos com 'adm'
        "judicial": [
            "ação rural", "ação de aposentadoria rural",
            "judicial rural", "processo rural",
        ],
        "adm": [
            "aposentadoria rural", "aposentadoria por idade rural",
            "aposentadoria por idade", "aposentadoria",
            "aposentar", "aposenta", "me aposentar",
            "me aposentar rural", "quero me aposentar",
            "queria me aposentar", "minha aposentadoria",
            "negócio de aposentadoria",
            "trabalhador rural", "trabalhadora rural",
            "segurado especial", "zona rural", "agricultura",
            "agricultor", "agricultora", "roça", "lavrador", "campo",
            "assunto rural", "rural",
            # Gírias / escrita informal
            "rural", "roça", "rocinha", "sitia", "sítio", "sito",
            "fazenda", "fazendeiro", "fazendeira",
            "trabalho no mato", "trabalho na terra",
            "mexo com terra", "mexo com roça",
            "agricultura familiar", "pequeno agricultor",
            "trabalhador do campo", "trabalhadora do campo",
            "trabalhador rural", "trabalhadora rural",
            "coloninho", "colono", "coloninha",
            "aposentadoria do campo", "beneficio da roça",
            "aposentadoria rural", "aposentadoria no campo",
            "aposentar no campo",
            # Dialeto informal / rural
            "trabalhei na roça", "trabalhei na lavoura",
            "trabalhava na roça", "trabalhava na lavoura",
            "vida de agricultor", "vida de lavrador",
            "vida de colono", "vida de roceiro",
            "trabalhei com enxada", "trabalhava com enxada",
            "serviço braçal", "servico bracal",
            "cortador de cana", "cortava cana",
            "boia fria", "bóia fria", "boia-fria",
            "meeiro", "arrendatário", "arrendatario",
            "vaqueiro", "tratorista",
            "planta feijão", "planta feijao", "planta milho", "planta mandioca",
            "cria galinha", "cria porco", "cria vaca",
            "tira leite", "tirava leite", "ordenhava",
            "trabáio", "trabáia", "trabaiador",
            "roço pasto", "rocava pasto",
            "capinava", "capinar", "capinei",
            "toco de terra", "pedaço de terra", "pedaco de terra",
            "sítio pequeno", "sito pequeno", "roça pequena",
            "trabalhador volante", "trabalhadora volante",
            "trabalhava sem carteira", "nunca tive carteira",
            "trabalhei a vida toda", "trabalhei desde pequeno",
            "comecei a trabalhar com", "trabalho desde criança",
            "mexo com enxada", "lido com terra", "lido com roça",
            "trabalho na lavoura", "trabalho na plantação",
            "plantação", "plantacao", "colheita", "colhia",
        ],
    },
    "revisao": {
        "adm": [
            "revisão administrativa", "revisao administrativa",
            "recálculo administrativo", "recalculo administrativo",
        ],
        "judicial": [
            "revisão", "revisao", "aumentar benefício",
            "benefício errado", "cálculo errado", "recálculo",
            "revisional", "ação revisional",
        ],
    },
    "pensao": {
        "adm": [
            "pensão por morte", "pensão morte", "pensao por morte",
            "viuva", "viúva", "dependente", "óbito", "obito",
            "faleceu", "morreu",
            # Gírias / escrita informal
            "morrer", "faleceu", "faleci", "perdi meu",
            "perdi minha", "marido morreu", "esposa morreu",
            "pai morreu", "mãe morreu", "mae morreu",
            "viuvei", "viveu", "enviuvou",
            "pensão alimentícia", "pensao alimentícia",
            "deixou pensão", "deixou pensao",
            "pensão do falecido", "pensão do marido",
            "pensão por morte", "pensão por óbito",
            "pensao por obito",
            # Dialeto informal / rural
            "morreu meu marido", "meu marido morreu",
            "perdi meu marido", "perdi minha esposa",
            "perdi meu companheiro", "perdi meu convivente",
            "pai dos meus filhos morreu",
            "companheiro morreu", "convivente morreu",
            "marido faleceu", "esposo faleceu", "esposa faleceu",
            "meu velho morreu", "minha velha morreu",
            "faleceu meu", "perdi meu pai", "perdi minha mãe",
            "perdi meu filho", "morreu meu filho",
            "deixou viúva", "deixou viuva", "deixou pensão",
            "deixou pensao", "viúva", "viuva", "viuvou",
        ],
        "judicial": [
            "pensão judicial", "pensão ação", "ação de pensão",
        ],
    },
}


def classificar(texto_cliente: str) -> dict:
    """Classifica o benefício com base no texto do cliente.

    Duas etapas:
      1. Correspondência exata por substring (confiança 0.85)
      2. Correspondência difusa por similaridade de palavras (confiança 0.60)

    Args:
        texto_cliente: descrição em linguagem natural do que precisa.

    Returns:
        dict com tipo, esfera, sub_tipo, docs_necessarios, confianca.
    """
    texto = normalizar_texto(texto_cliente).lower()

    # ── 1. Correspondência exata por substring (normalizando ambos os lados) ──
    for tipo, esferas in PALAVRAS_CHAVE.items():
        for esfera, palavras in esferas.items():
            for palavra in palavras:
                palavra_norm = remover_acentos(palavra.lower())
                if palavra_norm in texto:
                    docs = BENEFICIOS[tipo]["documentos"].get(esfera, [])
                    return {
                        "tipo": tipo,
                        "esfera": esfera,
                        "sub_tipo": None,
                        "docs_necessarios": docs,
                        "confianca": 0.85,
                    }

    # ── 2. Correspondência difusa (fuzzy) ──
    palavras_user = [w for w in texto.split() if w not in _PALAVRAS_IGNORAR]
    if not palavras_user:
        palavras_user = texto.split()  # fallback: usa todas

    palavras_user = [remover_acentos(w) for w in palavras_user]

    melhor_tipo = None
    melhor_esfera = None
    melhor_score = 0.0

    for tipo, esferas in PALAVRAS_CHAVE.items():
        for esfera, palavras in esferas.items():
            for keyword in palavras:
                keyword_tokens = [
                    remover_acentos(w) for w in keyword.lower().split()
                    if w not in _PALAVRAS_IGNORAR
                ]
                if not keyword_tokens:
                    keyword_tokens = [remover_acentos(w) for w in keyword.lower().split()]

                # Quantos tokens da keyword têm correspondência no texto do usuário?
                matches = 0
                for kt in keyword_tokens:
                    for ut in palavras_user:
                        if SequenceMatcher(None, kt, ut).ratio() >= 0.70:
                            matches += 1
                            break

                score = matches / len(keyword_tokens) if keyword_tokens else 0
                if score > melhor_score and score >= 0.4:
                    melhor_score = score
                    melhor_tipo = tipo
                    melhor_esfera = esfera

    if melhor_tipo and melhor_score >= 0.4:
        docs = BENEFICIOS[melhor_tipo]["documentos"].get(melhor_esfera, [])
        return {
            "tipo": melhor_tipo,
            "esfera": melhor_esfera,
            "sub_tipo": None,
            "docs_necessarios": docs,
            "confianca": 0.6,
        }

    # ── 3. Fallback ──
    return {
        "tipo": "outro",
        "esfera": "adm",
        "sub_tipo": None,
        "docs_necessarios": BENEFICIOS["outro"]["documentos"]["adm"],
        "confianca": 0.4,
    }
