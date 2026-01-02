# System Info Displayer (Windows Overlay)

A lightweight, always-on, click-through desktop overlay that displays live system stats with an optional audio visualizer. Designed for Windows and optimized to sit behind desktop icons while remaining visible during normal desktop usage.

The overlay shows:

- OS and uptime
- CPU model and usage
- Memory usage (with used/total MB)
- System drive disk usage
- NVIDIA GPU metrics (usage, memory, temperature) via NVML when available
- Audio visualizer (Volume, Bass, Mid, Treble) from system output via WASAPI loopback


## Features

- Transparent overlay window (Tkinter) embedded behind desktop icons on Windows.
- System tray icon with quick actions and a settings panel.
- Multi-monitor support; choose target monitor and corner placement with margins.
- Font, size, color, alignment, and refresh rate customization.
- Optional pause while a fullscreen app is focused on the same monitor.
- Audio visualizer using `pyaudiowpatch` (WASAPI loopback), with `soundcard` fallback.
- Graceful degradation: if NVML or loopback audio aren’t available, related lines display N/A.


## Requirements

- OS: Windows 10/11 recommended.
- Python: 3.10+ (3.11 tested).
- Dependencies: see requirements file.

Install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Notes:
- NVIDIA GPU metrics require a supported NVIDIA driver and the `nvidia-ml-py` package (installed via `requirements.txt`).
- Audio visualizer requires `pyaudiowpatch` on Windows (installed via `requirements.txt`). If unavailable, the app attempts a `soundcard` loopback fallback when possible.


## Run From Source

```powershell
python main.py
```

On startup, the overlay embeds behind desktop icons (Windows). A system tray icon appears; use it to open settings or exit.


## Configuration

Settings are stored alongside the app in `settings.json` and can be adjusted via the in-app Settings panel (tray → Open Settings) or by editing the file directly. Supported keys:

```json
{
	"font_family": "Cascadia Code",
	"font_size": 12,
	"text_color": "white",
	"update_interval": 1000,
	"audio_update_interval": 50,
	"pause_on_foreground": true,
	"anchor": "top-left",        // one of: top-left, top-right, bottom-left, bottom-right
	"text_align": "left",        // left | right (text alignment within lines)
	"margin_x": 12,
	"margin_y": 12,
	"monitor_device": "\\\\.\\DISPLAY1", // Windows display device name
	"audio_enabled": true
}
```

Tip: You can also change these in the Settings panel; saving will write them back to `settings.json`.


## System Tray

- Open Settings: adjust visual style, placement, refresh rates, audio, and target monitor.
- Exit: cleanly shuts down the overlay and NVML/audio capture if active.


## Build a Windows EXE (PyInstaller)

This project includes a ready-made spec file. The generated executable will be at:

- `dist/system_info_displayer/system_info_displayer.exe`

Build with the provided spec (recommended):

```powershell
python -m PyInstaller system_info_displayer.spec
```

Optional: produce a one-file EXE:

```powershell
python -m PyInstaller --onefile -n system_info_displayer main.py
```

Common options:

- `--icon path/to/icon.ico` — set a custom icon
- `--noconsole` — hide console window (GUI apps)
- `--add-data "src;dst"` — include data files (use `;` on Windows)

Clean a previous build:

```powershell
Remove-Item -Recurse -Force build, dist
```


## Troubleshooting

- GPU lines show N/A:
	- Ensure an NVIDIA GPU and drivers are installed; NVML must be accessible. The app uses `nvidia-ml-py`.
- Audio visualizer shows N/A:
	- Windows: ensure `pyaudiowpatch` is installed and WASAPI loopback devices exist. Otherwise the app tries a `soundcard` loopback fallback where supported.
	- Disable the visualizer via Settings if no loopback device is available.
- Overlay not visible:
	- Check it’s not paused by a fullscreen app on the same monitor (toggle “Pause On Fullscreen Apps” in Settings).
	- Verify font color/size and placement (anchor/margins) in Settings.
- Dist/EXE quarantined:
	- Add `dist/` to AV exclusions or rebuild locally.


## Optional: CLI Audio Visualizer (dev tool)

The repository also includes a simple terminal visualizer useful for testing audio devices:

```powershell
python test.py --list              # show WASAPI devices (Windows)
python test.py                     # run with default loopback device
python test.py --device "Speakers" # pick by name substring
python test.py --index 12          # pick by index from --list
```


## Files

- `main.py` — overlay application entry point.
- `settings.json` — persisted UI/behavior configuration.
- `requirements.txt` — runtime dependencies.
- `system_info_displayer.spec` — PyInstaller build recipe.
- `test.py` — optional console audio visualizer for development.


---
Happy monitoring!

