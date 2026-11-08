"""Single-process source scheduling; parent lifespan must start/stop exactly once.

This is deliberately NOT a distributed scheduler. Run the app with ONE worker.
Call start only during startup, before serving requests (recovery assumes no live
workers). stop drains imports instead of cancelling threads or unlocking sources
whose importer is still running. Existing manual background imports are owned by
Starlette, not this scheduler. No network/AI work runs on the asyncio loop.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import or_

from app.api.sources import claim_source_sync, run_import_task
from app.core.database import SessionLocal
from app.models.note import DataSource, ImportTask

logger = logging.getLogger(__name__)
ACTIVE = ("pending", "running")


def recover_interrupted_tasks(session_factory=SessionLocal):
    """Startup-only recovery, safe ONLY before this single worker accepts work."""
    with session_factory() as db:
        count = db.query(ImportTask).filter(ImportTask.status.in_(ACTIVE)).update({
            "status": "failed",
            "error_message": "Import interrupted by server restart; retry manually or wait for the next scheduled sync",
        }, synchronize_session=False)
        # Also remove dangling/stale claims; preserve last_task_id and all history.
        db.query(DataSource).filter(DataSource.active_task_id.is_not(None)).update(
            {"active_task_id": None}, synchronize_session=False
        )
        db.commit()
        return count


class SourceScheduler:
    def __init__(self, session_factory=SessionLocal, runner=None, *, poll_seconds=60):
        self.session_factory = session_factory
        self.runner = runner or run_import_task
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._loop_task = None
        self._tick_lock = asyncio.Lock()

    async def start(self):
        """Recover once, then poll immediately and every 60 seconds by default."""
        if self._loop_task is not None:
            raise RuntimeError("SourceScheduler is already started")
        await asyncio.to_thread(recover_interrupted_tasks, self.session_factory)
        self._stop.clear()
        self._loop_task = asyncio.create_task(self._run(), name="source-scheduler")

    async def stop(self):
        """Stop new claims and wait for owned threads; never cancel an importer."""
        self._stop.set()
        task = self._loop_task
        if task is not None:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # to_thread cancellation does not stop its thread. Keep claims
                # until execution actually finishes even if lifespan is cancelled.
                await task
                raise
            finally:
                self._loop_task = None

    def _claim_due(self, now):
        with self.session_factory() as db:
            ids = [row[0] for row in db.query(DataSource.id).filter(
                DataSource.sync_enabled.is_(True),
                DataSource.active_task_id.is_(None),
                or_(DataSource.next_sync_at.is_(None), DataSource.next_sync_at <= now),
            ).all()]
        claimed = []
        for source_id in ids:
            if self._stop.is_set():
                break
            try:
                with self.session_factory() as db:
                    task = claim_source_sync(db, source_id, scheduled_at=now)
                    if task is not None:
                        claimed.append(task.id)
            except Exception as exc:
                # Do not discard earlier durable claims if a later source fails.
                logger.warning("Source %s claim failed (%s)", source_id, type(exc).__name__)
        return claimed

    def _execute(self, task_id):
        # run_import_task owns an independent Session and its own asyncio loop.
        try:
            self.runner(task_id)
        except Exception as exc:
            logger.warning("Scheduled import %s crashed (%s)", task_id, type(exc).__name__)
        # A runner failure outside its internal exception handler (or an early
        # return) must remain visible, never leave a permanent active lock. This
        # executes only AFTER the synchronous runner/thread has returned.
        with self.session_factory() as db:
            task = db.get(ImportTask, task_id)
            if task is not None and task.status in ACTIVE:
                task.status = "failed"
                task.error_message = "Scheduled import did not finish; check server configuration and source availability"
            db.query(DataSource).filter(DataSource.active_task_id == task_id).update(
                {"active_task_id": None}, synchronize_session=False
            )
            db.commit()

    async def run_once(self, now=None):
        """One testable tick. All database and importer work is off-loop."""
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Schedule time must be timezone-aware UTC")
        now = now.astimezone(timezone.utc)
        async with self._tick_lock:
            ids = await asyncio.to_thread(self._claim_due, now)
        if ids:
            # No cancellation on normal shutdown: _run is drained by stop.
            outcomes = await asyncio.gather(
                *(asyncio.to_thread(self._execute, task_id) for task_id in ids),
                return_exceptions=True,
            )
            # Drain every thread even if persisting one outcome fails. Its
            # durable pending/running state is recovered on next startup.
            for task_id, outcome in zip(ids, outcomes):
                if isinstance(outcome, BaseException):
                    logger.error("Scheduled import %s outcome persistence failed (%s)", task_id, type(outcome).__name__)
        return ids

    async def _run(self):
        batches = set()

        def finished(batch):
            batches.discard(batch)
            if not batch.cancelled() and batch.exception() is not None:
                logger.warning("Source scheduler tick failed (%s)", type(batch.exception()).__name__)

        try:
            while not self._stop.is_set():
                # A slow source must not postpone polling other sources. The
                # database claim protects it from later ticks of this loop.
                batch = asyncio.create_task(self.run_once(), name="source-scheduler-tick")
                batches.add(batch)
                batch.add_done_callback(finished)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            # Preserve every in-flight thread and source lock until actual exit.
            if batches:
                await asyncio.gather(*batches, return_exceptions=True)
