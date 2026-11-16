"""Chrome Native Messaging transport. Never print diagnostics to stdout."""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import data_directory, read_json

MAX_MESSAGE = 1024 * 1024


def read_message(stream):
    size = stream.read(4)
    if not size:
        return None
    if len(size) != 4:
        raise ValueError("Incomplete Chrome message.")
    length = struct.unpack("<I", size)[0]
    if length > MAX_MESSAGE:
        raise ValueError("Chrome message too large.")
    payload = stream.read(length)
    if len(payload) != length:
        raise ValueError("Incomplete Chrome message.")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Invalid Chrome message.")
    return value


def write_message(stream, message):
    payload = json.dumps(message, ensure_ascii=False).encode()
    stream.write(struct.pack("<I", len(payload)) + payload)
    stream.flush()


def request(message):
    runtime = read_json(data_directory() / "runtime.json")
    port = runtime.get("port")
    if not isinstance(port, int) or not 1024 <= port <= 65535 or not runtime.get("token"):
        raise ConnectionError("Companion stopped.")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/rpc", data=json.dumps(message).encode(),
        headers={"Authorization": f"Bearer {runtime['token']}", "Content-Type": "application/json"},
    )
    # A browser/network proxy must never see the local control token.
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with client.open(req, timeout=95) as response:
        return json.load(response)


def request_with_launch(message):
    try:
        return request(message)
    except (OSError, ValueError):
        settings = read_json(data_directory() / "installation.json")
        app = settings.get("app_path")
        if not app or not Path(app).is_dir():
            return {"ok": False, "error": "Open Airplay Flixer in the menu bar, then try again."}
        subprocess.run(["/usr/bin/open", "-g", app], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            time.sleep(0.25)
            try:
                return request(message)
            except (OSError, ValueError):
                pass
        return {"ok": False, "error": "The companion did not start. Open Airplay Flixer and check its log."}


def main(browser_origin: str):
    settings = read_json(data_directory() / "installation.json")
    if browser_origin != settings.get("extension_origin"):
        write_message(sys.stdout.buffer, {"ok": False, "error": "Unauthorized extension."})
        return
    while True:
        try:
            message = read_message(sys.stdin.buffer)
            if message is None:
                return
            response = request_with_launch(message)
        except Exception as error:
            response = {"ok": False, "error": str(error)}
        write_message(sys.stdout.buffer, response)

