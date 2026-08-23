from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from core.config import settings


connect_args = {}
engine_options = {
    "pool_pre_ping": True,
}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {
        "check_same_thread": False,
        "timeout": settings.DB_POOL_TIMEOUT_SECONDS,
    }
else:
    # Les requêtes IA restent ouvertes longtemps. Le pool doit pouvoir servir
    # au moins 20 utilisateurs sans que cinq connexions par défaut ne forcent
    # une sérialisation artificielle de toutes les sessions.
    engine_options.update(
        {
            "pool_size": max(5, settings.DB_POOL_SIZE),
            "max_overflow": max(0, settings.DB_MAX_OVERFLOW),
            "pool_timeout": max(1, settings.DB_POOL_TIMEOUT_SECONDS),
            "pool_recycle": max(60, settings.DB_POOL_RECYCLE_SECONDS),
            "pool_use_lifo": True,
        }
    )

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    **engine_options,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def database_pool_status() -> dict[str, int | str]:
    """Expose uniquement les compteurs techniques, jamais l'URL de connexion."""
    pool = engine.pool
    payload: dict[str, int | str] = {
        "backend": pool.__class__.__name__,
        "status": pool.status(),
    }
    for name, reader in (
        ("pool_size", getattr(pool, "size", None)),
        ("checked_out", getattr(pool, "checkedout", None)),
        ("overflow", getattr(pool, "overflow", None)),
    ):
        if callable(reader):
            try:
                value = int(reader())
                payload[name] = max(0, value) if name == "overflow" else value
            except Exception:
                continue
    if not settings.DATABASE_URL.startswith("sqlite"):
        payload["max_overflow"] = max(0, settings.DB_MAX_OVERFLOW)
    return payload
