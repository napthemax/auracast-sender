#!/usr/bin/env python3
"""
Auracast Sender för macOS – lokalt GUI
=======================================
Routar ljud från mikrofon, systemljud eller fil till en USB Auracast-dongle
(Flairmesh FMA120, Sennheiser BTD 700, m.fl.).

GUI:t styr samma BroadcastManager som HTTP-API:t (server.py) använder:
allt ljud körs i en färsk subprocess per sändning (audio_worker.py), vilket
gör att dongelns lägesbyten (nytt CoreAudio-ID) aldrig kan låsa applikationen.

Kör: python3 main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ── Worker-dispatch (MÅSTE ske före tunga importer) ──────────────────
# I den PyInstaller-paketerade appen finns ingen python-interpreter, så
# BroadcastManager startar appens egen binär med --worker för ljud-I/O.
# Då ska processen köra audio_worker – aldrig öppna ett GUI.
if "--worker" in sys.argv:
    from audio_worker import main as worker_main
    worker_main(sys.argv[sys.argv.index("--worker") + 1:])
    sys.exit(0)

# --version before Tk/audio imports so it works without those packages.
if "--version" in sys.argv:
    from version import format_version
    print(format_version())
    sys.exit(0)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
import logging

from manager import BroadcastManager
from version import APP_VERSION, format_version

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

C = {
    "bg":       "#0d1117",
    "surface":  "#161b22",
    "card":     "#21262d",
    "border":   "#30363d",
    "blue":     "#58a6ff",
    "green":    "#3fb950",
    "red":      "#f85149",
    "orange":   "#d29922",
    "text":     "#e6edf3",
    "dim":      "#8b949e",
    "white":    "#ffffff",
}


class AuracastApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Auracast Sender {APP_VERSION}")
        self.configure(bg=C["bg"])
        self.geometry("640x560")
        self.resizable(False, False)

        # Dela manager med HTTP-API:t: GUI och webapp (Lovable) styr och
        # ser SAMMA sändning. API-servern körs i en bakgrundstråd i appen.
        try:
            import server as api_server
            self._manager = api_server.manager
            self._api_module = api_server
        except Exception:
            self._manager = BroadcastManager()
            self._api_module = None

        self._queue: queue.Queue = queue.Queue()
        self._vu_level = 0.0
        self._busy = False          # start/stopp pågår i bakgrundstråd
        self._was_broadcasting = False

        self._start_api_server()
        self._queue.put(("log", format_version(), "dim"))

        # Tillståndsvariabler
        self.var_source = tk.StringVar(value="mic")
        self.var_input_dev = tk.StringVar(value="")
        self.var_output_dev = tk.StringVar(value="")
        self.var_file = tk.StringVar(value="")
        self.var_loop = tk.BooleanVar(value=True)
        self.var_volume = tk.DoubleVar(value=0.8)

        self._input_devices = []
        self._output_devices = []

        self._build_ui()
        self.after(50, self._poll)
        self.after(100, self._animate_vu)
        self.after(200, self._load_devices)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Rubrik ─────────────────────────────────────────────────────
        top = tk.Frame(self, bg=C["bg"])
        top.pack(fill="x", padx=20, pady=(18, 0))

        tk.Label(top, text="Auracast Sender", font=("SF Pro Display", 22, "bold"),
                 fg=C["white"], bg=C["bg"]).pack(side="left")

        self._badge = tk.Label(top, text=" INAKTIV ", font=("SF Pro Display", 10, "bold"),
                               fg=C["bg"], bg=C["dim"], padx=8, pady=3)
        self._badge.pack(side="right")

        tk.Frame(self, height=1, bg=C["border"]).pack(fill="x", padx=20, pady=(12, 0))

        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=12)

        left = tk.Frame(body, bg=C["bg"])
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        right = tk.Frame(body, bg=C["bg"])
        right.pack(side="right", fill="both", expand=True, padx=(8, 0))

        self._build_dongle_card(left)
        self._build_source_card(left)
        self._build_volume_card(right)
        self._build_log_card(right)
        self._build_controls()

    def _card(self, parent, title):
        outer = tk.Frame(parent, bg=C["border"], padx=1, pady=1)
        outer.pack(fill="x", pady=(0, 10))
        inner = tk.Frame(outer, bg=C["surface"], padx=14, pady=12)
        inner.pack(fill="x")
        tk.Label(inner, text=title.upper(), font=("SF Pro Display", 9, "bold"),
                 fg=C["blue"], bg=C["surface"]).pack(anchor="w", pady=(0, 8))
        return inner

    def _build_dongle_card(self, parent):
        card = self._card(parent, "Auracast-dongle (utgång)")

        self._dongle_label = tk.Label(card, text="Söker...",
                                      font=("SF Pro Display", 12, "bold"),
                                      fg=C["dim"], bg=C["surface"], wraplength=240, justify="left")
        self._dongle_label.pack(anchor="w")

        row = tk.Frame(card, bg=C["surface"])
        row.pack(fill="x", pady=(8, 0))

        tk.Label(row, text="Välj utgång:", font=("SF Pro Display", 10),
                 fg=C["dim"], bg=C["surface"]).pack(side="left")

        self._output_menu = ttk.Combobox(row, textvariable=self.var_output_dev,
                                         width=26, state="readonly")
        self._output_menu.pack(side="left", padx=(6, 0))
        self._output_menu.bind("<<ComboboxSelected>>", lambda _: self._check_dongle_selection())

        tk.Button(card, text="Uppdatera", command=self._load_devices,
                  font=("SF Pro Display", 10), fg=C["blue"], bg=C["surface"],
                  relief="flat", cursor="hand2", borderwidth=0,
                  activeforeground=C["white"], activebackground=C["surface"]
                  ).pack(anchor="w", pady=(6, 0))

    def _build_source_card(self, parent):
        card = self._card(parent, "Ljudkälla")

        for label, val in [("Mikrofon", "mic"), ("Systemljud (BlackHole)", "system"), ("Fil (WAV)", "file")]:
            tk.Radiobutton(card, text=label, variable=self.var_source, value=val,
                           font=("SF Pro Display", 11), fg=C["text"], bg=C["surface"],
                           selectcolor=C["card"], activeforeground=C["white"],
                           activebackground=C["surface"],
                           command=self._on_source_change).pack(anchor="w", pady=2)

        # Mikrofon-meny
        self._mic_frame = tk.Frame(card, bg=C["surface"])
        self._mic_frame.pack(fill="x", pady=(8, 0))

        tk.Label(self._mic_frame, text="Ingångsenhet:", font=("SF Pro Display", 10),
                 fg=C["dim"], bg=C["surface"]).pack(anchor="w")
        self._input_menu = ttk.Combobox(self._mic_frame, textvariable=self.var_input_dev,
                                        width=28, state="readonly")
        self._input_menu.pack(anchor="w", pady=(4, 0))

        # Fil-väljare
        self._file_frame = tk.Frame(card, bg=C["surface"])

        file_row = tk.Frame(self._file_frame, bg=C["surface"])
        file_row.pack(fill="x")

        self._file_entry = tk.Entry(file_row, textvariable=self.var_file,
                                    font=("SF Pro Display", 10), bg=C["card"],
                                    fg=C["text"], insertbackground=C["white"],
                                    relief="flat", width=22)
        self._file_entry.pack(side="left", ipady=4, padx=(0, 4))

        tk.Button(file_row, text="Välj...", command=self._browse,
                  font=("SF Pro Display", 10), fg=C["white"], bg=C["blue"],
                  relief="flat", cursor="hand2", padx=8, pady=4).pack(side="left")

        tk.Checkbutton(self._file_frame, text="Loopa filen", variable=self.var_loop,
                       font=("SF Pro Display", 10), fg=C["text"], bg=C["surface"],
                       selectcolor=C["card"], activeforeground=C["white"],
                       activebackground=C["surface"]).pack(anchor="w", pady=(6, 0))

    def _build_volume_card(self, parent):
        card = self._card(parent, "Volym")

        self._vol_label = tk.Label(card, text="80%", font=("SF Pro Display", 18, "bold"),
                                   fg=C["white"], bg=C["surface"])
        self._vol_label.pack(anchor="w")

        slider = tk.Scale(card, variable=self.var_volume, from_=0.0, to=1.0,
                          resolution=0.01, orient="horizontal", length=200,
                          bg=C["surface"], fg=C["dim"], troughcolor=C["card"],
                          activebackground=C["blue"], highlightthickness=0,
                          sliderlength=16, showvalue=False,
                          command=self._on_volume_change)
        slider.pack(anchor="w", pady=(4, 0))

        vu_frame = tk.Frame(card, bg=C["surface"])
        vu_frame.pack(fill="x", pady=(10, 0))

        tk.Label(vu_frame, text="NIVÅ", font=("SF Pro Display", 9),
                 fg=C["dim"], bg=C["surface"]).pack(anchor="w")

        self._vu_canvas = tk.Canvas(vu_frame, height=12, width=200,
                                    bg=C["card"], highlightthickness=0)
        self._vu_canvas.pack(anchor="w", pady=(4, 0))

    def _build_log_card(self, parent):
        card = self._card(parent, "Logg")

        self._log = tk.Text(card, height=9, font=("Monaco", 9),
                            bg=C["card"], fg=C["green"], insertbackground=C["white"],
                            relief="flat", wrap="word", state="disabled")
        self._log.pack(fill="both", expand=True)
        self._log.tag_config("warn", foreground=C["orange"])
        self._log.tag_config("error", foreground=C["red"])
        self._log.tag_config("info", foreground=C["green"])
        self._log.tag_config("dim", foreground=C["dim"])

    def _build_controls(self):
        tk.Frame(self, height=1, bg=C["border"]).pack(fill="x", padx=20)

        ctrl = tk.Frame(self, bg=C["bg"], pady=14)
        ctrl.pack()

        self._start_btn = tk.Button(ctrl, text="  Starta sändning  ",
                                    command=self._start,
                                    font=("SF Pro Display", 14, "bold"),
                                    fg=C["bg"], bg=C["green"], relief="flat",
                                    cursor="hand2", padx=18, pady=10)
        self._start_btn.pack(side="left", padx=(0, 10))

        self._stop_btn = tk.Button(ctrl, text="  Stoppa  ",
                                   command=self._stop,
                                   font=("SF Pro Display", 14, "bold"),
                                   fg=C["white"], bg=C["red"], relief="flat",
                                   cursor="hand2", padx=18, pady=10,
                                   state="disabled")
        self._stop_btn.pack(side="left")

    # ------------------------------------------------------------------
    # Inbäddad API-server (för Lovable-webappen)
    # ------------------------------------------------------------------

    def _start_api_server(self):
        """Startar HTTP-API:t på :8765 i en bakgrundstråd."""
        if not self._api_module:
            return

        host = self._api_module.resolve_bind_host()
        port = self._api_module.resolve_port()

        def run():
            try:
                import uvicorn
                uvicorn.run(self._api_module.app, host=host,
                            port=port, log_level="warning")
            except Exception as exc:
                self._queue.put(("log", f"API-server: {exc}", "warn"))

        threading.Thread(target=run, daemon=True).start()
        display = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        self._queue.put(("log", f"API-server startad: http://{display}:{port}", "dim"))
        self._queue.put((
            "log",
            f"API-nyckel: {self._api_module.API_KEY}  (sparad i {self._api_module.API_KEY_FILE})",
            "dim",
        ))
        if host not in ("127.0.0.1", "localhost", "::1"):
            self._queue.put((
                "log",
                "API lyssnar utanför localhost – håll nyckeln hemlig.",
                "warn",
            ))

    # ------------------------------------------------------------------
    # Enheter
    # ------------------------------------------------------------------

    def _load_devices(self):
        def load():
            try:
                devs = self._manager.list_devices()
                self._queue.put(("devices", devs))
            except Exception as exc:
                self._queue.put(("log", f"Kunde inte lista enheter: {exc}", "error"))

        threading.Thread(target=load, daemon=True).start()

    def _apply_devices(self, devs: dict):
        self._input_devices = devs["inputs"]
        self._output_devices = devs["outputs"]

        # Ingångar
        in_names = [d["name"] for d in self._input_devices]
        self._input_menu["values"] = in_names
        if in_names:
            default = next((n for n in in_names
                            if "macbook" in n.lower() or "built-in" in n.lower()), in_names[0])
            self.var_input_dev.set(default)

        # Utgångar (dongle markeras med ★)
        out_names = [f"{'★ ' if d['is_auracast'] else ''}{d['name']}" for d in self._output_devices]
        self._output_menu["values"] = out_names

        dongle = next((d for d in self._output_devices if d["is_auracast"]), None)
        if dongle:
            self._output_menu.current(self._output_devices.index(dongle))
            self._dongle_label.config(text=f"✓ {dongle['name']}", fg=C["green"])
            self._log_msg(f"Auracast-dongle hittad: {dongle['name']}", "info")
        else:
            if out_names:
                self._output_menu.current(0)
            self._dongle_label.config(
                text="Ingen Auracast-dongle hittad\n(anslut FMA120 eller BTD 700)",
                fg=C["orange"],
            )
            self._log_msg("Ingen känd Auracast-dongle detekterad.", "warn")

    def _check_dongle_selection(self):
        sel = self.var_output_dev.get().lstrip("★ ")
        dev = next((d for d in self._output_devices if d["name"] == sel), None)
        if dev and dev["is_auracast"]:
            self._dongle_label.config(text=f"✓ {dev['name']}", fg=C["green"])
        elif dev:
            self._dongle_label.config(
                text=f"⚠ {dev['name']}\n(okänd Auracast-support)", fg=C["orange"]
            )

    def _selected_output_index(self):
        sel = self.var_output_dev.get().lstrip("★ ")
        dev = next((d for d in self._output_devices if d["name"] == sel), None)
        return dev["index"] if dev else None

    def _selected_input_index(self):
        sel = self.var_input_dev.get()
        dev = next((d for d in self._input_devices if d["name"] == sel), None)
        return dev["index"] if dev else None

    # ------------------------------------------------------------------
    # Start / stopp / volym  (allt via BroadcastManager)
    # ------------------------------------------------------------------

    def _start(self):
        if self._busy:
            return

        out_idx = self._selected_output_index()
        if out_idx is None:
            messagebox.showerror("Fel", "Ingen utgångsenhet vald.")
            return

        source = self.var_source.get()
        file_path = None
        if source == "file":
            file_path = self.var_file.get().strip()
            if not file_path or not Path(file_path).exists():
                messagebox.showerror("Fel", "Välj en giltig WAV-fil.")
                return

        in_idx = self._selected_input_index() if source == "mic" else None
        volume = self.var_volume.get()
        loop = self.var_loop.get()

        self._busy = True
        self._start_btn.config(state="disabled")
        self._log_msg("Startar sändning...", "dim")

        def run():
            result = self._manager.start(
                source=source,
                output_device=out_idx,
                input_device=in_idx,
                file_path=file_path,
                volume=volume,
                loop=loop,
            )
            self._queue.put(("start_result", result))

        threading.Thread(target=run, daemon=True).start()

    def _stop(self):
        if self._busy:
            return
        self._busy = True
        self._stop_btn.config(state="disabled")

        def run():
            self._manager.stop()
            self._queue.put(("stopped",))

        threading.Thread(target=run, daemon=True).start()

    def _on_volume_change(self, _val=None):
        pct = int(self.var_volume.get() * 100)
        self._vol_label.config(text=f"{pct}%")
        vol = self.var_volume.get()
        # set_volume tar managerns lås – kör i tråd så GUI:t aldrig fryser
        threading.Thread(target=lambda: self._manager.set_volume(vol), daemon=True).start()

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Välj WAV-fil",
            filetypes=[("WAV-filer", "*.wav"), ("Alla filer", "*.*")],
        )
        if path:
            self.var_file.set(path)

    def _on_source_change(self):
        mode = self.var_source.get()
        self._mic_frame.pack_forget()
        self._file_frame.pack_forget()
        if mode in ("mic", "system"):
            self._mic_frame.pack(fill="x", pady=(8, 0))
        else:
            self._file_frame.pack(fill="x", pady=(8, 0))

    # ------------------------------------------------------------------
    # Poll-loop: kö-händelser + status från managern
    # ------------------------------------------------------------------

    def _poll(self):
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "devices":
                    self._apply_devices(item[1])
                elif kind == "start_result":
                    self._busy = False
                    if not item[1]["ok"]:
                        self._log_msg(item[1]["error"], "error")
                        messagebox.showerror("Startfel", item[1]["error"])
                    else:
                        out = self.var_output_dev.get().lstrip("★ ")
                        self._log_msg(f"Sänder → {out}", "info")
                elif kind == "stopped":
                    self._busy = False
                    self._log_msg("Sändning stoppad.", "dim")
                elif kind == "log":
                    self._log_msg(item[1], item[2])
        except queue.Empty:
            pass

        # Spegla managerns status i UI:t
        status = self._manager.status()
        broadcasting = status["broadcasting"]
        self._vu_level = max(self._vu_level, status["level"])

        if broadcasting != self._was_broadcasting:
            self._was_broadcasting = broadcasting
            if not broadcasting and not self._busy:
                # T.ex. icke-loopad fil som spelat klart
                self._log_msg("Sändning avslutad.", "dim")

        if broadcasting:
            self._badge.config(text=" SÄNDER ", bg=C["green"])
            self._start_btn.config(state="disabled")
            self._stop_btn.config(state="normal" if not self._busy else "disabled")
        else:
            self._badge.config(text=" INAKTIV ", bg=C["dim"])
            self._start_btn.config(state="normal" if not self._busy else "disabled")
            self._stop_btn.config(state="disabled")

        self.after(100, self._poll)

    # ------------------------------------------------------------------
    # Logg + VU
    # ------------------------------------------------------------------

    def _log_msg(self, msg: str, tag: str = "info"):
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log.config(state="normal")
        self._log.insert("end", f"[{ts}] {msg}\n", tag)
        self._log.see("end")
        self._log.config(state="disabled")

    def _animate_vu(self):
        w = self._vu_canvas.winfo_width() or 200
        h = self._vu_canvas.winfo_height() or 12
        self._vu_canvas.delete("all")
        bar_w = int(w * self._vu_level)
        if bar_w > 0:
            color = C["green"] if self._vu_level < 0.7 else C["orange"] if self._vu_level < 0.9 else C["red"]
            self._vu_canvas.create_rectangle(0, 0, bar_w, h, fill=color, outline="")
        self._vu_level *= 0.75  # mjuk decay
        self.after(40, self._animate_vu)

    def destroy(self):
        # Stäng ev. pågående sändning när fönstret stängs
        try:
            self._manager.stop()
        except Exception:
            pass
        super().destroy()


# ------------------------------------------------------------------
# Start
# ------------------------------------------------------------------

def _check_deps():
    missing = []
    for pkg in ["sounddevice", "numpy"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"Saknade paket: {', '.join(missing)}")
        print(f"Kör: pip install {' '.join(missing)}")
        sys.exit(1)


def main():
    if sys.version_info < (3, 9):
        print("Python 3.9+ krävs.")
        sys.exit(1)
    _check_deps()
    app = AuracastApp()
    app.mainloop()


if __name__ == "__main__":
    main()
