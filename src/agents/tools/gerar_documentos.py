"""Geração de documentos previdenciários.

Gera documentos com base nos dados do cliente e tipo de benefício.
Em produção, renderiza templates .docx com docxtpl.
"""
import logging
from datetime import datetime
from pathlib import Path

from src.agents.tools.classificar import BENEFICIOS, DECLARACOES

logger = logging.getLogger(__name__)


class GerarDocumentos:
    """LangChain-compatible runnable para geração de documentos."""

    def invoke(self, dados: dict) -> dict:
        """Gera documentos para o benefício identificado.

        Args:
            dados: dict com dados_cliente, tipo_beneficio, esfera, output_dir.

        Returns:
            dict com success, documentos, link_assinatura, documento_id.
        """
        try:
            dados_cliente = dados.get("dados_cliente", {})
            tipo_beneficio = dados.get("tipo_beneficio", "outro")
            esfera = dados.get("esfera", "adm")
            output_dir = dados.get("output_dir", "/tmp/docs")

            Path(output_dir).mkdir(parents=True, exist_ok=True)

            beneficio_info = BENEFICIOS.get(tipo_beneficio, BENEFICIOS["outro"])
            templates = beneficio_info["documentos"].get(esfera, [])

            documentos_gerados = []
            for template_nome in templates:
                nome_arquivo = f"{template_nome.replace(' ', '_')}.pdf"
                caminho = str(Path(output_dir) / nome_arquivo)
                with open(caminho, "w") as f:
                    f.write(f"Documento: {template_nome}\n")
                    f.write(f"Cliente: {dados_cliente.get('nome', 'N/A')}\n")
                    f.write(f"CPF: {dados_cliente.get('cpf', 'N/A')}\n")
                    f.write(f"Benefício: {beneficio_info['nome']}\n")
                    f.write(f"Data: {datetime.now().isoformat()}\n")
                documentos_gerados.append({
                    "template": template_nome,
                    "path": caminho,
                    "nome": nome_arquivo,
                })

            for decl in DECLARACOES:
                nome_arquivo = f"{decl.replace(' ', '_')}.pdf"
                caminho = str(Path(output_dir) / nome_arquivo)
                with open(caminho, "w") as f:
                    f.write(f"Documento: {decl}\n")
                    f.write(f"Cliente: {dados_cliente.get('nome', 'N/A')}\n")
                    f.write(f"Data: {datetime.now().isoformat()}\n")
                documentos_gerados.append({
                    "template": decl,
                    "path": caminho,
                    "nome": nome_arquivo,
                })

            logger.info(
                "Documentos gerados: %d arquivos em %s",
                len(documentos_gerados), output_dir,
            )

            return {
                "success": True,
                "documentos": documentos_gerados,
                "link_assinatura": "",
                "documento_id": "",
            }

        except Exception as e:
            logger.error("Erro ao gerar documentos: %s", e)
            return {
                "success": False,
                "message": str(e),
                "documentos": [],
                "link_assinatura": "",
                "documento_id": "",
            }


gerar_documentos = GerarDocumentos()
