import tkinter as tk
import tkinter.ttk as ttk
import tkinter.font as tkfont
import time
import platform
import os
import ctypes
import json
import threading
import importlib
import psutil
import subprocess
import winreg
try:
    import pynvml as nvml
    NVML_AVAILABLE = True
except Exception:
    NVML_AVAILABLE = False

# System tray dependencies will be imported lazily within tray startup

UPDATE_INTERVAL = 1000  # ms


class SystemMonitor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("System Monitor Overlay")
        self.transparent_color = "#000000"
        self.configure(bg=self.transparent_color)
        if platform.system() == "Windows":
            try:
                self.attributes("-transparentcolor", self.transparent_color)
            except Exception:
                pass
        self.overrideredirect(True)
        self.tray_icon = None
        self.tray_thread = None
        self.settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
        self.settings = self._load_settings()
        self.update_interval = int(self.settings.get("update_interval", 1000))
        self.audio_update_interval = int(self.settings.get("audio_update_interval", 50))
        self.pause_on_foreground = bool(self.settings.get("pause_on_foreground", True))
        self.paused = False
        self.margin_x = int(self.settings.get("margin_x", 12))
        self.margin_y = int(self.settings.get("margin_y", 12))
        self.text_align = self.settings.get("text_align", "left")
        self.audio_enabled = bool(self.settings.get("audio_enabled", True))
        self.audio_available = False
        self.audio_metrics = {"volume": 0.0, "bass": 0.0, "mid": 0.0, "treble": 0.0}
        self.audio_stop_event = threading.Event()
        self.audio_device = ""  # human-readable selected device name
        # Collect available font families for dropdown
        try:
            self.font_families = sorted([str(f) for f in tkfont.families()])
        except Exception:
            self.font_families = ["Consolas", "Segoe UI", "Arial"]
        self.monitors = self._get_monitors_windows() if platform.system() == "Windows" else [
            {"name": "Primary", "x": 0, "y": 0, "w": self.winfo_screenwidth(), "h": self.winfo_screenheight(), "primary": True}
        ]
        self.monitor_device = self.settings.get("monitor_device")
        if not self._find_monitor_by_name(self.monitor_device):
            primary = next((m for m in self.monitors if m.get("primary")), None)
            fallback = primary or (self.monitors[0] if self.monitors else None)
            self.monitor_device = fallback["name"] if isinstance(fallback, dict) else None

        self.labels = {}
        # Content container placed in a corner based on alignment
        self.container = tk.Frame(self, bg=self.transparent_color)
        # Fixed character width for audio metric labels to align bars
        self.audio_label_width = max(len("Audio Volume:"), len("Audio Bass:"), len("Audio Mid:"), len("Audio Treble:"))

        for key in [
            "OS", "Uptime", "CPU Model", "CPU Usage",
            "Memory Usage", "Disk Usage",
            "GPU Name", "GPU Usage", "GPU Memory", "GPU Temp",
            "Audio Source",
            "Audio Volume", "Audio Bass", "Audio Mid", "Audio Treble"
        ]:
            # Create paired labels for audio metrics so only the bar changes color
            if key in ("Audio Volume", "Audio Bass", "Audio Mid", "Audio Treble"):
                row = tk.Frame(self.container, bg=self.transparent_color)
                row.pack(fill="x", padx=10, pady=2)

                static_lbl = tk.Label(
                    row,
                    text=f"{key}:",
                    anchor=self._text_anchor(),
                    font=(self.settings.get("font_family", "Consolas"), int(self.settings.get("font_size", 10))),
                    bg=self.transparent_color,
                    fg=self.settings.get("text_color", "white"),
                    width=self.audio_label_width,
                )
                static_lbl.pack(side="left")

                bar_lbl = tk.Label(
                    row,
                    text="",
                    anchor="w",
                    font=(self.settings.get("font_family", "Consolas"), int(self.settings.get("font_size", 10))),
                    bg=self.transparent_color,
                    fg=self.settings.get("text_color", "white"),
                )
                bar_lbl.pack(side="left", padx=(6, 0))

                self.labels[key] = static_lbl
                self.labels[f"{key} Bar"] = bar_lbl
            else:
                lbl = tk.Label(
                    self.container,
                    text="",
                    anchor=self._text_anchor(),
                    font=(self.settings.get("font_family", "Consolas"), int(self.settings.get("font_size", 10))),
                    bg=self.transparent_color,
                    fg=self.settings.get("text_color", "white"),
                )
                lbl.pack(fill="x", padx=10, pady=2)
                self.labels[key] = lbl

        # NVML setup (safe init)
        self.gpu_available = False
        self.gpu_handle = None
        if NVML_AVAILABLE:
            try:
                nvml.nvmlInit()
                self.gpu_handle = nvml.nvmlDeviceGetHandleByIndex(0)  # GPU #0
                self.gpu_available = True
                gpu_name = nvml.nvmlDeviceGetName(self.gpu_handle)
                if isinstance(gpu_name, bytes):
                    gpu_name = gpu_name.decode("utf-8", errors="replace")
                self.labels["GPU Name"].config(text=f"GPU: {gpu_name}")
            except Exception as e:
                self.labels["GPU Name"].config(text=f"GPU: Not available ({e.__class__.__name__})")
                self.gpu_available = False
        else:
            self.labels["GPU Name"].config(text="GPU: Not available (NVML not installed)")

        self.update_stats()

        # Embed behind desktop icons on Windows
        if platform.system() == "Windows":
            self.update_idletasks()
            self._embed_on_windows_desktop()
        else:
            # Size to primary screen for non-Windows
            width, height = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f"{width}x{height}+0+0")

        # Size to selected monitor region
        self._apply_monitor_geometry()

        # Place content according to settings
        self.update_idletasks()
        self._update_layout()

        # Start system tray (no-op if dependencies missing)
        self._start_tray()

        # Start audio visualizer capture
        if self.audio_enabled:
            self._start_audio_capture()
            # Fast refresh loop for audio labels separate from system stats
            self.after(self.audio_update_interval, self.update_audio_labels)

        # Foreground watcher
        if platform.system() == "Windows":
            self.after(250, self._check_pause_state)

        # Ensure NVML shuts down when window closes
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):
        if self.gpu_available:
            try:
                nvml.nvmlShutdown()
            except Exception:
                pass
        try:
            if self.tray_icon is not None:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.audio_stop_event.set()
        except Exception:
            pass
        self.destroy()

    def _embed_on_windows_desktop(self):
        # Reparent the Tk window to the WorkerW behind the desktop icons
        user32 = ctypes.windll.user32

        hwnd_progman = user32.FindWindowW("Progman", "Program Manager")
        result = ctypes.c_ulong()
        user32.SendMessageTimeoutW(hwnd_progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(result))

        found_hwnd = ctypes.c_void_p(0)

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def enum_windows_proc(hwnd, lparam):
            # Look for SHELLDLL_DefView; then get the WorkerW behind it
            shellview = user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
            if shellview:
                workerw = user32.FindWindowExW(0, hwnd, "WorkerW", None)
                if workerw:
                    found_hwnd.value = workerw
                    return False  # stop enumeration
            return True

        user32.EnumWindows(WNDENUMPROC(enum_windows_proc), 0)

        workerw_hwnd = found_hwnd.value
        if workerw_hwnd:
            tk_hwnd = self.winfo_id()
            user32.SetParent(tk_hwnd, workerw_hwnd)
            # Cover the full virtual desktop across monitors
            SM_XVIRTUALSCREEN = 76
            SM_YVIRTUALSCREEN = 77
            SM_CXVIRTUALSCREEN = 78
            SM_CYVIRTUALSCREEN = 79
            x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
            y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
            h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
            self.geometry(f"{w}x{h}+{x}+{y}")
        else:
            # Fallback to primary screen size
            width, height = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f"{width}x{height}+0+0")

    def _get_monitors_windows(self):
        user32 = ctypes.windll.user32
        monitors = []

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_uint),
                ("szDevice", ctypes.c_wchar * 32),
            ]

        handles = []
        MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p)

        def _enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
            handles.append(hMonitor)
            return 1

        try:
            user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_enum_proc), 0)
            for h in handles:
                info = MONITORINFOEXW()
                info.cbSize = ctypes.sizeof(MONITORINFOEXW)
                if user32.GetMonitorInfoW(h, ctypes.byref(info)):
                    x = info.rcMonitor.left
                    y = info.rcMonitor.top
                    w = info.rcMonitor.right - info.rcMonitor.left
                    hgt = info.rcMonitor.bottom - info.rcMonitor.top
                    name = info.szDevice
                    primary = bool(info.dwFlags & 1)
                    monitors.append({"name": name, "x": x, "y": y, "w": w, "h": hgt, "primary": primary})
        except Exception:
            # Fallback to virtual screen
            x = user32.GetSystemMetrics(76)
            y = user32.GetSystemMetrics(77)
            w = user32.GetSystemMetrics(78)
            h = user32.GetSystemMetrics(79)
            monitors.append({"name": "VirtualScreen", "x": x, "y": y, "w": w, "h": h, "primary": True})
        return monitors

    def _find_monitor_by_name(self, name):
        return next((m for m in self.monitors if m.get("name") == name), None)

    def _get_target_monitor(self):
        return self._find_monitor_by_name(self.monitor_device) or (self.monitors[0] if self.monitors else None)

    def _find_monitor_by_point(self, x, y):
        try:
            for m in self.monitors:
                mx, my, mw, mh = int(m.get("x", 0)), int(m.get("y", 0)), int(m.get("w", 0)), int(m.get("h", 0))
                if (mx <= x < mx + mw) and (my <= y < my + mh):
                    return m
        except Exception:
            pass
        return None

    def _apply_monitor_geometry(self):
        mon = self._get_target_monitor()
        if mon:
            self.geometry(f"{mon['w']}x{mon['h']}+{mon['x']}+{mon['y']}")

    def _update_layout(self):
        # Position the content container to the requested corner
        anchor = self.settings.get("anchor", "top-left")
        self.update_idletasks()
        req_w = self.container.winfo_reqwidth()
        req_h = self.container.winfo_reqheight()
        # Use root window client size; placement is relative to window origin
        w = self.winfo_width()
        h = self.winfo_height()

        if anchor == "top-left":
            x = self.margin_x
            y = self.margin_y
            self.container.place(x=x, y=y)
        elif anchor == "top-right":
            x = w - req_w - self.margin_x
            y = self.margin_y
            self.container.place(x=x, y=y)
        elif anchor == "bottom-left":
            x = self.margin_x
            y = h - req_h - self.margin_y - 50
            self.container.place(x=x, y=y)
        elif anchor == "bottom-right":
            x = w - req_w - self.margin_x
            y = h - req_h - self.margin_y - 50
            self.container.place(x=x, y=y)
        else:
            self.container.place(x=self.margin_x, y=self.margin_y)

    def _anchor_to_tk(self, anchor):
        return "e" if anchor in ("top-right", "bottom-right") else "w"

    def _text_anchor(self):
        return "e" if str(self.text_align).lower() == "right" else "w"

    def _load_settings(self):
        defaults = {
            "font_family": "Consolas",
            "font_size": 12,
            "text_color": "white",
            "update_interval": 1000,
            "audio_update_interval": 50,
            "pause_on_foreground": True,
            "anchor": "top-left",
            "text_align": "left"
        }
        try:
            if os.path.exists(self.settings_path):
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    defaults.update({k: v for k, v in data.items() if v is not None})
        except Exception:
            pass
        return defaults

    def _apply_settings(self):
        self.update_interval = int(self.settings.get("update_interval", 1000))
        self.audio_update_interval = int(self.settings.get("audio_update_interval", 50))
        self.pause_on_foreground = bool(self.settings.get("pause_on_foreground", True))
        anchor = self._text_anchor()
        font_family = self.settings.get("font_family", "Consolas")
        font_size = int(self.settings.get("font_size", 12))
        text_color = self.settings.get("text_color", "white")
        self.margin_x = int(self.settings.get("margin_x", 12))
        self.margin_y = int(self.settings.get("margin_y", 12))
        self.text_align = self.settings.get("text_align", "left")
        self.audio_enabled = bool(self.settings.get("audio_enabled", True))
        if self.audio_enabled and not self.audio_available:
            self._start_audio_capture()
        elif not self.audio_enabled and self.audio_available:
            try:
                self.audio_stop_event.set()
            except Exception:
                pass
        for lbl in self.labels.values():
            lbl.config(anchor=anchor, font=(font_family, font_size), fg=text_color, bg=self.transparent_color)
        self._apply_monitor_geometry()
        self._update_layout()

    def _start_tray(self):
        try:
            pystray = importlib.import_module("pystray")
            Image = importlib.import_module("PIL.Image")
            ImageDraw = importlib.import_module("PIL.ImageDraw")
        except Exception:
            return

        def _create_image():
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.ellipse((8, 8, 56, 56), outline=(255, 255, 255, 200), width=3)
            d.line((20, 32, 44, 32), fill=(0, 200, 0, 220), width=4)
            d.line((32, 20, 32, 44), fill=(0, 200, 0, 220), width=4)
            return img

        def open_settings_action(icon, item):
            self.after(0, self.open_settings_panel)

        def exit_action(icon, item):
            self.after(0, self.on_close)

        menu = pystray.Menu(
            pystray.MenuItem("Open Settings", open_settings_action),
            pystray.MenuItem("Exit", exit_action)
        )
        icon = pystray.Icon("System Monitor", _create_image(), "System Monitor", menu)
        self.tray_icon = icon

        def run_icon():
            try:
                icon.run()
            except Exception:
                pass

        self.tray_thread = threading.Thread(target=run_icon, daemon=True)
        self.tray_thread.start()

    def open_settings_panel(self):
        if getattr(self, "_settings_win", None) and tk.Toplevel.winfo_exists(self._settings_win):
            self._settings_win.lift()
            return
        win = tk.Toplevel(self)
        self._settings_win = win
        win.title("Overlay Settings")
        win.attributes("-topmost", True)

        font_family_var = tk.StringVar(value=self.settings.get("font_family", "Consolas"))
        font_size_var = tk.IntVar(value=int(self.settings.get("font_size", 12)))
        text_color_var = tk.StringVar(value=self.settings.get("text_color", "white"))
        update_interval_var = tk.IntVar(value=int(self.settings.get("update_interval", 1000)))
        audio_update_interval_var = tk.IntVar(value=int(self.settings.get("audio_update_interval", 50)))
        anchor_var = tk.StringVar(value=self.settings.get("anchor", "top-left"))
        text_align_var = tk.StringVar(value=self.settings.get("text_align", "left"))
        audio_enabled_var = tk.BooleanVar(value=bool(self.settings.get("audio_enabled", True)))
        pause_on_foreground_var = tk.BooleanVar(value=bool(self.settings.get("pause_on_foreground", True)))
        margin_x_var = tk.IntVar(value=int(self.settings.get("margin_x", 12)))
        margin_y_var = tk.IntVar(value=int(self.settings.get("margin_y", 12)))
        monitor_names = [str(m.get("name")) for m in self.monitors] or ["Primary"]
        current_monitor = self.monitor_device if (self.monitor_device in monitor_names) else monitor_names[0]
        monitor_var = tk.StringVar(value=str(current_monitor))

        row = 0
        tk.Label(win, text="Font Family:").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        fonts = self.font_families or ["Consolas"]
        if font_family_var.get() not in fonts:
            font_family_var.set(fonts[0])
        font_combo = ttk.Combobox(win, textvariable=font_family_var, values=fonts, state="normal")
        font_combo.grid(row=row, column=1, sticky="ew", padx=8, pady=6)

        def _filter_fonts(event=None):
            query = (font_family_var.get() or "").lower().strip()
            filtered = [f for f in self.font_families if query in f.lower()] if query else self.font_families
            font_combo.configure(values=filtered)

        font_combo.bind("<KeyRelease>", _filter_fonts)
        row += 1

        tk.Label(win, text="Font Size:").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        tk.Spinbox(win, from_=8, to=48, textvariable=font_size_var).grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        row += 1

        tk.Label(win, text="Text Color:").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        tk.Entry(win, textvariable=text_color_var).grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        row += 1

        tk.Label(win, text="Update Interval (ms):").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        tk.Spinbox(win, from_=250, to=10000, increment=250, textvariable=update_interval_var).grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        row += 1

        tk.Label(win, text="Audio Update Interval (ms):").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        tk.Spinbox(win, from_=20, to=1000, increment=10, textvariable=audio_update_interval_var).grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        row += 1

        tk.Label(win, text="Alignment:").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        tk.OptionMenu(win, anchor_var, "top-left", "top-right", "bottom-left", "bottom-right").grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        row += 1

        tk.Label(win, text="Text Align:").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        tk.OptionMenu(win, text_align_var, "left", "right").grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        row += 1

        tk.Label(win, text="Audio Visualizer:").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(win, variable=audio_enabled_var, text="Enabled").grid(row=row, column=1, sticky="w", padx=8, pady=6)
        row += 1

        tk.Label(win, text="Pause On Fullscreen Apps:").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(win, variable=pause_on_foreground_var, text="Enabled").grid(row=row, column=1, sticky="w", padx=8, pady=6)
        row += 1

        tk.Label(win, text="Target Monitor:").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        tk.OptionMenu(win, monitor_var, *monitor_names).grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        row += 1

        tk.Label(win, text="Margin X:").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        tk.Spinbox(win, from_=0, to=100, textvariable=margin_x_var).grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        row += 1

        tk.Label(win, text="Margin Y:").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        tk.Spinbox(win, from_=0, to=100, textvariable=margin_y_var).grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        row += 1

        btn_frame = tk.Frame(win)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=12)

        def save_and_apply():
            self.settings.update({
                "font_family": font_family_var.get(),
                "font_size": int(font_size_var.get()),
                "text_color": text_color_var.get(),
                "update_interval": int(update_interval_var.get()),
                "audio_update_interval": int(audio_update_interval_var.get()),
                "pause_on_foreground": bool(pause_on_foreground_var.get()),
                "anchor": anchor_var.get(),
                "text_align": text_align_var.get(),
                "audio_enabled": bool(audio_enabled_var.get()),
                "margin_x": int(margin_x_var.get()),
                "margin_y": int(margin_y_var.get()),
                "monitor_device": monitor_var.get()
            })
            try:
                with open(self.settings_path, "w", encoding="utf-8") as f:
                    json.dump(self.settings, f, indent=2)
            except Exception:
                pass
            self.monitor_device = monitor_var.get()
            self._apply_settings()

        tk.Button(btn_frame, text="Save", command=save_and_apply).pack(side="left", padx=6)
        tk.Button(btn_frame, text="Close", command=win.destroy).pack(side="left", padx=6)
        win.columnconfigure(1, weight=1)

    def format_uptime(self):
        uptime_seconds = int(time.time() - psutil.boot_time())
        hrs, rem = divmod(uptime_seconds, 3600)
        mins, secs = divmod(rem, 60)
        return f"{hrs}h {mins}m {secs}s"
    
    def get_cpu_model(self):
        system = platform.system()

        if system == "Windows":
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
                )
                name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                return name
            except Exception:
                return "Unknown CPU"

        elif system == "Linux":
            try:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if "model name" in line:
                            return line.split(":")[1].strip()
            except Exception:
                return "Unknown CPU"

        elif system == "Darwin":
            try:
                return subprocess.check_output(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    text=True
                ).strip()
            except Exception:
                return "Unknown CPU"

        return "Unsupported OS"
    

    def update_stats(self):
        # If paused, skip heavy updates but keep scheduling
        if self.pause_on_foreground and self.paused:
            self.after(self.update_interval, self.update_stats)
            return
        # OS
        find_OS = platform.version()
        if platform.system() == "Windows":
            if find_OS > "10.0.22000":
                true_OS = "11"
            elif find_OS > "10.0.19044":
                true_OS = "10"
            self.labels["OS"].config(text=f"OS: {platform.system()} {true_OS}")
        else:
            self.labels["OS"].config(text=f"OS: {platform.system()} {platform.release()}")

        # Uptime
        self.labels["Uptime"].config(text=f"Uptime: {self.format_uptime()}")

        # CPU
        #self.labels["CPU Model"].config(text=f"CPU Model: {cpuinfo.get_cpu_info().get('brand_raw', 'Unknown')}")
        self.labels["CPU Model"].config(text=f"CPU Model: {self.get_cpu_model()}")
        self.labels["CPU Usage"].config(text=f"CPU Usage: {psutil.cpu_percent()}%")

        # RAM
        mem = psutil.virtual_memory()
        mem_total = psutil.virtual_memory().total // (1024**2)
        self.labels["Memory Usage"].config(
            text=f"Memory Usage: {mem.percent}% ({mem.used // (1024**2)} / {mem_total} MB used)"
        )

        # Disk
        system_drive = os.environ.get("SystemDrive", "C:")
        disk = psutil.disk_usage(f"{system_drive}\\")
        self.labels["Disk Usage"].config(text=f"Disk Usage: {disk.percent}%")

        # GPU (NVIDIA)
        if self.gpu_available and self.gpu_handle is not None:
            try:
                util = nvml.nvmlDeviceGetUtilizationRates(self.gpu_handle)
                meminfo = nvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
                temp = nvml.nvmlDeviceGetTemperature(self.gpu_handle, nvml.NVML_TEMPERATURE_GPU)

                used_bytes = int(getattr(meminfo, "used", 0) or 0)
                total_bytes = int(getattr(meminfo, "total", 0) or 0)
                used_mb = float(used_bytes) / (1024**2)
                total_mb = float(total_bytes) / (1024**2)
                mem_pct = (used_bytes / total_bytes * 100) if total_bytes else 0

                self.labels["GPU Usage"].config(text=f"GPU Usage: {util.gpu}%  |  Mem Ctrl: {util.memory}%")
                self.labels["GPU Memory"].config(
                    text=f"GPU Memory: {mem_pct:.1f}% ({used_mb:.0f} / {total_mb:.0f} MB)"
                )
                self.labels["GPU Temp"].config(text=f"GPU Temp: {temp}°C")

            except Exception as e:
                self.labels["GPU Usage"].config(text=f"GPU Usage: Error ({e.__class__.__name__})")
                self.labels["GPU Memory"].config(text="")
                self.labels["GPU Temp"].config(text="")

        else:
            self.labels["GPU Usage"].config(text="GPU Usage: N/A")
            self.labels["GPU Memory"].config(text="GPU Memory: N/A")
            self.labels["GPU Temp"].config(text="GPU Temp: N/A")

        # Audio labels are refreshed separately in `update_audio_labels`

        # Schedule next update (non-blocking)
        self.after(self.update_interval, self.update_stats)

    def _ascii_bar(self, value, width=30):
        try:
            v = max(0.0, min(1.0, float(value)))
        except Exception:
            v = 0.0
        filled = int(round(v * width))
        return "|" * filled + "-" * (width - filled)

    def _level_color(self, value):
        try:
            v = max(0.0, min(1.0, float(value)))
        except Exception:
            v = 0.0
        if v <= 0.5:
            t = v / 0.5  # 0..1
            r = int(255 * t)
            g = 255
        else:
            t = (v - 0.5) / 0.5  # 0..1
            r = 255
            g = int(255 * (1.0 - t))
        return f"#{r:02x}{g:02x}00"

    def update_audio_labels(self):
        """Fast refresh loop for audio visualizer labels."""
        try:
            if self.pause_on_foreground and self.paused:
                # Keep labels unchanged while paused
                pass
            elif self.audio_enabled and self.audio_available:
                vol = self.audio_metrics.get("volume", 0.0)
                bass = self.audio_metrics.get("bass", 0.0)
                mid = self.audio_metrics.get("mid", 0.0)
                treble = self.audio_metrics.get("treble", 0.0)
                self.labels["Audio Source"].config(text=f"Audio Source: {self.audio_device or 'Unknown'}")
                default_color = self.settings.get("text_color", "white")
                # Keep static labels in default color; colorize only the bar labels
                self.labels["Audio Volume"].config(text="Audio Volume:", fg=default_color)
                self.labels["Audio Volume Bar"].config(text=self._ascii_bar(vol), fg=self._level_color(vol))

                self.labels["Audio Bass"].config(text="Audio Bass:", fg=default_color)
                self.labels["Audio Bass Bar"].config(text=self._ascii_bar(bass), fg=self._level_color(bass))

                self.labels["Audio Mid"].config(text="Audio Mid:", fg=default_color)
                self.labels["Audio Mid Bar"].config(text=self._ascii_bar(mid), fg=self._level_color(mid))

                self.labels["Audio Treble"].config(text="Audio Treble:", fg=default_color)
                self.labels["Audio Treble Bar"].config(text=self._ascii_bar(treble), fg=self._level_color(treble))
            else:
                default_color = self.settings.get("text_color", "white")
                self.labels["Audio Source"].config(text="Audio Source: N/A")
                # Static labels default color; bars show N/A in default color
                self.labels["Audio Volume"].config(text="Audio Volume:", fg=default_color)
                self.labels["Audio Volume Bar"].config(text="N/A", fg=default_color)

                self.labels["Audio Bass"].config(text="Audio Bass:", fg=default_color)
                self.labels["Audio Bass Bar"].config(text="N/A", fg=default_color)

                self.labels["Audio Mid"].config(text="Audio Mid:", fg=default_color)
                self.labels["Audio Mid Bar"].config(text="N/A", fg=default_color)

                self.labels["Audio Treble"].config(text="Audio Treble:", fg=default_color)
                self.labels["Audio Treble Bar"].config(text="N/A", fg=default_color)
        finally:
            try:
                self.after(self.audio_update_interval, self.update_audio_labels)
            except Exception:
                pass

    def _check_pause_state(self):
        """Pause only when a fullscreen app is focused on the overlay's monitor (Windows)."""
        try:
            if not self.pause_on_foreground or platform.system() != "Windows":
                self.paused = False
            else:
                user32 = ctypes.windll.user32
                hwnd = user32.GetForegroundWindow()
                # If our own window is focused, do not pause
                if hwnd and hwnd == self.winfo_id():
                    self.paused = False
                else:
                    # Determine the monitor of the foreground window by its center point
                    class RECT(ctypes.Structure):
                        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                    r = RECT()
                    if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                        self.paused = False
                    else:
                        cx = int((r.left + r.right) / 2)
                        cy = int((r.top + r.bottom) / 2)
                        window_mon = self._find_monitor_by_point(cx, cy)
                        target_mon = self._get_target_monitor()
                        if window_mon and target_mon and window_mon.get("name") == target_mon.get("name"):
                            self.paused = self._is_window_fullscreen(hwnd)
                        else:
                            # Fullscreen on a different monitor should not pause
                            self.paused = False
        except Exception:
            self.paused = False
        finally:
            try:
                self.after(250, self._check_pause_state)
            except Exception:
                pass

    def _is_window_fullscreen(self, hwnd):
        """Return True if the window covers the monitor (fullscreen)."""
        try:
            if not hwnd:
                return False
            user32 = ctypes.windll.user32
            MONITOR_DEFAULTTONEAREST = 2
            monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            if not monitor:
                return False

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_uint)]

            r = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return False

            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
                return False

            margin = 8  # tolerance for borders/taskbar

            def covers(rect, bounds):
                return (
                    abs(rect.left - bounds.left) <= margin and
                    abs(rect.top - bounds.top) <= margin and
                    abs(rect.right - bounds.right) <= margin and
                    abs(rect.bottom - bounds.bottom) <= margin
                )

            return covers(r, mi.rcMonitor) or covers(r, mi.rcWork)
        except Exception:
            return False

    def _start_audio_capture(self):
        if self.audio_available:
            return
        # Prefer PyAudio WASAPI loopback for output-only capture, fallback to soundcard
        has_sc = False
        try:
            pyaudio = importlib.import_module("pyaudiowpatch")
            np = importlib.import_module("numpy")
            use_pyaudio = True
        except Exception:
            use_pyaudio = False
            try:
                sc = importlib.import_module("soundcard")
                np = importlib.import_module("numpy")
                has_sc = True
            except Exception:
                self.audio_available = False
                return

        self.audio_available = True

        def worker():
            frames = 2048
            smoothing = 0.6
            vol_ema = bass_ema = mid_ema = treble_ema = 0.0

            if use_pyaudio:
                pa = pyaudio.PyAudio()
                stream = None
                rate = 48000
                try:
                    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
                    ha_index = wasapi.get("index")

                    # Find loopback twin of default output; else first loopback
                    try:
                        default_out = wasapi.get("defaultOutputDevice")
                    except Exception:
                        default_out = None
                    base_name = ""
                    if default_out is not None:
                        try:
                            base = pa.get_device_info_by_index(default_out)
                            base_name = base.get("name") or ""
                        except Exception:
                            base_name = ""

                    dev_index = None
                    dev_info = None
                    for i in range(pa.get_device_count()):
                        d = pa.get_device_info_by_index(i)
                        if d.get("hostApi") == ha_index and bool(d.get("isLoopbackDevice")):
                            if base_name and base_name in (d.get("name") or ""):
                                dev_index, dev_info = i, d
                                break
                            if dev_index is None:
                                dev_index, dev_info = i, d

                    if dev_index is None or dev_info is None:
                        raise RuntimeError("No WASAPI loopback device found")

                    rate = int(dev_info.get("defaultSampleRate", 48000) or 48000)
                    channels = max(1, int(dev_info.get("maxInputChannels", 2) or 2))

                    stream = pa.open(
                        format=pyaudio.paInt16,
                        channels=channels,
                        rate=rate,
                        input=True,
                        frames_per_buffer=frames,
                        input_device_index=dev_index,
                    )
                    self.audio_device = f"{dev_info.get('name', 'Output')} "
                    self.audio_device = self.audio_device.replace("[Loopback]", "").strip()

                    while not self.audio_stop_event.is_set():
                        try:
                            if getattr(self, "pause_on_foreground", False) and getattr(self, "paused", False):
                                time.sleep(0.1)
                                continue
                            data = stream.read(frames)
                            if not data:
                                continue
                            arr_i16 = np.frombuffer(data, dtype=np.int16)
                            if arr_i16.size == 0:
                                continue
                            arr = arr_i16.astype(np.float32) / 32768.0
                            if channels > 1:
                                arr = arr.reshape(-1, channels).mean(axis=1)
                            if arr.ndim == 2:
                                arr = arr.mean(axis=1)
                            if arr.size:
                                arr = arr * np.hanning(arr.size)

                            rms = float(np.sqrt(np.mean(arr ** 2)))
                            vol_ema = smoothing * vol_ema + (1.0 - smoothing) * min(1.0, rms * 10.0)

                            fft = np.fft.rfft(arr)
                            freqs = np.fft.rfftfreq(arr.size, d=1.0 / rate)
                            mag2 = np.abs(fft) ** 2

                            def band_power(f_lo, f_hi):
                                idx = (freqs >= f_lo) & (freqs < f_hi)
                                return float(mag2[idx].sum()) if idx.any() else 0.0

                            bass = band_power(20, 250)
                            mid = band_power(250, 2000)
                            treble = band_power(2000, 16000)

                            total = float(mag2.sum()) or 1.0
                            bass_n = bass / total
                            mid_n = mid / total
                            treble_n = treble / total

                            bass_ema = smoothing * bass_ema + (1.0 - smoothing) * min(1.0, bass_n * 8.0)
                            mid_ema = smoothing * mid_ema + (1.0 - smoothing) * min(1.0, mid_n * 8.0)
                            treble_ema = smoothing * treble_ema + (1.0 - smoothing) * min(1.0, treble_n * 8.0)

                            self.audio_metrics = {
                                "volume": vol_ema,
                                "bass": bass_ema,
                                "mid": mid_ema,
                                "treble": treble_ema,
                            }
                        except Exception:
                            continue
                except Exception:
                    # Fallback to soundcard below
                    try:
                        if stream:
                            stream.stop_stream()
                            stream.close()
                    except Exception:
                        pass
                    try:
                        pa.terminate()
                    except Exception:
                        pass
                    # proceed to soundcard fallback
                else:
                    # Ensure stream closed on exit
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
                    try:
                        pa.terminate()
                    except Exception:
                        pass
                    return

            # soundcard fallback
            if not has_sc:
                # Neither backend available
                self.audio_available = False
                return

            samplerate = 48000
            frames = 2048
            try:
                speaker = sc.default_speaker()
                recorder = None
                if speaker:
                    loopback_mic = sc.Microphone(id=speaker.id, include_loopback=True)
                    recorder = loopback_mic.recorder(samplerate=samplerate)
                    self.audio_device = f"{speaker.name} (loopback)"
                if recorder is None:
                    # Try any loopback microphone
                    mics = sc.all_microphones(include_loopback=True)
                    loopbacks = [m for m in mics if getattr(m, 'isloopback', False) or 'loopback' in (m.name or '').lower()]
                    target = loopbacks[0] if loopbacks else None
                    if target:
                        recorder = target.recorder(samplerate=samplerate)
                        self.audio_device = f"{target.name}"
                if recorder is None:
                    self.audio_available = False
                    return

                with recorder:
                    while not self.audio_stop_event.is_set():
                        try:
                            if getattr(self, "pause_on_foreground", False) and getattr(self, "paused", False):
                                time.sleep(0.1)
                                continue
                            data = recorder.record(numframes=frames)
                            if data is None:
                                continue
                            arr = np.asarray(data, dtype=np.float32)
                            if arr.ndim == 2:
                                arr = arr.mean(axis=1)
                            if arr.size:
                                arr = arr * np.hanning(arr.size)
                            rms = float(np.sqrt(np.mean(arr**2)))
                            vol_ema = smoothing * vol_ema + (1.0 - smoothing) * min(1.0, rms * 10.0)

                            fft = np.fft.rfft(arr)
                            freqs = np.fft.rfftfreq(arr.size, d=1.0 / samplerate)
                            mag = np.abs(fft)

                            def band_power(f_lo, f_hi):
                                idx = (freqs >= f_lo) & (freqs < f_hi)
                                return float((mag[idx]**2).sum()) if idx.any() else 0.0

                            bass = band_power(20, 250)
                            mid = band_power(250, 2000)
                            treble = band_power(2000, 16000)

                            total = float((mag**2).sum()) or 1.0
                            bass_n = bass / total
                            mid_n = mid / total
                            treble_n = treble / total

                            bass_ema = smoothing * bass_ema + (1.0 - smoothing) * min(1.0, bass_n * 8.0)
                            mid_ema = smoothing * mid_ema + (1.0 - smoothing) * min(1.0, mid_n * 8.0)
                            treble_ema = smoothing * treble_ema + (1.0 - smoothing) * min(1.0, treble_n * 8.0)

                            self.audio_metrics = {
                                "volume": vol_ema,
                                "bass": bass_ema,
                                "mid": mid_ema,
                                "treble": treble_ema,
                            }
                        except Exception:
                            continue
            except Exception:
                self.audio_available = False
                return

        t = threading.Thread(target=worker, daemon=True)
        t.start()


if __name__ == "__main__":
    app = SystemMonitor()
    app.mainloop()
