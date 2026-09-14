#!/usr/bin/env python3
"""
Auracast Sender – HTTP API server
=================================
Exposes Auracast routing as a REST API so a web app (e.g. built in Lovable)
can control the broadcast from a browser.

Audio is routed to a USB Auracast dongle (Sennheiser BTD 700 and similar)
which handles the actual Auracast transmission internally.

Run:   python3 server.py
Docs:  http://127.0.0.1:8765/docs   (interactive OpenAPI)

Secure by default: binds to localhost, requires a local API key on /api
routes (except GET /api/health), and restricts CORS to localhost origins.
"""

import argparse
import logging
import os
import secrets
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from manager import BroadcastManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

API_VERSION = "1.1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MAX_UPLOAD_MB = 50
LOCAL_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
OPEN_API_PATHS = frozenset({"/api/health"})

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


def resolve_bind_host(cli_host: Optional[str] = None, lan: bool = False) -> str:
    """Bind address: localhost unless the user explicitly opts into LAN."""
    if cli_host:
        return cli_host
    if lan:
        return "0.0.0.0"
    env_host = (os.environ.get("AURACAST_HOST") or os.environ.get("AURACAST_BIND") or "").strip()
    if env_host:
        return env_host
    if os.environ.get("AURACAST_LAN", "").strip().lower() in ("1", "true", "yes"):
        return "0.0.0.0"
    return DEFAULT_HOST


def resolve_port(cli_port: Optional[int] = None) -> int:
    if cli_port is not None:
        return cli_port
    raw = os.environ.get("AURACAST_PORT", "").strip()
    if raw:
        return int(raw)
    return DEFAULT_PORT


def resolve_max_upload_bytes() -> int:
    raw = os.environ.get("AURACAST_MAX_UPLOAD_MB", "").strip()
    mb = int(raw) if raw else DEFAULT_MAX_UPLOAD_MB
    if mb < 1:
        mb = 1
    return mb * 1024 * 1024


def default_api_key_file() -> Path:
    override = os.environ.get("AURACAST_API_KEY_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".auracast-sender" / "api_key"


def load_or_create_api_key() -> tuple[str, Path]:
    """Return (key, key_file). Env AURACAST_API_KEY wins; otherwise persist locally."""
    key_file = default_api_key_file()
    env_key = os.environ.get("AURACAST_API_KEY", "").strip()
    if env_key:
        return env_key, key_file
    if key_file.is_file():
        existing = key_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing, key_file
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(32)
    key_file.write_text(key + "\n", encoding="utf-8")
    os.chmod(key_file, 0o600)
    logger.info("Generated API key and saved to %s", key_file)
    return key, key_file


def cors_kwargs() -> dict:
    extra = os.environ.get("AURACAST_CORS_ORIGINS", "").strip()
    if extra == "*":
        return {
            "allow_origins": ["*"],
            "allow_origin_regex": None,
        }
    origins = [o.strip() for o in extra.split(",") if o.strip()]
    return {
        "allow_origins": origins,
        "allow_origin_regex": LOCAL_ORIGIN_REGEX,
    }


def api_key_from_request(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    scheme, _, remainder = auth.partition(" ")
    if scheme.lower() == "bearer" and remainder.strip():
        return remainder.strip()
    return (request.headers.get("X-Api-Key") or "").strip()


def keys_match(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    # compare_digest requires equal length; mismatch is simply not a match.
    if len(provided) != len(expected):
        return False
    return secrets.compare_digest(provided, expected)


def constrain_upload_file_path(file_path: str) -> str:
    """Resolve file_path and require it to be a real file under uploads/."""
    try:
        resolved = Path(file_path).expanduser().resolve()
        uploads = UPLOAD_DIR.resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid file_path: {exc}") from exc
    if not resolved.is_relative_to(uploads):
        raise HTTPException(
            status_code=400,
            detail="file_path must be a file under the app uploads/ directory "
            "(upload via POST /api/upload).",
        )
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail=f"File does not exist: {file_path}")
    return str(resolved)


API_KEY, API_KEY_FILE = load_or_create_api_key()
MAX_UPLOAD_BYTES = resolve_max_upload_bytes()
PORT = resolve_port()

app = FastAPI(
    title="Auracast Sender API",
    version=API_VERSION,
    description=(
        "Control Auracast broadcast from a USB dongle (e.g. Sennheiser BTD 700). "
        "Protected routes require Authorization: Bearer <key> or X-Api-Key. "
        "GET /api/health is open for discovery."
    ),
)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if path in OPEN_API_PATHS or not path.startswith("/api/"):
            return await call_next(request)
        if not keys_match(api_key_from_request(request), API_KEY):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Invalid or missing API key. "
                    "Send Authorization: Bearer <key> or X-Api-Key.",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


# Auth first (inner), CORS last (outer) so preflight OPTIONS never hits the key check
# and 401 responses still get CORS headers.
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    **cors_kwargs(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = BroadcastManager()


# ──────────────────────────────────────────────────────────────────────
# Request models
# ──────────────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    source: str                         # "mic" | "system" | "file"
    output_device: Optional[int] = None  # None = auto-detect Auracast dongle
    input_device: Optional[int] = None   # None = default / auto for system
    file_path: Optional[str] = None      # required for source="file"; must be under uploads/
    volume: float = 0.8                  # 0.0–1.0
    loop: bool = True                    # loop file


class VolumeRequest(BaseModel):
    volume: float


# ──────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    """Liveness check — left unauthenticated so clients can discover the server."""
    return {
        "ok": True,
        "service": "auracast-sender",
        "version": API_VERSION,
        "auth": "api_key",
    }


@app.get("/api/devices")
def devices():
    """
    List audio devices. Auracast dongles are marked is_auracast=true.
    `default_output` is the auto-detected dongle index (or null).
    """
    data = manager.list_devices()
    data["default_output"] = manager.find_default_output()
    return data


@app.get("/api/status")
def status():
    """
    Current broadcast status including level (VU 0–1) and configuration.
    Poll this (e.g. every 200 ms) for a live VU meter in the web app.
    """
    return manager.status()


@app.post("/api/start")
def start(req: StartRequest):
    """
    Start broadcast. If output_device is omitted the Auracast dongle is auto-selected.
    For source=file, file_path must resolve inside uploads/.
    """
    file_path = req.file_path
    if req.source == "file":
        if not file_path:
            raise HTTPException(
                status_code=400,
                detail="file_path is required when source is 'file'.",
            )
        file_path = constrain_upload_file_path(file_path)

    output = req.output_device
    if output is None:
        output = manager.find_default_output()
        if output is None:
            raise HTTPException(
                status_code=400,
                detail="Ingen Auracast-dongle hittad. Anslut en BTD 700 / FMA120, "
                       "eller ange output_device explicit.",
            )

    result = manager.start(
        source=req.source,
        output_device=output,
        input_device=req.input_device,
        file_path=file_path,
        volume=req.volume,
        loop=req.loop,
    )
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"ok": True, "status": manager.status()}


@app.post("/api/stop")
def stop():
    """Stop the current broadcast."""
    manager.stop()
    return {"ok": True, "status": manager.status()}


@app.post("/api/volume")
def set_volume(req: VolumeRequest):
    """Adjust volume live (0.0–1.0)."""
    return manager.set_volume(req.volume)


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    """
    Upload a WAV file for playback. Returns `file_path` to send to /api/start
    with source="file". Size is capped (default 50 MB).
    """
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            # Multipart wrapping adds a little overhead on top of the file.
            if int(content_length) > MAX_UPLOAD_BYTES + 1024 * 1024:
                raise HTTPException(
                    status_code=413,
                    detail=f"Upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                )
        except ValueError:
            pass

    safe_name = Path(file.filename or "").name
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    if not safe_name.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Endast .wav-filer stöds.")

    dest = UPLOAD_DIR / safe_name
    written = 0
    try:
        with dest.open("wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                    )
                buffer.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    logger.info("File uploaded: %s (%s bytes)", dest, written)
    return {"ok": True, "file_path": str(dest), "filename": safe_name, "size_bytes": written}


@app.get("/api/uploads")
def list_uploads():
    """List previously uploaded WAV files."""
    files = [
        {"filename": f.name, "file_path": str(f), "size_bytes": f.stat().st_size}
        for f in UPLOAD_DIR.glob("*.wav")
    ]
    return {"files": files}


@app.get("/")
def root():
    return JSONResponse({
        "service": "Auracast Sender API",
        "version": API_VERSION,
        "docs": "/docs",
        "auth": "Send Authorization: Bearer <key> or X-Api-Key on /api routes except GET /api/health.",
        "endpoints": [
            "GET  /api/health",
            "GET  /api/devices",
            "GET  /api/status",
            "POST /api/start",
            "POST /api/stop",
            "POST /api/volume",
            "POST /api/upload",
            "GET  /api/uploads",
        ],
    })


# ──────────────────────────────────────────────────────────────────────
# Start
# ──────────────────────────────────────────────────────────────────────

def _print_banner(host: str, port: int) -> None:
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    w = 62
    lines = [
        "",
        "  ╔" + "═" * w + "╗",
        "  ║" + "  Auracast Sender API".ljust(w) + "║",
        "  ╠" + "═" * w + "╣",
        "  ║" + f"  API:   http://{display_host}:{port}".ljust(w) + "║",
        "  ║" + f"  Docs:  http://{display_host}:{port}/docs".ljust(w) + "║",
        "  ║" + f"  Bind:  {host}".ljust(w) + "║",
        "  ║" + f"  Key:   {API_KEY}".ljust(w) + "║",
        "  ║" + f"  File:  {API_KEY_FILE}".ljust(w) + "║",
        "  ╚" + "═" * w + "╝",
        "",
        "  Send the key as Authorization: Bearer <key> or X-Api-Key.",
    ]
    if host not in ("127.0.0.1", "localhost", "::1"):
        lines.append("  WARNING: Listening beyond localhost. Keep the API key secret;")
        lines.append("  do not tunnel this API to the public internet.")
    lines.append("")
    print("\n".join(lines), flush=True)


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Auracast Sender HTTP API")
    parser.add_argument(
        "--host",
        default=None,
        help="Bind address (default 127.0.0.1). Use 0.0.0.0 to listen on LAN.",
    )
    parser.add_argument(
        "--lan",
        action="store_true",
        help="Listen on all interfaces (0.0.0.0). Same as --host 0.0.0.0.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port (default {DEFAULT_PORT}).",
    )
    args = parser.parse_args()

    host = resolve_bind_host(cli_host=args.host, lan=args.lan)
    port = resolve_port(args.port)
    _print_banner(host, port)

    out = manager.find_default_output()
    if out is not None:
        devs = manager.list_devices()
        name = next((d["name"] for d in devs["outputs"] if d["index"] == out), "?")
        print(f"  ✓ Auracast-dongle hittad: {name} (index {out})")
    else:
        print("  ⚠ Ingen Auracast-dongle hittad – anslut en BTD 700 / FMA120.")
    print("")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
