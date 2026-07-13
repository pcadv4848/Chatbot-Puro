"""Transcrição de áudio via faster-whisper (open-source, offline)."""

import asyncio
import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

try:
    from faster_whisper import WhisperModel

    _DISPONIVEL = True
except ImportError:
    _DISPONIVEL = False

logger = logging.getLogger(__name__)

_modelo: Optional["WhisperModel"] = None
_MODELO_NOME: str = os.getenv("WHISPER_MODEL", "tiny")


def disponivel() -> bool:
    return _DISPONIVEL


def _obter_modelo() -> Optional["WhisperModel"]:
    global _modelo
    if not _DISPONIVEL:
        return None
    if _modelo is None:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        try:
            _modelo = WhisperModel(_MODELO_NOME, device=device, compute_type=compute_type)
            logger.info("Whisper carregado: %s (%s, %s)", _MODELO_NOME, device, compute_type)
        except Exception as e:
            logger.warning("Falha ao carregar Whisper: %s", e)
            return None
    return _modelo


def transcrever_audio(dados_audio: bytes, idioma: str = "pt") -> Optional[str]:
    """Transcreve áudio usando faster-whisper (chamada síncrona).

    Args:
        dados_audio: Conteúdo binário do .ogg ou outro formato.
        idioma: Código do idioma esperado (padrão 'pt').

    Returns:
        Texto transcrito ou None se falhar.
    """
    modelo = _obter_modelo()
    if modelo is None:
        return None

    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(dados_audio)
            caminho = tmp.name

        try:
            segmentos, _ = modelo.transcribe(caminho, language=idioma, beam_size=5)
            texto = " ".join(seg.text for seg in segmentos).strip()
            return texto if texto else None
        finally:
            Path(caminho).unlink(missing_ok=True)
    except Exception as e:
        logger.warning("Erro na transcrição: %s", e)
        return None


async def transcrever_audio_async(dados_audio: bytes, idioma: str = "pt") -> Optional[str]:
    """Versão assíncrona que executa a transcrição em thread separada."""
    return await asyncio.to_thread(transcrever_audio, dados_audio, idioma)
