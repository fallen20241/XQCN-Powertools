# XQCN PowerTools

A Windows desktop application for inspecting, comparing, and editing Qualcomm XQCN modem calibration files. Built for engineers working with Quectel modem modules.

> **Version 0.12** — Active development. The XQCN Editor feature is experimental.

---

## Features

### XQCN Compare
Side-by-side comparison of two XQCN files.

- Detects entries that match, differ, or exist only in one file
- 6-column view: Path/Name, NV Name, Section, Status, Length A, Length B
- Bottom pane with Item Info, Payload A, Payload B, and a synchronized side-by-side Diff View (differing bytes highlighted)
- Filter by status (All / Match / Only A / Only B / Different) with live search
- Export a text report of the comparison

### XQCN Editor *(Experimental)*
Selectively remove NV entries from an XQCN file and save a trimmed copy.

- Loads all entries grouped by section
- Check/uncheck individual entries or entire sections
- Double-click any entry for a hex payload preview
- Saves only checked entries to a new file — original is never modified
- Correctly removes paired EFS_Dir + EFS_Data entries together

### NV Name Resolution
Friendly names and descriptions are resolved from a consolidated index at `addons/nv_index.json` (~14,900 entries, ~707 KB). The index is generated at build time from three upstream Qualcomm XMLs, which are kept in `addons/` as source-of-truth but not bundled into the final executable:

| Source XML | Contents |
|---|---|
| `NvDefinition.xml` | RF NV items by numeric ID (~14k items) |
| `NvDefinition5g.xml` | 5G NR tree file IDs |
| `nv_efs_data_format.xml` | EFS path → name + description |

Regenerate the index after updating any source XML:
```bash
python tools/build_nv_index.py            # rebuild
python tools/build_nv_index.py --check    # exit 1 if stale (CI-friendly)
```
SHA-256 hashes of the source XMLs are recorded in the index's `_meta` block so staleness is detectable without re-parsing.

---

## Requirements

- Python 3.10+
- `tkinter` (included with standard Python on Windows and macOS; on Linux: `sudo apt install python3-tk`)
- On macOS/Linux, Pillow is required for the app icon: `pip install pillow`

Or use the pre-built binary from the [Releases](../../releases) page — no Python required.

| Platform | Python install |
|---|---|
| Windows 10/11 | [python.org](https://python.org) — tkinter included |
| macOS | [python.org](https://python.org) or `brew install python-tk` |
| Linux (Ubuntu/Debian) | `sudo apt install python3 python3-tk` |

---

## Running from Source

**Windows:**
```bat
XQCNPowerTools.bat
```

**macOS / Linux:**
```bash
chmod +x XQCNPowerTools.sh
./XQCNPowerTools.sh
```

Or directly on any platform:
```bash
python3 XQCNPowerTools.py
```

---

## Building the Executable

Requires [PyInstaller](https://pyinstaller.org). Build must be run on the target platform.

```bash
pip install pyinstaller
pyinstaller XQCNPowerTools.spec
```

| Platform | Output |
|---|---|
| Windows | `dist/XQCNPowerTools.exe` |
| macOS | `dist/XQCNPowerTools` |
| Linux | `dist/XQCNPowerTools` |

---

## Companion Tool — Firmware Extractor

`FirmwareExtractor.py` extracts XQCN calibration files from raw Quectel modem firmware images (`modem.img`).

**Dependencies:**
```bash
pip install ubireader PySquashfsImage lzallright
```

**Run:**
```bat
FirmwareExtractor.bat
```

**Build:**
```bash
pyinstaller FirmwareExtractor.spec
```

---

## File Structure

```
XQCNPowerTools.py       Main application
XQCNPowerTools.spec     PyInstaller build spec
XQCNPowerTools.bat      Windows launcher
XQCNPowerTools.sh       macOS / Linux launcher
FirmwareExtractor.py    Firmware extraction tool
FirmwareExtractor.spec  PyInstaller build spec
FirmwareExtractor.bat   Windows launcher
fallout.ico             Application icon
.gitattributes          Cross-platform line-ending rules
addons/
  nv_index.json         Consolidated NV lookup (bundled at runtime)
  NvDefinition.xml      Source XML — Qualcomm RF NV items
  NvDefinition5g.xml    Source XML — 5G NR tree files
  nv_efs_data_format.xml  Source XML — EFS paths + descriptions
tools/
  build_nv_index.py     Regenerates addons/nv_index.json from source XMLs
```

---

## Supported Modem Modules

Developed and tested against firmware from:

- Quectel RM551E-GL
- Quectel RM521F-GL

XQCN files from other Qualcomm-based Quectel modules should also work.

---

## Theme

UI uses the [Catppuccin Mocha](https://github.com/catppuccin/catppuccin) dark color palette.

---

## License

MIT
