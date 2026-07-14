"""Script para pré-adicionar clientes à tabela attended_clients.

Uso:
    python -m src.scripts.pre_add_attended 556181198609 5511999999999
"""
import asyncio
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from src.services.attended_clients import mark_attended


async def main():
    args = sys.argv[1:]
    if not args:
        print("Uso: python -m src.scripts.pre_add_attended 556181198609 5511999999999")
        sys.exit(1)

    for numero in args:
        await mark_attended(numero)
        print(f"  Adicionado: {numero}")


if __name__ == "__main__":
    asyncio.run(main())
