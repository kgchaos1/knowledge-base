from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from app.core.config import settings
from app.core.auth import install_auth

logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


def create_app(config=None, *, initialize_database=True):
    """Build an app; tests may disable startup DDL and override DB dependencies."""
    config = settings if config is None else config
    from app.api import notes, ai, import_tasks, sources, attachments, backups, sync, devices, file_import, operations, bookmark_import
    from app import models_membership  # Register additive source-note associations before DDL.

    @asynccontextmanager
    async def lifespan(application):
        if initialize_database:
            from app.core.database import engine, Base
            Base.metadata.create_all(bind=engine)
            from app.core.migrations import migrate_schema
            migrate_schema(engine)
        import asyncio
        from app.core.backup_service import scheduler
        task = asyncio.create_task(scheduler()) if initialize_database else None
        from app.core.source_scheduler import SourceScheduler
        sources_schedule = SourceScheduler() if initialize_database else None
        if sources_schedule:
            await sources_schedule.start()
        try:
            yield
        finally:
            if sources_schedule:
                await sources_schedule.stop()
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    # Authentication never exposes debug tracebacks, including in DEBUG mode.
    application = FastAPI(title=config.APP_NAME, version=config.APP_VERSION,
                          debug=False, lifespan=lifespan)
    # Same-origin frontend only. No permissive credentialed CORS middleware.
    install_auth(application, config)
    for router in (notes.router, ai.router, import_tasks.router, sources.router, attachments.router, backups.router, sync.router, devices.router, file_import.router, operations.router, bookmark_import.router):
        application.include_router(router, prefix="/api")

    @application.get("/")
    async def root():
        return {"status": "running"}

    @application.get("/api/health")
    async def health_check():
        return {"status": "healthy"}

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT,
                reload=settings.DEBUG, proxy_headers=False)
