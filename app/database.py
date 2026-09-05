from collections.abc import AsyncGenerator
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

engine_options = {
    "echo": settings.debug,
    "pool_pre_ping": True,
    "pool_recycle": 3600,
}

if settings.database_url_port:
    # Supabase's transaction pooler is PgBouncer-backed.  NullPool avoids
    # holding server connections and unique names prevent cross-connection
    # prepared-statement collisions in PgBouncer.
    engine_options.update(
        poolclass=NullPool,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        },
    )
else:
    engine_options.update(pool_size=5, max_overflow=5)

engine = create_async_engine(settings.async_database_url, **engine_options)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
