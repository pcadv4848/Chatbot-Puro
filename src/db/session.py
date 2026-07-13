"""Gerenciamento de sessão assíncrona do banco (SQLAlchemy + SQLite/PostgreSQL).

Cria o engine e a fábrica de sessões com base nas configurações.
- PostgreSQL: pool configurado para produção (tamanho, overflow, tempo de espera)
- SQLite: check_same_thread=False + WAL mode + foreign keys + busy timeout
- Diretório do arquivo SQLite é criado automaticamente se não existir
"""
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from src.config import settings


def _criar_engine_sqlite(url: str, echo: bool):
    """Cria engine SQLite com otimizações e garante que o diretório exista."""
    db_path = url.removeprefix("sqlite+aiosqlite:///")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        url,
        echo=echo,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _config_sqlite(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def _criar_engine_postgres(url: str, echo: bool):
    """Cria engine PostgreSQL com pool configurado para produção."""
    return create_async_engine(
        url,
        echo=echo,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


if settings.database_url.startswith("sqlite"):
    engine = _criar_engine_sqlite(settings.database_url, settings.debug)
else:
    engine = _criar_engine_postgres(settings.database_url, settings.debug)

async_session = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provider de sessão do banco para uso com FastAPI dependency injection."""
    async with async_session() as session:
        yield session
