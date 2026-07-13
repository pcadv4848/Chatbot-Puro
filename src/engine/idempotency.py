"""Cache de idempotência em memória para evitar operações duplicadas.

Uso:
    cache = IdempotencyCache()

    # Antes de uma operação não-idempotente:
    if not cache.check_and_set("envio_msg_123"):
        logger.warning("Operação já processada, ignorando duplicata")
        return cache.get("envio_msg_123")

    resultado = fazer_operacao()
    cache.set("envio_msg_123", resultado)
"""
import logging
import threading
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)


class IdempotencyCache:
    """Cache thread-safe com TTL para chaves de idempotência.

    Armazena resultados de operações já concluídas para que,
    em caso de repetição da mesma chave, o resultado anterior
    seja retornado sem executar a operação novamente.
    """

    def __init__(self, ttl: int = 3600, max_size: int = 10000):
        self._ttl = ttl
        self._max_size = max_size
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def check_and_set(self, key: str) -> bool:
        """Se key não existir ou estiver expirada, marca como pendente e retorna True.

        Se key já existir e não expirou, retorna False (duplicata).
        """
        now = time.time()
        with self._lock:
            existing = self._data.get(key)
            if existing is not None and existing[0] > now:
                return False
            self._data[key] = (now + self._ttl, _PENDING)
            self._evict_if_needed()
            return True

    def set(self, key: str, value: Any) -> None:
        """Armazena o resultado de uma operação concluída."""
        now = time.time()
        with self._lock:
            self._data[key] = (now + self._ttl, value)
            self._evict_if_needed()

    def get(self, key: str) -> Optional[Any]:
        """Retorna o resultado armazenado, ou None se não existir/expirado/pendente."""
        now = time.time()
        with self._lock:
            existing = self._data.get(key)
            if existing is None or existing[0] <= now:
                return None
            if existing[1] is _SENTINEL:
                return None
            return existing[1]

    def exists(self, key: str) -> bool:
        """Verifica se a chave existe e não expirou."""
        now = time.time()
        with self._lock:
            existing = self._data.get(key)
            return existing is not None and existing[0] > now

    def remove(self, key: str) -> None:
        """Remove uma chave do cache."""
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        """Limpa todo o cache."""
        with self._lock:
            self._data.clear()

    def _evict_if_needed(self) -> None:
        if len(self._data) > self._max_size:
            now = time.time()
            expired = [k for k, (exp, _) in self._data.items() if exp <= now]
            for k in expired:
                del self._data[k]
            if len(self._data) > self._max_size:
                sorted_keys = sorted(self._data.keys(), key=lambda k: self._data[k][0])
                excess = len(self._data) - self._max_size
                for k in sorted_keys[:excess]:
                    del self._data[k]

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


_SENTINEL = object()
_PENDING = _SENTINEL


def gerar_chave_idempotencia(*partes: str) -> str:
    """Gera uma chave de idempotência a partir de partes.

    Usa um hash SHA256 das partes para garantir chaves únicas
    e de tamanho previsível.
    """
    import hashlib
    raw = "|".join(partes)
    return hashlib.sha256(raw.encode()).hexdigest()


def gerar_id_mensagem() -> str:
    """Gera um ID único para mensagens enviadas (msg_id)."""
    return str(uuid.uuid4())


cache = IdempotencyCache()
