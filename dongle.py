"""
USB Auracast-dongle-detektor
-----------------------------
Hittar kända Auracast USB-ljud-donglar bland macOS ljudenheter.
Dessa donglar registreras av macOS som vanliga USB-ljud-utgångar (class-compliant) –
Auracast-sändningen sköts helt inuti dongeln.
"""

import sounddevice as sd
from dataclasses import dataclass
from typing import Optional

# Nyckelord i enhetsnamnet för kända Auracast-donglar
AURACAST_DONGLE_KEYWORDS = [
    "floogoo",
    "fma120",
    "fma121",
    "flairmesh",
    "qcc3086",        # Qualcomm-chippet i FMA120 – macOS visar chipnamnet
    "qcc5181",
    "qcc5171",
    "btd 700",
    "btd700",
    "sennheiser dongle",
    "moerlink",
    "moor audio",
    "auracast",
    "le audio",
]


@dataclass
class AudioDevice:
    index: int
    name: str
    max_output_channels: int
    max_input_channels: int
    default_samplerate: float
    is_auracast: bool


def refresh_audio_backend() -> None:
    """
    Tvingar PortAudio att läsa om enhetstopologin.

    Nödvändigt eftersom en Auracast-dongle (t.ex. BTD 700) får ett NYTT
    internt CoreAudio-ID när den växlar läge (streaming ↔ Auracast-broadcast).
    Utan detta pekar enhetsindexen på inaktuella handtag och PortAudio vägrar
    öppna strömmen (CoreAudio-fel -10851 / PaErrorCode -9986).

    OBS: Får ALDRIG anropas medan en ljudström är aktiv – det river strömmen.
    """
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        # Om reinit misslyckas faller vi tillbaka på befintlig cache
        pass


def find_devices() -> tuple[list[AudioDevice], list[AudioDevice]]:
    """
    Returnerar (ingångar, utgångar) som AudioDevice-objekt.
    Auracast-donglar markeras med is_auracast=True.
    """
    inputs: list[AudioDevice] = []
    outputs: list[AudioDevice] = []

    for i, dev in enumerate(sd.query_devices()):
        name_lower = dev["name"].lower()
        is_auracast = any(kw in name_lower for kw in AURACAST_DONGLE_KEYWORDS)

        device = AudioDevice(
            index=i,
            name=dev["name"],
            max_output_channels=dev["max_output_channels"],
            max_input_channels=dev["max_input_channels"],
            default_samplerate=dev["default_samplerate"],
            is_auracast=is_auracast,
        )

        if dev["max_output_channels"] > 0:
            outputs.append(device)
        if dev["max_input_channels"] > 0:
            inputs.append(device)

    return inputs, outputs


def find_auracast_output() -> Optional[AudioDevice]:
    """Returnerar första hittade Auracast-dongle-utgång, eller None."""
    _, outputs = find_devices()
    for dev in outputs:
        if dev.is_auracast:
            return dev
    return None


def find_loopback_input() -> Optional[AudioDevice]:
    """Hittar BlackHole eller annan loopback-enhet för systemljud."""
    inputs, _ = find_devices()
    for dev in inputs:
        name = dev.name.lower()
        if any(kw in name for kw in ["blackhole", "loopback", "soundflower"]):
            return dev
    return None
