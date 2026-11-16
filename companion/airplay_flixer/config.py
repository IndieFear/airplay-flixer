from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def data_directory() -> Path:
    path = Path(os.environ.get("AIRPLAY_FLIXER_HOME", Path.home() / "Library/Application Support/Airplay Flixer"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path: Path, value):
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False)
    os.replace(temporary, path)
    path.chmod(0o600)


def web_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 16384:
        raise ValueError("Invalid media URL.")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("The media must have an HTTP or HTTPS URL. Blob URLs are unsupported.")
    return value


def origin(value: str) -> str:
    parsed = urlsplit(web_url(value))
    host = parsed.hostname.lower()
    if ':' in host:
        host = f'[{host}]'
    port = parsed.port
    suffix = f':{port}' if port and port != (443 if parsed.scheme == 'https' else 80) else ''
    return f'{parsed.scheme}://{host}{suffix}'

