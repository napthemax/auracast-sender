"""
Audio-router
-------------
Tar ljud från en källa (mikrofon, fil, loopback) och matar ut det
till en Auracast USB-dongle via sounddevice.

Dongeln (BTD 700, FMA120, m.fl.) tar emot vanligt PCM-ljud och
sköter själv Auracast-sändningen internt – appen behöver inte
hantera Bluetooth-protokollet alls.
"""

import threading
import wave
import logging
from pathlib import Path
from typing import Optional, Callable

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000   # Hz – standard för USB-audio och BTD 700 / FMA120
CHANNELS = 2
BLOCKSIZE = 1024      # Samplar per callback

# Största tillåtna dekodade filstorlek (float32-PCM i minnet).
# 500 MB ≈ 43 min stereo @ 48 kHz – långt mer än användningsfallet kräver.
MAX_DECODED_BYTES = 500_000_000


class AudioRouter:
    """
    Routar ljud live från ingång (mikrofon/loopback) → Auracast-dongle.

    Används så här:
        router = AudioRouter(input_device=0, output_device=5)
        router.start()
        ...
        router.stop()
    """

    def __init__(
        self,
        input_device: Optional[int],        # None = standard mikrofon
        output_device: int,                  # Index för Auracast-dongeln
        volume: float = 1.0,                 # 0.0–1.0
        on_level: Optional[Callable[[float], None]] = None,  # VU-mätare
    ):
        self.input_device = input_device
        self.output_device = output_device
        self.volume = volume
        self.on_level = on_level

        self._stream: Optional[sd.Stream] = None
        self._running = False

    def start(self):
        """Öppnar dubbelriktad ström: ingång in, dongle ut."""
        if self._running:
            return

        # Välj samplingsfrekvens baserat på dongeln
        try:
            dev_info = sd.query_devices(self.output_device)
            samplerate = int(dev_info["default_samplerate"])
        except Exception:
            dev_info = None
            samplerate = SAMPLE_RATE

        # Anpassa kanalantal till enheternas faktiska kapacitet.
        # Många macOS-mikrofoner är mono (1 kanal) – begär aldrig fler
        # kanaler än enheten stöder, annars vägrar PortAudio öppna strömmen.
        try:
            in_info = sd.query_devices(
                self.input_device if self.input_device is not None
                else sd.default.device[0]
            )
            in_ch = min(CHANNELS, max(1, in_info["max_input_channels"]))
        except Exception:
            in_ch = 1
        try:
            out_ch = min(CHANNELS, max(1, dev_info["max_output_channels"]))
        except Exception:
            out_ch = CHANNELS

        def callback(indata, outdata, frames, time_info, status):
            if status:
                logger.debug("Ljudstatus: %s", status)

            data = indata * self.volume

            # Anpassa kanalantal ingång → utgång
            ich = data.shape[1]
            och = outdata.shape[1]
            if ich == och:
                out = data
            elif ich == 1:
                out = np.repeat(data, och, axis=1)       # mono → flera kanaler
            else:
                out = data.mean(axis=1, keepdims=True)    # mixa ner …
                if och > 1:
                    out = np.repeat(out, och, axis=1)     # … och sprid ut
            outdata[:] = out

            if self.on_level:
                rms = float(np.sqrt(np.mean(out**2)))
                self.on_level(min(rms * 10, 1.0))

        try:
            self._stream = sd.Stream(
                samplerate=samplerate,
                blocksize=BLOCKSIZE,
                device=(self.input_device, self.output_device),
                channels=(in_ch, out_ch),
                dtype="float32",
                callback=callback,
                latency="low",
            )
            self._stream.start()
            self._running = True
            logger.info(
                "Routing: enhet %s → enhet %s @ %d Hz",
                self.input_device,
                self.output_device,
                samplerate,
            )
        except sd.PortAudioError as exc:
            raise RuntimeError(f"Kunde inte öppna ljudström: {exc}") from exc

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running


class FileRouter:
    """
    Spelar upp en WAV-fil till Auracast-dongeln.

    Hela filen dekodas (och resamplas vid behov) EN gång i start().
    Uppspelningen sker sedan ur minnesbufferten med EN öppen ström:
    - sömlös loop (ingen ström-omstart eller fil-omläsning per varv)
    - inga resample-skarvar mellan chunkar
    - startfel (trasig fil, fel format) rapporteras direkt från start()
    """

    def __init__(
        self,
        file_path: str,
        output_device: int,
        volume: float = 1.0,
        loop: bool = True,
        on_level: Optional[Callable[[float], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
    ):
        self.file_path = Path(file_path)
        self.output_device = output_device
        self.volume = volume
        self.loop = loop
        self.on_level = on_level
        self.on_finished = on_finished

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._audio: Optional[np.ndarray] = None   # (frames, 2) float32
        self._samplerate = SAMPLE_RATE

    def start(self):
        if self._running:
            return

        try:
            dev_info = sd.query_devices(self.output_device)
            self._samplerate = int(dev_info["default_samplerate"])
        except Exception:
            self._samplerate = SAMPLE_RATE

        # Dekoda hela filen nu – fel upptäcks här och kastas synkront
        self._audio = self._load_wav(self.file_path, self._samplerate)

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._play, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._running = False
        # Vänta in uppspelningstråden så ljudströmmen hinner stängas RENT
        # innan processen avslutas. Annars kan PortAudio-anropet (via CFFI)
        # krocka med Python-nedstängningen → segfault.
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3)

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Uppspelning
    # ------------------------------------------------------------------

    def _play(self):
        audio = self._audio
        chunk = self._samplerate // 10  # 100 ms per skrivning
        pos = 0

        try:
            stream = sd.OutputStream(
                samplerate=self._samplerate,
                channels=CHANNELS,
                device=self.output_device,
                dtype="float32",
                latency="low",
            )
            stream.start()
        except Exception as exc:
            logger.error("Kunde inte öppna utström: %s", exc)
            self._running = False
            if self.on_finished:
                self.on_finished()
            return

        try:
            while not self._stop_event.is_set():
                end = pos + chunk
                if end <= len(audio):
                    block = audio[pos:end]
                    pos = end
                else:
                    # Filslut: loopa sömlöst eller avsluta
                    if self.loop:
                        block = np.concatenate([audio[pos:], audio[:end - len(audio)]])
                        pos = end - len(audio)
                    else:
                        block = audio[pos:]
                        pos = len(audio)

                out = block * self.volume  # live-volym per chunk

                if self.on_level and len(out):
                    rms = float(np.sqrt(np.mean(out**2)))
                    self.on_level(min(rms * 10, 1.0))

                if len(out):
                    stream.write(np.ascontiguousarray(out, dtype=np.float32))

                if not self.loop and pos >= len(audio):
                    break
        except Exception as exc:
            logger.error("Uppspelningsfel: %s", exc)
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

        self._running = False
        if self.on_finished:
            self.on_finished()

    # ------------------------------------------------------------------
    # Dekodning
    # ------------------------------------------------------------------

    @staticmethod
    def _load_wav(path: Path, out_samplerate: int) -> np.ndarray:
        """Läser en WAV-fil → (frames, 2) float32 @ out_samplerate."""
        if path.suffix.lower() != ".wav":
            raise RuntimeError(f"Filformatet stöds inte: {path.suffix} (stöder .wav)")

        with wave.open(str(path), "rb") as wf:
            src_rate = wf.getframerate()
            src_ch = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            n_frames = wf.getnframes()

            decoded_bytes = n_frames * max(src_ch, 2) * 4
            if decoded_bytes > MAX_DECODED_BYTES:
                raise RuntimeError(
                    f"Filen är för lång ({decoded_bytes // 1_000_000} MB dekodad). "
                    f"Max ≈ {MAX_DECODED_BYTES // 1_000_000} MB (~43 min stereo)."
                )

            raw = wf.readframes(n_frames)

        # PCM → float32 (vektoriserat, även 24-bit)
        if sampwidth == 1:    # 8-bit unsigned
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif sampwidth == 2:  # 16-bit
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sampwidth == 3:  # 24-bit
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            vals = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
            vals = np.where(vals & 0x800000, vals - 0x1000000, vals)
            data = vals.astype(np.float32) / 8388608.0
        elif sampwidth == 4:  # 32-bit
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            raise RuntimeError(f"Ovanlig sampelbredd: {sampwidth * 8}-bit stöds inte.")

        # (frames, kanaler) → stereo
        data = data.reshape(-1, src_ch)
        if src_ch == 1:
            data = np.repeat(data, 2, axis=1)
        elif src_ch > 2:
            data = data[:, :2]

        # Resampla hela filen en gång (linjär interpolation, inga skarvar)
        if src_rate != out_samplerate:
            data = _resample(data, src_rate, out_samplerate)

        return np.ascontiguousarray(data, dtype=np.float32)


def _resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    src_len = len(data)
    dst_len = int(src_len * dst_rate / src_rate)
    if dst_len == src_len:
        return data
    x_src = np.linspace(0.0, 1.0, src_len)
    x_dst = np.linspace(0.0, 1.0, dst_len)
    out = np.empty((dst_len, data.shape[1]), dtype=np.float32)
    for ch in range(data.shape[1]):
        out[:, ch] = np.interp(x_dst, x_src, data[:, ch])
    return out
