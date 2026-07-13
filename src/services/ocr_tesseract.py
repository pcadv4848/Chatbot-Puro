"""Serviço de OCR via Tesseract (local, gratuito).

Primário do pipeline OCR: Tesseract -> LLM (estruturação) -> Regex (fallback final).
Processa imagens localmente sem chamadas de rede (Tesseract).
Requer: tesseract-ocr + tesseract-ocr-por instalados no sistema.
"""
import logging
import subprocess
from io import BytesIO
from typing import Optional

logger = logging.getLogger(__name__)


def tesseract_disponivel() -> bool:
    """Verifica se o binário tesseract está instalado no sistema."""
    try:
        subprocess.run(
            ["tesseract", "--version"],
            capture_output=True, timeout=5, check=False,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _lingua_por_disponivel() -> bool:
    """Verifica se o pacote de idioma português está instalado."""
    try:
        result = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return "por" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def extrair_texto_tesseract(imagem_bytes: bytes) -> str:
    """Extrai texto de uma imagem usando Tesseract OCR local.

    Aplica pré-processamento básico (escala de cinza + threshold)
    para melhorar a taxa de reconhecimento.

    Args:
        imagem_bytes: conteúdo binário da imagem (JPEG, PNG, etc.)

    Returns:
        Texto extraído, ou string vazia se falhar.
    """
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        import pytesseract
    except ImportError:
        logger.warning("pytesseract ou Pillow não instalados")
        return ""

    if not tesseract_disponivel():
        logger.warning("Tesseract OCR não encontrado no sistema")
        return ""

    lingua = "por" if _lingua_por_disponivel() else "eng"
    if lingua == "eng":
        logger.info("Idioma 'por' não encontrado, usando 'eng' como fallback")

    try:
        imagem = Image.open(BytesIO(imagem_bytes))

        if imagem.mode != "L":
            imagem = imagem.convert("L")

        enhancer = ImageEnhance.Contrast(imagem)
        imagem = enhancer.enhance(2.0)

        imagem = imagem.filter(ImageFilter.SHARPEN)

        config = (
            "--oem 3 --psm 6 "
            "-c tessedit_char_whitelist="
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
            "ÁÀÃÂÄÉÈÊËÍÌÎÏÓÒÕÔÖÚÙÛÜÇáàãâäéèêëíìîïóòõôöúùûüç"
            "0123456789./-:,() "
        )

        texto = pytesseract.image_to_string(imagem, lang=lingua, config=config)
        return texto.strip()

    except Exception as e:
        logger.error("Falha ao processar imagem com Tesseract: %s", e)
        return ""


def extrair_texto_sync(imagem_bytes: bytes) -> str:
    """Wrapper síncrono.

    Chamada pelo pipeline OCR: Tesseract -> LLM -> Regex.
    """
    return extrair_texto_tesseract(imagem_bytes)
