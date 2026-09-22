# Package Scripts

`build-standalone-cli.ps1` builds a single-file, self-contained CLI exe with PyInstaller. It analyzes only the freshly built Lara wheel, so `dist\lara.exe` carries all Python code and dependencies inside one file: copy it to any Windows x64 machine and run it directly without Python, `uv`, or this repository. Launched without arguments (for example by double-click), it opens an interactive chat session (`lara session chat --new`); with arguments it behaves as the normal CLI. Build output lands in `build\package\lara-standalone\lara.exe` with the shippable copy at `dist\lara.exe`.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\package\build-standalone-cli.ps1
```

Windows desktop packaging is split into two steps:

1. `build-python-api.ps1` uses PyInstaller through `uv` to create `build/package/lara-api/lara-api.exe` and `build/package/lara-api/lara.exe`.
2. `build-desktop.ps1` builds the React renderer and runs electron-builder. The PyInstaller output is copied into Electron resources as `backend/lara-api`.

The resulting Electron app starts the bundled `lara-api.exe` at launch, and the Windows installer adds the bundled CLI directory to the current user's PATH. Target machines do not need Python, `uv`, or project Python dependencies installed. After installation, open a new terminal to run commands such as:

```powershell
lara session chat --new
```

Useful commands from the repository root:

```powershell
npm run desktop:portable
npm --prefix apps/desktop run package:dir
npm --prefix apps/desktop run package:win
```

`npm run desktop:portable` creates a standalone `Lara-Desktop-<version>-portable.exe` in `apps/desktop/release`. It can be copied to another Windows x64 machine and opened directly without an installer. The portable build does not modify `PATH`; use the NSIS installer when the `lara` terminal command is also required.

`build-desktop.ps1` sets default Electron download mirrors for China-friendly packaging. Override `ELECTRON_MIRROR` or `ELECTRON_BUILDER_BINARIES_MIRROR` before running the script if you use a different mirror.
