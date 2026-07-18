"""
BroadcastManager
----------------
Delad tillståndshantering för Auracast-sändningen. Används av HTTP-API:t (server.py).

Själva ljud-I/O:t körs i en FÄRSK subprocess (audio_worker.py) per sändning.
Det är avgörande: en Auracast-dongle (BTD 700) byter CoreAudio-enhets-ID när den
växlar streaming-/Auracast-läge, och en långkörande process kan inte återta enheten
(CoreAudio-fel -10851). En ny process får alltid en ren anslutning till dongelns
aktuella läge. Managern resolvar enheter till NAMN och låter subprocessen slå upp
färska index i sin egen process.
"""

import sys
import time
import threading
import logging
import subprocess
from pathlib import Path
from typing import Optional

from dongle import find_devices, AudioDevice, find_loopback_input

logger = logging.getLogger(__name__)

WORKER = Path(__file__).parent / "audio_worker.py"
READY_TIMEOUT = 6.0  # sek att vänta på att strömmen öppnas


class BroadcastManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._stderr_file = None
        self._reader: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._running = False
        self._level = 0.0
        self._started_at: Optional[float] = None
        self._config: dict = {}
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Enheter
    # ------------------------------------------------------------------

    def list_devices(self) -> dict:
        inputs, outputs = find_devices()
        return {
            "inputs": [self._dev_dict(d) for d in inputs],
            "outputs": [self._dev_dict(d) for d in outputs],
        }

    @staticmethod
    def _dev_dict(d: AudioDevice) -> dict:
        return {
            "index": d.index,
            "name": d.name,
            "channels": max(d.max_output_channels, d.max_input_channels),
            "samplerate": int(d.default_samplerate),
            "is_auracast": d.is_auracast,
        }

    def find_default_output(self) -> Optional[int]:
        """Returnerar index för Auracast-dongeln om den finns."""
        _, outputs = find_devices()
        for d in outputs:
            if d.is_auracast:
                return d.index
        return None

    def _name_for_index(self, index: Optional[int], kind: str) -> Optional[str]:
        """Slår upp enhetsnamnet för ett index (kind='output'|'input')."""
        if index is None:
            return None
        inputs, outputs = find_devices()
        pool = outputs if kind == "output" else inputs
        for d in pool:
            if d.index == index:
                return d.name
        # Fallback: sök i båda om kind-gissningen var fel
        for d in inputs + outputs:
            if d.index == index:
                return d.name
        return None

    # ------------------------------------------------------------------
    # Sändningskontroll
    # ------------------------------------------------------------------

    def start(
        self,
        source: str,                       # "mic" | "system" | "file"
        output_device: int,
        input_device: Optional[int] = None,
        file_path: Optional[str] = None,
        volume: float = 0.8,
        loop: bool = True,
    ) -> dict:
        with self._lock:
            if self._is_alive():
                return {"ok": False, "error": "Sändning pågår redan. Stoppa först."}

            self._last_error = None
            self._level = 0.0

            # Resolva enhets-INDEX → NAMN (subprocessen slår sedan upp färska index).
            output_name = self._name_for_index(output_device, "output")
            if not output_name:
                return {"ok": False, "error": f"Utgångsenhet (index {output_device}) hittades inte."}

            input_name = None
            if source == "mic" and input_device is not None:
                input_name = self._name_for_index(input_device, "input")
            elif source == "system":
                lb = find_loopback_input()
                if lb is None:
                    return {
                        "ok": False,
                        "error": "Ingen loopback-enhet (BlackHole) hittad för systemljud.",
                    }
                input_name = lb.name

            if source == "file":
                if not file_path or not Path(file_path).exists():
                    return {"ok": False, "error": f"Filen finns inte: {file_path}"}

            # Bygg kommandoraden för subprocessen.
            # I en PyInstaller-fryst app finns ingen python-interpreter –
            # då startar vi APPENS EGEN binär med --worker, som dispatchar
            # till audio_worker.main() (se main.py) i en färsk process.
            if getattr(sys, "frozen", False):
                base = [sys.executable, "--worker"]
            else:
                base = [sys.executable, str(WORKER)]
            argv = base + [
                "--source", source,
                "--output-name", output_name,
                "--volume", str(volume),
            ]
            if input_name:
                argv += ["--input-name", input_name]
            if source == "file":
                argv += ["--file", file_path]
                if loop:
                    argv += ["--loop"]

            self._ready.clear()
            self._running = False
            try:
                # Handtaget sparas och stängs i _cleanup_proc – annars läcker
                # en filbeskrivare per start.
                self._stderr_file = open("/tmp/auracast_worker.log", "w")
                self._proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=self._stderr_file,
                    text=True,
                    bufsize=1,
                    cwd=str(WORKER.parent),
                )
            except Exception as exc:
                self._last_error = str(exc)
                self._close_stderr_file()
                return {"ok": False, "error": str(exc)}

            self._reader = threading.Thread(target=self._read_output, daemon=True)
            self._reader.start()

            # Vänta på READY eller ERR (synkront svar till API:t)
            self._ready.wait(timeout=READY_TIMEOUT)

            if not self._running:
                err = self._last_error or "Tidsgräns: strömmen öppnades inte."
                self._cleanup_proc()
                return {"ok": False, "error": err}

            self._started_at = time.time()
            self._config = {
                "source": source,
                "output_device": output_name,
                "input_device": input_name,
                "file_path": file_path,
                "volume": volume,
                "loop": loop,
            }
            logger.info("Sändning startad: %s", self._config)
            return {"ok": True}

    def _read_output(self):
        """Läser subprocessens stdout och uppdaterar tillstånd.

        Guardas med `self._proc is proc`: en läsartråd för en GAMMAL process
        får aldrig skriva över tillståndet efter att en ny sändning startats
        (tråden kan leva någon millisekund efter stop() → start()).
        """
        proc = self._proc
        if not proc or not proc.stdout:
            return
        for raw in proc.stdout:
            if self._proc is not proc:
                return  # ny process har tagit över – släpp allt
            line = raw.strip()
            if line == "READY":
                self._running = True
                self._ready.set()
            elif line.startswith("L "):
                try:
                    self._level = float(line[2:])
                except ValueError:
                    pass
            elif line.startswith("ERR "):
                self._last_error = line[4:]
                self._running = False
                self._ready.set()
            elif line == "DONE":
                self._running = False
        # stdout stängd → processen avslutad
        if self._proc is proc:
            self._running = False
            self._level = 0.0
            self._ready.set()

    def _is_alive(self) -> bool:
        return bool(self._proc and self._proc.poll() is None and self._running)

    def _close_stderr_file(self):
        if self._stderr_file:
            try:
                self._stderr_file.close()
            except Exception:
                pass
            self._stderr_file = None

    def _cleanup_proc(self):
        if self._proc:
            try:
                if self._proc.poll() is None:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
            except Exception:
                pass
            # Stäng våra pipe-ändar så filbeskrivare inte läcker
            for pipe in (self._proc.stdin, self._proc.stdout):
                try:
                    if pipe:
                        pipe.close()
                except Exception:
                    pass
        self._close_stderr_file()
        self._proc = None
        self._running = False
        self._level = 0.0

    def stop(self) -> dict:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.stdin.write("stop\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
            self._cleanup_proc()
            self._started_at = None
            self._config = {}
            logger.info("Sändning stoppad.")
            return {"ok": True}

    def set_volume(self, volume: float) -> dict:
        volume = max(0.0, min(1.0, volume))
        with self._lock:
            if self._proc and self._proc.poll() is None and self._proc.stdin:
                try:
                    self._proc.stdin.write(f"vol {volume}\n")
                    self._proc.stdin.flush()
                except Exception:
                    pass
            if self._config:
                self._config["volume"] = volume
        return {"ok": True, "volume": volume}

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        running = self._is_alive()
        elapsed = (time.time() - self._started_at) if (running and self._started_at) else 0.0
        return {
            "broadcasting": running,
            "level": round(self._level, 4),
            "elapsed_seconds": round(elapsed, 1),
            "config": self._config,
            "last_error": self._last_error,
        }
