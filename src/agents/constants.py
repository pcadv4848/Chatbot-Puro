"""Constantes do agente: perguntas, padrões, mapeamentos e mensagens fixas.

Extraído de supervisor.py para reduzir acoplamento e facilitar manutenção.
"""
import re


from src.conversation.state import SessionStatus
from src.agents.tools.validar import validar_cpf_formatado, validar_rg, validar_cep, validar_telefone, validar_email
# ── Perguntas para cada campo ──
PERGUNTAS_CAMPOS: dict[str, str] = {
    "nome": "Qual seu nome completo?",
    "cpf": "Qual seu CPF? (ex: 123.456.789-00)",
    "rg": "Qual seu RG? (ex: 12.345.678-9)",
    "logradouro": "Qual seu endereço (rua e número)? (ex: Rua das Flores, 123)",
    "numero": "Qual o número da sua casa? (ex: 123)",
    "bairro": "Qual seu bairro? (ex: Centro)",
    "cidade": "Qual sua cidade? (ex: São Paulo)",
    "uf": "Qual seu estado (UF)? (ex: SP)",
    "cep": "Qual seu CEP? (ex: 01234-567)",
    "data_nascimento": "Qual sua data de nascimento? (ex: 20/10/2001)",
    "nacionalidade": "Qual sua nacionalidade? (ex: Brasileira)",
    "telefone": "Qual seu telefone de contato? (ex: 11988887777)",
    "email": "Qual seu e-mail? (ex: joao@email.com)",
}

# ── Perguntas simplificadas (para usuarios com dificuldade) ──
PERGUNTAS_SIMPLES: dict[str, str] = {
    "nome": "Qual o seu nome?",
    "cpf": "Qual o numero do seu CPF? (pode ser so os numeros)",
    "rg": "Qual o numero do seu RG?",
    "logradouro": "Onde voce mora? Qual rua e numero?",
    "numero": "Qual o numero da sua casa?",
    "bairro": "Qual o nome do seu bairro?",
    "cidade": "Qual cidade voce mora?",
    "uf": "Qual estado? (ex: SP, BA, MG)",
    "cep": "Qual o CEP da sua rua?",
    "data_nascimento": "Qual a data que voce nasceu? (ex: 20/10/2001)",
    "nacionalidade": "Qual sua nacionalidade? (ex: Brasileira)",
    "telefone": "Qual seu telefone para contato?",
    "email": "Qual seu email?",
}

# ── Mapeamento de meses por extenso → número ──
MESES_PT: dict[str, str] = {
    "janeiro": "01", "fevereiro": "02", "março": "03", "abril": "04",
    "maio": "05", "junho": "06", "julho": "07", "agosto": "08",
    "setembro": "09", "outubro": "10", "novembro": "11", "dezembro": "12",
}

# ── Mapeamento de estados por extenso → sigla ──
UF_MAP: dict[str, str] = {
    "acre": "AC", "alagoas": "AL", "amapá": "AP", "amazonas": "AM",
    "bahia": "BA", "ceará": "CE", "distrito federal": "DF",
    "espírito santo": "ES", "goiás": "GO", "maranhão": "MA",
    "mato grosso": "MT", "mato grosso do sul": "MS",
    "minas gerais": "MG", "pará": "PA", "paraíba": "PB",
    "paraná": "PR", "pernambuco": "PE", "piauí": "PI",
    "rio de janeiro": "RJ", "rio grande do norte": "RN",
    "rio grande do sul": "RS", "rondônia": "RO", "roraima": "RR",
    "santa catarina": "SC", "são paulo": "SP", "sergipe": "SE",
    "tocantins": "TO",
}

# ── Padrões para extrair campos de texto livre ──
PADROES_CAMPO: dict[str, re.Pattern] = {
    "cpf": re.compile(
        r"(?:cpf|documento|c\.p\.f\.?)\s*(?::|é|−|–|—)?\s*([\d\.\-\s]{11,})", re.I
    ),
    "telefone": re.compile(
        r"(?:telefone|celular|whatsapp|tel|fone|whats|contato)\s*(?::|é)?\s*"
        r"([\d\s\(\)\+\-]{8,})", re.I
    ),
    "email": re.compile(
        r"(?:e[-]?mail|email)\s*(?::|é)?\s*([\w\.\-]+@[\w\.\-]+\.\w+)", re.I
    ),
    "cep": re.compile(
        r"(?:cep)\s*(?::|é)?\s*(\d[\d\-]{4,})", re.I
    ),
    "data_nascimento": re.compile(
        r"(?:(?:data\s*(?:de\s+)?)?nascimento|nasceu)\s*(?::|é|em)?\s*"
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        re.I,
    ),
    "rg": re.compile(
        r"(?:rg|identidade|r\.g\.?)\s*(?::|é)?\s*([\w\.\-]{4,})", re.I
    ),
    "estado_civil": re.compile(
        r"(?:estado civil)\s*(?::|é)?\s*(solteir[ao]|casado|divorciado|viúv[ao]|viuv[ao]|separado|união estável|uniao estavel)", re.I
    ),
    "profissao": re.compile(
        r"(?:profissão|profissao|trabalho\s+como|sou)\s*(?::|é)?\s*([a-zA-ZÀ-ÿ\s]{3,})", re.I
    ),
}

# ── Validação pré-armazenamento ──
VALIDAR_CAMPO: dict[str, callable] = {
    "cpf": lambda v: validar_cpf_formatado(v) if v else False,
    "rg": lambda v: validar_rg(v) if v else False,
    "cep": lambda v: validar_cep(v) if v else False,
    "telefone": lambda v: validar_telefone(v) if v else False,
    "email": lambda v: validar_email(v) if v else False,
}

PREFIXOS_NOME = re.compile(
    r"^(?:(?:meu\s+)?nome\s+(?:é|e|:)\s+|sou\s+|me\s+chamo\s+|"
    r"chamo-me\s+|chamad[ao]\s+|que\s+é\s+|diz\s+que\s+)",
    re.I,
)

PREFIXOS_RUA = ("r ", "r.", "rua ", "av ", "avenida ", "travessa ",
                 "praça ", "rodovia ", "estrada ", "alameda ", "via ")

NACIONALIDADES = ("brasileir", "portugu", "italian", "espanhol", "argentino",
                  "chileno", "peruano", "colombiano", "uruguaio", "paraguaio",
                  "brasil", "portugal", "italia", "espanha", "argentina",
                  "chile", "peru", "colombia", "uruguai", "paraguai",
                  "alemão", "alemã", "francês", "francesa", "inglês", "inglesa",
                  "japonês", "japonesa", "chinês", "chinesa", "coreano",
                  "mexicano", "canadense", "australiano", "sul-africano",
                  "holandês", "holandesa", "suíço", "suíça", "sueco", "sueca")

# ── Nomes de benefícios ──
BENEFICIO_NOME: dict[str, str] = {
    "incapacidade": "Benefício por Incapacidade",
    "idade_rural": "Aposentadoria por Idade Rural",
    "revisao": "Revisão de Benefício",
    "pensao": "Pensão por Morte",
    "outro": "Benefício",
}

# ── Constantes do fluxo de classificação ──
MAX_TENTATIVAS_CLASSIFICACAO = 15
MIN_STEPS_EARLY_CLASSIFY = 3
MIN_STEPS_PARA_CONCLUIR = 7
EARLY_CLASSIFY_CONFIDENCE = 0.7

# ── Mensagens do fluxo de tráfego pago ──
TRAFEGO_SAUDACAO = [
    "Aqui e da Advocacia Penido Castro. Como voce se chama?",
    "Penido Castro. Qual o seu nome?",
]

TRAFEGO_HISTORIA = [
    "Prazer, {nome}. Me conta um pouco sobre o que esta acontecendo.",
    "{nome}, me explique resumidamente qual e a situacao.",
]

TRAFEGO_FINALIZAR = [
    "Perfeito, {nome}. Ja entendi seu caso. Vou registrar e dar inicio ao seu atendimento.",
    "Entendi, {nome}. Deixa que eu cuido disso agora mesmo.",
]

# ── Sinais de dificuldade ──
SINAIS_DIFICULDADE = frozenset({
    "nao entendi", "nao intendi", "num entendi", "num intendi",
    "como e", "como é", "o que e", "o que é", "que e", "que é",
    "nao sei", "num sei", "não sei",
    "nao soube", "num soube",
    "difícil", "dificil", "dificiu",
    "complicado", "complicou",
    "não sei escrever", "nao sei escrever",
    "sou analfabeto", "sou analfabeta", "analfabeto", "analfabeta",
    "estou confuso", "to confuso", "tô confuso",
    "nao to entendendo", "nao tou entendendo", "nao to intendendo",
    "fala mais devagar", "fala mais simples",
    "explica de novo", "explica dnovo",
    "nao sei ler", "sou lerdo", "nao sou estudado",
})

# ── Mensagens fixas ──
MENSAGEM_NAO_ENTENDI = "Não entendi. Pode repetir? "

MENSAGEM_ERRO_IA = (
    "Desculpe, estou com dificuldades para processar sua mensagem agora."
    " Pode tentar novamente em alguns instantes?"
)

MENSAGEM_QUOTA_EXCEDIDA = (
    "Excedemos o limite de uso no momento."
    " Aguarde alguns instantes e tente novamente."
)

MENSAGEM_FORA_ESCOPO = (
    "Certo. Vou passar seu caso para um advogado da nossa equipe"
    " analisar pessoalmente e dar continuidade."
)

MENSAGEM_HUMANO = (
    "Perfeito. Seu caso foi registrado com sucesso."
    " Um advogado da nossa equipe vai preparar seus documentos."
)

MENSAGEM_HUMANO_DUVIDA = (
    "Essa é uma boa pergunta. Prefiro que um advogado da nossa equipe"
    " analise seu caso pessoalmente para te passar a informação correta."
)

SILENT = "__SILENT__"
"""Sentinel: processar retorna SILENT quando o bot deve processar sem responder."""


# ── Sinais de que a IA está em dúvida ou não tem certeza ──
SINAIS_INCERTEZA = frozenset({
    "não tenho certeza", "nao tenho certeza",
    "não sei", "nao sei",
    "não posso afirmar", "nao posso afirmar",
    "não posso confirmar", "nao posso confirmar",
    "não tenho essa informação", "nao tenho essa informacao",
    "não tenho como saber", "nao tenho como saber",
    "talvez", "pode ser que",
    "não consigo responder", "nao consigo responder",
    "não posso responder", "nao posso responder",
    "sugiro consultar", "consulte um",
    "é importante consultar",
    "aconselho consultar", "aconselho buscar",
    "recomendo consultar", "recomendo procurar",
    "não é possível afirmar", "nao e possivel afirmar",
    "não é possível confirmar", "nao e possivel confirmar",
    "isso requer", "seria necessário",
    "depende de", "depende do",
    "não posso dar essa informação", "nao posso dar essa informacao",
    "não posso fornecer", "nao posso fornecer",
    "busque orientação", "busque orientacao",
    "procure um advogado", "procure um profissional",
    "não tenho acesso", "nao tenho acesso",
})

# ── Perguntas progressivas para classificação (poucas para fluxo rápido) ──
PERGUNTAS_CLASSIFICACAO = [
    "me conte um pouco sobre o que esta acontecendo.",
    "ha quanto tempo voce esta nessa situacao?",
    "ja contribuiu para o INSS alguma vez?",
    "tem alguma questao de saude envolvida?",
    "gostaria de acrescentar mais alguma coisa?",
]

# ── Palavras de afirmação/negação ──
PALAVRAS_SIM = frozenset({
    "sim", "ss", "isso", "correto", "exato", "isso mesmo",
    "é isso", "pode ser", "ok", "tá", "ta", "si",
    "confirmo", "afirmativo", "yes", "y", "claro", "certamente",
    "com certeza", "verdade", "pode", "pode sim",
})

PALAVRAS_NAO = frozenset({
    "não", "nao", "não é",
    "errado", "negativo", "outro", "nada", "nenhum",
    "não isso", "não é isso", "tá errado", "nops",
})

SIM_NGRAMAS = frozenset({
    "pode sim", "isso mesmo", "é isso", "com certeza",
    "pode ser", "sim sim", "claro que sim", "isso ai",
    "isso aí", "ta certo", "tá certo", "verdade",
    "exato", "isso", "correto",
})

NAO_NGRAMAS = frozenset({
    "não é", "não isso", "não é isso", "tá errado",
    "não quero", "não mesmo", "não é bem",
})

PALAVRAS_NAO_AFIRMATIVAS = frozenset({
    "repetir", "explicar", "ajudar", "falar", "dizer",
    "mandar", "mostrar", "enviar", "esclarecer", "responder",
})

# ── Palavras de profissão ──
PROF_PALAVRAS = {"trabalho", "trabalhadora", "trabalhador", "sou",
                 "pedreiro", "professor", "professora", "advogado",
                 "advogada", "medico", "médico", "medica", "médica",
                 "enfermeiro", "enfermeira", "motorista", "pintor",
                 "pintora", "caminhoneiro", "costureira", "domestica",
                 "doméstica", "lavrador", "lavradora", "agricultor",
                 "agricultora", "comerciante", "vendedor", "vendedora",
                 "servente", "carpinteiro", "eletricista", "encanador",
                 "cozinheiro", "cozinheira", "zelador", "zeladora",
                 "porteiro", "porteira", "auxiliar", "servidor",
                 "funcionario", "funcionária", "funcionario publico",
                 "aposentado", "aposentada", "estudante"}

CIVIS = {"solteiro", "solteira", "casado", "casada", "divorciado",
         "divorciada", "viúvo", "viúva", "viuvo", "viuva",
         "separado", "separada"}
