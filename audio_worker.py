#!/usr/bin/env python3
"""
audio_worker.py – Ljud-I/O i en isolerad subprocess
====================================================
Startas som en FÄRSK process per sändning av BroadcastManager. Det är avsiktligt:

En Auracast-dongle (t.ex. Sennheiser BTD 700) får ett NYTT CoreAudio-enhets-ID
när den växlar mellan streaming- och Auracast-läge. En långkörande process som
initierade PortAudio före lägesbytet kan inte återta enheten (CoreAudio-fel
-10851 / PaErrorCode -9986), och `sd._terminate()/_initialize()` flushar inte
HAL-cachen i efterhand. En ny process får däremot alltid en ren anslutning som
ser dongelns aktuella läge.

Protokoll (rader, textbaserat):
  stdin  : "vol <0..1>\n"  – ändra volym live
           "stop\n"        – avsluta
  stdout : "READY\n"               – ström öppnad, sänder
           "L <level>\n"           – VU-nivå 0..1 (10 ggr/sek)
           "ERR <meddelande>\n"    – fel, processen avslutas
           "DONE\n"                – fil slut (icke-loopad)

Enheter resolvas på NAMN (inte index) för robusthet mot omnumrering.
"""

import os
import sys
import signal
import argparse
import threading

LEVEL_INTERVAL = 0.1  # sek mellan L-rader till managern


# Skriv ut direkt utan buffring så managern ser nivå/status i realtid
def emit(line: str):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def resolve_index(name: str, kind: str):
    """Hitta enhetsindex för ett enhetsnamn (kind='output'|'input')."""
    if not name:
        return None
    from dongle import find_devices
    inputs, outputs = find_devices()
    pool = outputs if kind == "output" else inputs
    # Exakt match först, annars delsträng
    for d in pool:
        if d.name == name:
            return d.index
    for d in pool:
        if name.lower() in d.name.lower():
            return d.index
    return None


def main(argv=None):
    """Kör workern. `argv` kan ges explicit (används av frozen app via --worker)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=["mic", "system", "file"])
    parser.add_argument("--output-name", required=True)
    parser.add_argument("--input-name", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--volume", type=float, default=0.8)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args(argv)

    from router import AudioRouter, FileRouter

    stop_flag = threading.Event()

    out_idx = resolve_index(args.output_name, "output")
    if out_idx is None:
        emit(f"ERR Utgångsenheten '{args.output_name}' hittades inte")
        return

    in_idx = resolve_index(args.input_name, "input") if args.input_name else None

    # VU-nivån sätts från ljud-callbacken men SKICKAS av en separat tråd.
    # En stdout-write i callbacken kan blockera realtidstråden om pipen
    # är full → ljudglapp. En variabel-skrivning kan aldrig blockera.
    level = [0.0]

    def on_level(v: float):
        level[0] = v

    if args.source == "file":
        router = FileRouter(
            file_path=args.file,
            output_device=out_idx,
            volume=args.volume,
            loop=args.loop,
            on_level=on_level,
            on_finished=lambda: (emit("DONE"), stop_flag.set()),
        )
    else:
        router = AudioRouter(
            input_device=in_idx,
            output_device=out_idx,
            volume=args.volume,
            on_level=on_level,
        )

    # SIGTERM (från managern) → avsluta värdigt via stop-flaggan
    signal.signal(signal.SIGTERM, lambda *_: stop_flag.set())

    # Kontrolltråd: läs kommandon från stdin
    def control_loop():
        for raw in sys.stdin:
            cmd = raw.strip()
            if cmd == "stop":
                break
            if cmd.startswith("vol "):
                try:
                    router.volume = max(0.0, min(1.0, float(cmd[4:])))
                except ValueError:
                    pass
        stop_flag.set()

    threading.Thread(target=control_loop, daemon=True).start()

    try:
        router.start()
    except Exception as exc:
        emit(f"ERR {exc}")
        return

    emit("READY")

    # Nivå-rapportör: skickar senaste nivån 10 ggr/sek tills stopp
    def level_loop():
        while not stop_flag.wait(LEVEL_INTERVAL):
            emit(f"L {level[0]:.4f}")

    threading.Thread(target=level_loop, daemon=True).start()

    try:
        stop_flag.wait()
    except KeyboardInterrupt:
        pass

    # Stäng ljudströmmen RENT (router.stop() väntar in ljudtråden) och avsluta
    # sedan med os._exit() så att Pythons finalisering ALDRIG kör medan en
    # PortAudio-tråd lever – det var orsaken till segfault-kraschen vid stopp.
    try:
        router.stop()
    except Exception:
        pass
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
