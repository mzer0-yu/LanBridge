"""Read configuration without Store initialization, migrations, credentials or writes."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from .models import Settings
from .service import configured_sites


def read_status(root):
    database = Path(root) / 'state.sqlite'
    if not database.is_file():
        raise ValueError('本机配置尚未初始化，请先使用 serve 或 setup-admin')
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            db.execute('BEGIN')
            rows = db.execute("SELECT key,value FROM kv WHERE key IN ('settings','sites','cloudflare_status','pending_admin_port','pending_gateway_port')").fetchall()
            snapshot = {key: json.loads(value) for key, value in rows}
            cfg = Settings(**snapshot.get('settings', {})).model_dump()
            return {'settings': cfg, 'pending_admin_port': snapshot.get('pending_admin_port'),
                    'pending_gateway_port': snapshot.get('pending_gateway_port'),
                    'sites': configured_sites(snapshot.get('sites', []), cfg),
                    'cloudflare': snapshot.get('cloudflare_status'),
                    'note': '连接器实时进程状态请查管理台 API'}
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        raise ValueError('无法只读查看本机配置，请检查数据库状态或使用运行中的 online state') from None
