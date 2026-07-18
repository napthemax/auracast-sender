#!/usr/bin/env python3
"""
Auracast Sender – HTTP API-server
==================================
Exponerar Auracast-routingen som ett REST-API så att en webapp
(t.ex. byggd i Lovable) kan styra sändningen i webbläsaren.

Ljudet routas till en USB Auracast-dongle (Sennheiser BTD 700 m.fl.)
som sköter själva Auracast-sändningen internt.

Kör:   python3 server.py
Docs:  http://localhost:8765/docs   (interaktiv OpenAPI)

CORS är öppet för alla origins så Lovable-appen kan anropa API:t.
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from manager import BroadcastManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

API_VERSION = "1.0"
PORT = 8765
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="Auracast Sender API",
    version=API_VERSION,
    description="Styr Auracast-sändning från en USB-dongle (t.ex. Sennheiser BTD 700).",
)

# Öppen CORS – tillåter Lovable-webappen att anropa lokalt API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = BroadcastManager()


# ──────────────────────────────────────────────────────────────────────
# Request-modeller
# ──────────────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    source: str                         # "mic" | "system" | "file"
    output_device: Optional[int] = None  # None = auto-detektera Auracast-dongle
    input_device: Optional[int] = None   # None = standard / auto för system
    file_path: Optional[str] = None      # krävs för source="file"
    volume: float = 0.8                  # 0.0–1.0
    loop: bool = True                    # loopa fil


class VolumeRequest(BaseModel):
    volume: float


# ──────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    """Enkel hälsokontroll – används av webappen för att hitta servern."""
    return {"ok": True, "service": "auracast-sender", "version": API_VERSION}


@app.get("/api/devices")
def devices():
    """
    Listar ljudenheter. Auracast-donglar markeras med is_auracast=true.
    `default_output` är index för auto-detekterad dongle (eller null).
    """
    data = manager.list_devices()
    data["default_output"] = manager.find_default_output()
    return data


@app.get("/api/status")
def status():
    """
    Nuvarande sändningsstatus inkl. nivå (VU 0–1) och konfiguration.
    Polla denna (t.ex. var 200 ms) för en live VU-mätare i webappen.
    """
    return manager.status()


@app.post("/api/start")
def start(req: StartRequest):
    """
    Startar sändning. Om output_device utelämnas auto-väljs Auracast-dongeln.
    """
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
        file_path=req.file_path,
        volume=req.volume,
        loop=req.loop,
    )
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"ok": True, "status": manager.status()}


@app.post("/api/stop")
def stop():
    """Stoppar pågående sändning."""
    manager.stop()
    return {"ok": True, "status": manager.status()}


@app.post("/api/volume")
def set_volume(req: VolumeRequest):
    """Justerar volymen live (0.0–1.0)."""
    return manager.set_volume(req.volume)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """
    Laddar upp en WAV-fil för uppspelning. Returnerar `file_path` som
    sedan skickas till /api/start med source="file".
    """
    # Sanera filnamnet: släng all sökvägsinformation så ett namn som
    # "../../x.wav" aldrig kan skriva utanför uploads-katalogen.
    safe_name = Path(file.filename or "").name
    if not safe_name.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Endast .wav-filer stöds.")

    dest = UPLOAD_DIR / safe_name
    with dest.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    logger.info("Fil uppladdad: %s", dest)
    return {"ok": True, "file_path": str(dest), "filename": safe_name}


@app.get("/api/uploads")
def list_uploads():
    """Listar tidigare uppladdade WAV-filer."""
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

def main():
    import uvicorn
    w = 46
    print("")
    print("  ╔" + "═" * w + "╗")
    print("  ║" + "  Auracast Sender API".ljust(w) + "║")
    print("  ╠" + "═" * w + "╣")
    print("  ║" + f"  API:   http://localhost:{PORT}".ljust(w) + "║")
    print("  ║" + f"  Docs:  http://localhost:{PORT}/docs".ljust(w) + "║")
    print("  ╚" + "═" * w + "╝")
    print("")
    # Auto-detektera dongle vid start
    out = manager.find_default_output()
    if out is not None:
        devs = manager.list_devices()
        name = next((d["name"] for d in devs["outputs"] if d["index"] == out), "?")
        print(f"  ✓ Auracast-dongle hittad: {name} (index {out})")
    else:
        print("  ⚠ Ingen Auracast-dongle hittad – anslut en BTD 700 / FMA120.")
    print("")

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
