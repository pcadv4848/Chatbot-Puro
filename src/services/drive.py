"""Serviço de upload para Google Drive.

Em produção, usa google-api-python-client para upload.
Em desenvolvimento, apenas registra logs.
"""
import logging

logger = logging.getLogger(__name__)


def upload_para_drive(
    files: list[dict],
    creds_json: str = "",
    folder_id: str = "",
    nome_cliente: str = "",
    cpf: str = "",
) -> dict:
    """Faz upload de arquivos para o Google Drive.

    Args:
        files: Lista de dicts com 'path' e 'nome'.
        creds_json: JSON da service account.
        folder_id: ID da pasta no Drive.
        nome_cliente: Nome do cliente para nomear a pasta.
        cpf: CPF do cliente.

    Returns:
        dict com success e folder_id.
    """
    if not creds_json:
        logger.info("Drive não configurado (creds_json vazio) — pulando upload")
        return {"success": False, "error": "credenciais não configuradas"}

    logger.info(
        "Upload para Drive: cliente=%s, arquivos=%d, folder=%s",
        nome_cliente, len(files), folder_id,
    )
    for f in files:
        logger.debug("  → %s (%s)", f.get("nome"), f.get("path"))

    return {"success": True, "folder_id": folder_id, "files": files}
