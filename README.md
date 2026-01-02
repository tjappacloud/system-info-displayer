# System Info Displayer — Windows EXE

This project is packaged with PyInstaller. The generated executable is located at:

- `dist/system_info_displayer/system_info_displayer.exe`

## Rebuild the EXE

From the project root:

```powershell
# Using the existing spec (recommended for consistent builds)
C:/Users/natha/AppData/Local/Programs/Python/Python311/python.exe -m PyInstaller system_info_displayer.spec
```

## Optional: One-file EXE

If you prefer a single-file executable:

```powershell
C:/Users/natha/AppData/Local/Programs/Python/Python311/python.exe -m PyInstaller --onefile -n system_info_displayer main.py
```

This will place `dist/system_info_displayer.exe` as a single file.

## Common Options

- `--icon path/to/icon.ico` — set a custom icon
- `--noconsole` — hide console window (for GUI apps)
- `--add-data "src;dst"` — include data files (use `;` on Windows)

## Clean Builds

```powershell
# Remove previous build and dist outputs
Remove-Item -Recurse -Force build, dist
```

## Troubleshooting

- If antivirus quarantines the EXE, add the `dist/` folder to exceptions.
- For missing DLLs or data files, add them via `--add-data` or update the `.spec` file to include them.
"# system-info-displayer" 
