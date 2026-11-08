"""Single-worker scheduler with durable settings and verified, bounded retention."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import uuid
from app.core.config import settings
from app.core.backup_archive import create_archive, verify_archive

logger = logging.getLogger(__name__)
attachment_lock = asyncio.Lock()
job_lock = asyncio.Lock()
CST = timezone(timedelta(hours=8))
ROOT = Path(settings.BACKUP_LOCAL_PATH) / 'archives'
CONFIG = ROOT / 'schedule.json'
STATE = {'running': False, 'last_error': None}


def schedule_settings():
    value = {'enabled': settings.BACKUP_ENABLED, 'hour': 2, 'keep': 7, 'last_success': None}
    if CONFIG.exists():
        value.update(json.loads(CONFIG.read_text(encoding='utf-8')))
    now = datetime.now(CST)
    due = now.replace(hour=value['hour'], minute=0, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    value['next_due'] = due.isoformat() if value['enabled'] else None
    return value


def save_settings(changes):
    value = schedule_settings()
    value.pop('next_due', None)
    value.update(changes)
    ROOT.mkdir(parents=True, exist_ok=True)
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps(value), encoding='utf-8')
    temp.replace(CONFIG)
    return schedule_settings()


def list_backups():
    result = []
    if ROOT.exists():
        for path in ROOT.glob('*.metadata.json'):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if (ROOT / data['filename']).is_file():
                    result.append(data)
            except (ValueError, OSError, KeyError):
                logger.warning('Invalid backup metadata skipped')
    return sorted(result, key=lambda b: b['created_at'], reverse=True)


def lookup(backup_id):
    if not isinstance(backup_id, str) or len(backup_id) != 32:
        return None
    return next((b for b in list_backups() if b['id'] == backup_id), None)


def db_path():
    from app.core.database import engine
    if engine.url.get_backend_name() != 'sqlite' or not engine.url.database:
        raise ValueError('Only file-backed SQLite backup is supported')
    return Path(engine.url.database)


async def make_backup():
    if job_lock.locked():
        return
    # Drain the whole publish/metadata/retention operation on shutdown, not only
    # the archive thread. A completed ZIP must never become an invisible orphan.
    operation = asyncio.create_task(_make_backup_complete())
    try:
        await asyncio.shield(operation)
    except asyncio.CancelledError:
        await operation
        raise


async def _make_backup_complete():
    async with job_lock:
        STATE.update(running=True, last_error=None)
        try:
            async with attachment_lock:
                data = await asyncio.to_thread(create_archive, db_path(), Path('/app/data/attachments'), ROOT)
            path = Path(data['path'])
            verified = await asyncio.to_thread(verify_archive, path)
            identifier = uuid.uuid4().hex
            public = {'id': identifier, 'filename': path.name, 'size': path.stat().st_size,
                      'sha256': data['sha256'], 'created_at': datetime.now(timezone.utc).isoformat(),
                      'status': 'completed', 'verified': True, 'notes_count': data['notes_count']}
            metadata = ROOT / (identifier+'.metadata.json')
            temporary = metadata.with_suffix('.tmp')
            temporary.write_text(json.dumps(public), encoding='utf-8')
            temporary.replace(metadata)
            save_settings({'last_success': public['created_at']})
            # Only our own verified archives are subject to retention, never legacy snapshots.
            for old in list_backups()[schedule_settings()['keep']:]:
                target = ROOT / old['filename']
                if target.parent.resolve() != ROOT.resolve() or target.is_symlink():
                    continue
                target.unlink(missing_ok=True)
                (ROOT / (old['id']+'.metadata.json')).unlink(missing_ok=True)
        except Exception as exc:
            STATE['last_error'] = '备份未完成，请检查磁盘空间和附件完整性。'
            logger.warning('Backup failed (%s)', type(exc).__name__)
        finally:
            STATE['running'] = False


async def scheduler():
    while True:
        try:
            config = schedule_settings()
            now = datetime.now(CST)
            due = now.replace(hour=config['hour'],minute=0,second=0,microsecond=0)
            last = datetime.fromisoformat(config['last_success']).astimezone(CST) if config['last_success'] else None
            if config['enabled'] and now >= due and (not last or last < due):
                await make_backup()
        except Exception as exc:
            logger.warning('Backup scheduler error (%s)', type(exc).__name__)
        # Wake at the configured hour; failed attempts retry no more than hourly.
        config = schedule_settings()
        until_due = (datetime.fromisoformat(config['next_due']) - datetime.now(CST)).total_seconds() if config['next_due'] else 3600
        await asyncio.sleep(max(30, min(3600, until_due)))
