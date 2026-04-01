"""
XQCN PowerTools — A tkinter GUI for comparing XQCN calibration files.
"""

import os
import platform
import re
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------
_OS = platform.system()   # "Windows" | "Darwin" | "Linux"

# UI font: Segoe UI on Windows, SF Pro on macOS, Ubuntu/DejaVu on Linux
FONT_UI   = ("Segoe UI"        if _OS == "Windows" else
             "SF Pro Text"     if _OS == "Darwin"  else
             "Ubuntu")

# Monospace font for hex dumps
FONT_MONO = ("Consolas"        if _OS == "Windows" else
             "Menlo"           if _OS == "Darwin"  else
             "DejaVu Sans Mono")

# ---------------------------------------------------------------------------
# Catppuccin Mocha palette
# ---------------------------------------------------------------------------
BG       = "#1e1e2e"
PANEL    = "#181825"
SURFACE  = "#313244"
SURFACE2 = "#45475a"
TEXT     = "#cdd6f4"
SUBTEXT  = "#a6adc8"
ACCENT   = "#89b4fa"
GREEN    = "#a6e3a1"
YELLOW   = "#f9e2af"
RED      = "#f38ba8"
MAUVE    = "#cba6f7"
PEACH    = "#fab387"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Entry:
    key: str
    label: str
    section: str
    hex_value: str
    length: int


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def _decode_efs_path(hex_str: str) -> str:
    """Decode EFS_Dir stream bytes as a UTF-8 null-terminated path.

    Different XQCN sections use different header lengths (8 bytes for
    EFS_Backup, 0 bytes for NV_Items, etc.).  Rather than hard-coding
    the skip count, scan for the first 0x2F ('/') byte — all EFS paths
    are absolute and begin with '/'.
    """
    parts = hex_str.strip().split()
    if not parts:
        return ""
    start = next((i for i, x in enumerate(parts) if int(x, 16) == 0x2F), -1)
    if start < 0:
        return ""
    raw = bytes(int(x, 16) for x in parts[start:])
    return raw.decode("utf-8", errors="replace").rstrip("\x00")


def _walk(node: ET.Element, section_stack: list, results: dict):
    """
    Recursively walk the XML tree.
    Detects EFS_Dir + EFS_Data sibling pairs and pairs them by index.
    """
    # Collect direct children by tag and name
    child_storages: dict[str, ET.Element] = {}
    child_streams: list[ET.Element] = []

    for child in node:
        if child.tag == "Storage":
            name = child.get("Name", "")
            child_storages[name] = child
        elif child.tag == "Stream":
            child_streams.append(child)

    # Check for EFS_Dir + EFS_Data pattern
    efs_dir_node  = child_storages.get("EFS_Dir")
    efs_data_node = child_storages.get("EFS_Data")
    handled_efs_names: set[str] = set()

    if efs_dir_node is not None and efs_data_node is not None:
        # Build index->path map from EFS_Dir
        dir_entries: dict[str, str] = {}
        for stream in efs_dir_node:
            if stream.tag == "Stream":
                idx = stream.get("Name", "")
                val = stream.get("Value", "")
                path = _decode_efs_path(val)
                if path:
                    dir_entries[idx] = path

        # Build index->stream map from EFS_Data
        data_entries: dict[str, ET.Element] = {}
        for stream in efs_data_node:
            if stream.tag == "Stream":
                idx = stream.get("Name", "")
                data_entries[idx] = stream

        # Current section name is last item in stack (without the prefix strip)
        section = section_stack[-1] if section_stack else "unknown"

        for idx, path in dir_entries.items():
            data_stream = data_entries.get(idx)
            hex_val = ""
            length  = 0
            if data_stream is not None:
                hex_val = " ".join(data_stream.get("Value", "").split())
                try:
                    length = int(data_stream.get("Length", "0"))
                except ValueError:
                    length = 0

            key = f"{section}::{path}"
            results[key] = Entry(
                key=key,
                label=path,
                section=section,
                hex_value=hex_val,
                length=length,
            )

        handled_efs_names = {"EFS_Dir", "EFS_Data"}

    # Process plain Stream children
    section = section_stack[-1] if section_stack else "unknown"
    for stream in child_streams:
        name    = stream.get("Name", "")
        hex_val = " ".join(stream.get("Value", "").split())
        try:
            length = int(stream.get("Length", "0"))
        except ValueError:
            length = 0
        key = f"{section}::{name}"
        results[key] = Entry(
            key=key,
            label=name,
            section=section,
            hex_value=hex_val,
            length=length,
        )

    # Recurse into child storages (skip EFS handled ones)
    skip_prefixes = {"00000000", "default"}  # prefix-stripping targets

    for name, child in child_storages.items():
        if name in handled_efs_names:
            continue
        # Build new section stack
        # Strip the top-level filename, 00000000, and default wrappers
        if name in skip_prefixes or (section_stack and section_stack[0] == name):
            new_stack = section_stack[:]
        else:
            new_stack = section_stack + [name]
        _walk(child, new_stack, results)


def _strip_xml_declaration(text: str) -> str:
    """Remove <?xml ...?> declaration if present."""
    return re.sub(r"<\?xml[^?]*\?>\s*", "", text, count=1)


def parse_xqcn(path: str) -> dict:
    """Parse an XQCN file and return a dict of key -> Entry."""
    with open(path, "rb") as f:
        raw = f.read()

    # Decode with Windows-1252 fallback
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("windows-1252", errors="replace")

    text = _strip_xml_declaration(text)

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"Failed to parse XQCN XML: {exc}") from exc

    results: dict = {}

    # Root is <Storage Name="filename.xqcn">
    # Walk its children; skip the 00000000 > default wrapper silently
    def _descend_to_default(node: ET.Element):
        """Navigate through 00000000 > default wrappers before real walking."""
        for child in node:
            if child.tag == "Storage":
                name = child.get("Name", "")
                if name == "00000000":
                    # Look for default child
                    for grandchild in child:
                        if grandchild.tag == "Storage" and grandchild.get("Name") == "default":
                            # Walk default's children
                            for item in grandchild:
                                if item.tag == "Storage":
                                    section_name = item.get("Name", "unknown")
                                    _walk(item, [section_name], results)
                                elif item.tag == "Stream":
                                    section = "default"
                                    hex_val = " ".join(item.get("Value", "").split())
                                    try:
                                        length = int(item.get("Length", "0"))
                                    except ValueError:
                                        length = 0
                                    stream_name = item.get("Name", "")
                                    key = f"{section}::{stream_name}"
                                    results[key] = Entry(
                                        key=key,
                                        label=stream_name,
                                        section=section,
                                        hex_value=hex_val,
                                        length=length,
                                    )
                        else:
                            # No default wrapper; walk the 00000000 children directly
                            section_name = grandchild.get("Name", "unknown")
                            _walk(grandchild, [section_name], results)
                else:
                    # Some other top-level storage (e.g. File_Version stream sibling)
                    pass
            elif child.tag == "Stream":
                # Top-level streams like File_Version
                pass  # Skip File_Version and similar

    _descend_to_default(root)
    return results


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------
def diff_entries(a: dict, b: dict) -> dict:
    """
    Returns dict of key -> (status, entry_a_or_None, entry_b_or_None).
    status: "match" | "only_a" | "only_b" | "differ"
    """
    all_keys = set(a) | set(b)
    result = {}
    for key in all_keys:
        ea = a.get(key)
        eb = b.get(key)
        if ea is not None and eb is None:
            result[key] = ("only_a", ea, None)
        elif ea is None and eb is not None:
            result[key] = ("only_b", None, eb)
        elif ea is not None and eb is not None:
            if ea.hex_value == eb.hex_value:
                result[key] = ("match", ea, eb)
            else:
                result[key] = ("differ", ea, eb)
    return result


# ---------------------------------------------------------------------------
# Hex dump
# ---------------------------------------------------------------------------
def hex_dump(hex_str: str) -> str:
    """Return a formatted hex dump, 16 bytes per line."""
    if not hex_str or not hex_str.strip():
        return "(empty)"
    parts = hex_str.strip().split()
    lines = []
    for i in range(0, len(parts), 16):
        chunk = parts[i:i + 16]
        offset = f"{i:04X}"
        hex_part = " ".join(chunk).ljust(47)
        ascii_part = "".join(
            chr(int(b, 16)) if 0x20 <= int(b, 16) < 0x7F else "."
            for b in chunk
        )
        lines.append(f"{offset}  {hex_part}  {ascii_part}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# NV Definition loader
# ---------------------------------------------------------------------------
def _find_base_dir() -> str:
    """Locate the directory containing the exe or script."""
    if getattr(sys, "frozen", False):
        # PyInstaller one-file: datas land in sys._MEIPASS
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _find_addons_dir() -> str:
    """Locate the addons/ directory next to the exe or script."""
    return os.path.join(_find_base_dir(), "addons")


def load_nv_definitions(addons_dir: str) -> dict:
    """
    Build a lookup dict from the addon XML definition files.

    Keys:
      - EFS full path  (e.g. "/nv/item_files/modem/nas/ehplmn")
      - Numeric ID str (e.g. "10")
    Values:
      - (name: str, description: str)
    """
    defs: dict[str, tuple[str, str]] = {}

    # --- nv_efs_data_format.xml -------------------------------------------
    efs_fmt = os.path.join(addons_dir, "nv_efs_data_format.xml")
    if os.path.isfile(efs_fmt):
        try:
            root = ET.parse(efs_fmt).getroot()
            for elem in root.iter():
                if elem.tag not in ("NvEfsItemData", "NvItemData"):
                    continue
                name  = elem.get("name", "")
                desc  = elem.get("description", "")
                nv_id = elem.get("id", "")
                fpath = elem.get("fullpathname", "")
                if fpath:
                    defs[fpath] = (name, desc)
                if nv_id and nv_id not in defs:
                    defs[nv_id] = (name, desc)
        except Exception:
            pass

    # --- NvDefinition.xml  (RF NV items by numeric ID) --------------------
    nvdef = os.path.join(addons_dir, "NvDefinition.xml")
    if os.path.isfile(nvdef):
        try:
            root = ET.parse(nvdef).getroot()
            for elem in root.iter("NvItem"):
                nv_id = elem.get("id", "")
                name  = elem.get("name", "")
                if nv_id and name and nv_id not in defs:
                    defs[nv_id] = (name, "")
        except Exception:
            pass

    # --- NvDefinition5g.xml  (5G tree files by numeric ID) ----------------
    nvdef5g = os.path.join(addons_dir, "NvDefinition5g.xml")
    if os.path.isfile(nvdef5g):
        try:
            root = ET.parse(nvdef5g).getroot()
            for elem in root.iter():
                if elem.tag in ("NvItem", "NvTreeFile"):
                    nv_id = elem.get("id", "")
                    name  = elem.get("name", "")
                    if nv_id and name and nv_id not in defs:
                        defs[nv_id] = (name, "")
        except Exception:
            pass

    return defs


# ---------------------------------------------------------------------------
# XQCN save helper (used by XQCN Editor)
# ---------------------------------------------------------------------------
def _remove_unlisted_entries(node: ET.Element, keys_to_keep: set, section_stack: list):
    """Walk the XML tree, removing Stream elements whose constructed key is not in keys_to_keep."""
    child_storages: dict[str, ET.Element] = {}
    child_streams: list[ET.Element] = []
    for child in node:
        if child.tag == "Storage":
            child_storages[child.get("Name", "")] = child
        elif child.tag == "Stream":
            child_streams.append(child)

    efs_dir_node  = child_storages.get("EFS_Dir")
    efs_data_node = child_storages.get("EFS_Data")
    handled: set[str] = set()
    section = section_stack[-1] if section_stack else "unknown"

    if efs_dir_node is not None and efs_data_node is not None:
        dir_map: dict[str, str] = {}
        for stream in efs_dir_node:
            if stream.tag == "Stream":
                idx  = stream.get("Name", "")
                path = _decode_efs_path(stream.get("Value", ""))
                if path:
                    dir_map[idx] = path
        remove_idx = {idx for idx, path in dir_map.items()
                      if f"{section}::{path}" not in keys_to_keep}
        for stream in list(efs_dir_node):
            if stream.tag == "Stream" and stream.get("Name", "") in remove_idx:
                efs_dir_node.remove(stream)
        for stream in list(efs_data_node):
            if stream.tag == "Stream" and stream.get("Name", "") in remove_idx:
                efs_data_node.remove(stream)
        handled = {"EFS_Dir", "EFS_Data"}

    for stream in child_streams:
        if f"{section}::{stream.get('Name', '')}" not in keys_to_keep:
            node.remove(stream)

    skip_prefixes = {"00000000", "default"}
    for name, child in child_storages.items():
        if name in handled:
            continue
        new_stack = section_stack[:] if (
            name in skip_prefixes or (section_stack and section_stack[0] == name)
        ) else section_stack + [name]
        _remove_unlisted_entries(child, keys_to_keep, new_stack)


def save_xqcn_filtered(source_path: str, dest_path: str, keys_to_keep: set):
    """Write a new XQCN containing only entries whose key is in keys_to_keep."""
    with open(source_path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("windows-1252", errors="replace")

    # Detect the attribute quote character used in the original file so we can preserve it.
    # Both ' and " are valid XML; some XQCN files use single quotes throughout.
    _qm = re.search(r'\bName=(["\'])', text)
    orig_quote = _qm.group(1) if _qm else '"'

    text = _strip_xml_declaration(text)
    root = ET.fromstring(text)

    for child in root:
        if child.tag == "Storage" and child.get("Name") == "00000000":
            for grandchild in child:
                if grandchild.tag == "Storage" and grandchild.get("Name") == "default":
                    for item in list(grandchild):
                        if item.tag == "Storage":
                            _remove_unlisted_entries(item, keys_to_keep, [item.get("Name", "unknown")])
                        elif item.tag == "Stream":
                            if f"default::{item.get('Name', '')}" not in keys_to_keep:
                                grandchild.remove(item)
                elif grandchild.tag == "Storage":
                    _remove_unlisted_entries(grandchild, keys_to_keep, [grandchild.get("Name", "unknown")])

    # ET always serializes with double quotes; convert back to single quotes if the source used them.
    output = ET.tostring(root, encoding="unicode")
    if orig_quote == "'":
        output = re.sub(r'="([^"]*)"', r"='\1'", output)

    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(output)


# ---------------------------------------------------------------------------
# GUI Application
# ---------------------------------------------------------------------------
class XQCNPowerTools(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("XQCN PowerTools — v0.10")
        self.geometry("1280x820")
        self.minsize(900, 600)
        self.configure(bg=BG)

        # App icon (Fallout radiation symbol)
        _ico = os.path.join(_find_base_dir(), "fallout.ico")
        if os.path.isfile(_ico):
            try:
                if _OS == "Windows":
                    self.iconbitmap(_ico)
                else:
                    # iconbitmap only works on Windows; use iconphoto on macOS/Linux
                    from PIL import Image, ImageTk
                    _img = Image.open(_ico)
                    self._icon_photo = ImageTk.PhotoImage(_img)  # keep reference alive
                    self.iconphoto(True, self._icon_photo)
            except Exception:
                pass

        # State
        self._file_a: Optional[str] = None
        self._file_b: Optional[str] = None
        self._entries_a: dict = {}
        self._entries_b: dict = {}
        self._diff: dict = {}   # key -> (status, ea, eb)
        self._mode: str = "none"  # "none" | "single" | "diff"
        self._tree_items: dict = {}  # iid -> key
        self._filter_var = tk.StringVar(value="All")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())

        # NV definitions
        self._nv_defs: dict = load_nv_definitions(_find_addons_dir())

        # Navigation state
        self._panels: dict = {}
        self._nav_buttons: dict = {}
        self._active_panel: str = ""

        # Editor state
        self._editor_file: Optional[str] = None
        self._editor_entries: dict = {}
        self._editor_checked: set = set()        # keys of entries to KEEP
        self._editor_tree_items: dict = {}       # iid -> key  (None = section header)
        self._editor_section_iids: dict = {}     # section -> header iid
        self._editor_section_items: dict = {}    # section -> [item iids]

        self._build_styles()
        self._build_ui()

    # ------------------------------------------------------------------
    # NV name lookup
    # ------------------------------------------------------------------
    def _resolve_nv_key(self, label: str) -> str:
        """
        Map an entry label to a definitions dict key.
        Handles three formats:
          1. Exact EFS path  e.g. "/nv/item_files/modem/nas/ehplmn"
          2. EFS path with zero-padded numeric ID  e.g. "/nv/item_files/rfnv/00029652"
          3. Plain numeric stream name  e.g. "10"
        Returns the matching key string, or '' if no match.
        """
        if label in self._nv_defs:
            return label
        # EFS path whose last component is a (possibly zero-padded) numeric ID
        if '/' in label:
            last = label.rsplit('/', 1)[-1]
            if last and all(c.isdigit() for c in last):
                numeric_id = str(int(last))   # strip leading zeros
                if numeric_id in self._nv_defs:
                    return numeric_id
        # Plain numeric stream name
        elif label and all(c.isdigit() for c in label):
            if label in self._nv_defs:
                return label
        return ""

    def _lookup_nv_name(self, entry) -> str:
        """Return the friendly NV name for an entry, or '' if unknown."""
        key = self._resolve_nv_key(entry.label)
        return self._nv_defs[key][0] if key else ""

    def _lookup_nv_desc(self, entry) -> str:
        """Return the description for an entry, or '' if unknown."""
        key = self._resolve_nv_key(entry.label)
        return self._nv_defs[key][1] if key else ""

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------
    def _build_styles(self):
        style = ttk.Style(self)
        style.theme_use("default")

        # Treeview
        style.configure(
            "Custom.Treeview",
            background=SURFACE,
            foreground=TEXT,
            fieldbackground=SURFACE,
            rowheight=22,
            font=(FONT_MONO, 10),
            borderwidth=0,
        )
        style.configure(
            "Custom.Treeview.Heading",
            background=PANEL,
            foreground=ACCENT,
            font=(FONT_UI, 10, "bold"),
            relief="flat",
        )
        style.map(
            "Custom.Treeview",
            background=[("selected", SURFACE2)],
            foreground=[("selected", TEXT)],
        )

        # Notebook
        style.configure(
            "Custom.TNotebook",
            background=PANEL,
            borderwidth=0,
        )
        style.configure(
            "Custom.TNotebook.Tab",
            background=SURFACE,
            foreground=SUBTEXT,
            padding=[10, 4],
            font=(FONT_UI, 10),
        )
        style.map(
            "Custom.TNotebook.Tab",
            background=[("selected", ACCENT)],
            foreground=[("selected", PANEL)],
        )

        # Radiobuttons
        style.configure(
            "Filter.TRadiobutton",
            background=PANEL,
            foreground=TEXT,
            font=(FONT_UI, 10),
            focuscolor=PANEL,
        )
        style.map(
            "Filter.TRadiobutton",
            background=[("active", PANEL)],
            foreground=[("active", ACCENT)],
        )

        # Labels
        style.configure(
            "Header.TLabel",
            background=PANEL,
            foreground=SUBTEXT,
            font=(FONT_UI, 10),
        )
        style.configure(
            "Filename.TLabel",
            background=PANEL,
            foreground=TEXT,
            font=(FONT_UI, 10, "bold"),
        )
        style.configure(
            "Stats.TLabel",
            background=BG,
            foreground=SUBTEXT,
            font=(FONT_UI, 10),
        )

        # Frames
        style.configure("Header.TFrame", background=PANEL)
        style.configure("Stats.TFrame",  background=BG)
        style.configure("Filter.TFrame", background=PANEL)
        style.configure("Main.TFrame",   background=BG)

        # Separator
        style.configure("TSeparator", background=SURFACE2)

        # Scrollbar
        style.configure(
            "Custom.Vertical.TScrollbar",
            background=SURFACE2,
            troughcolor=SURFACE,
            borderwidth=0,
            arrowsize=12,
        )
        style.configure(
            "Custom.Horizontal.TScrollbar",
            background=SURFACE2,
            troughcolor=SURFACE,
            borderwidth=0,
            arrowsize=12,
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True)

        self._build_nav(outer)

        # Vertical divider
        tk.Frame(outer, bg=SURFACE2, width=1).pack(side="left", fill="y")

        # Content host — all panels stack here
        content_host = tk.Frame(outer, bg=BG)
        content_host.pack(side="left", fill="both", expand=True)

        self._build_xqcn_compare_panel(content_host)
        self._build_placeholder_panel(content_host, "nv_browser",    "NV Browser",     MAUVE)
        self._build_xqcn_editor_panel(content_host)

        self._show_panel("xqcn_compare")

    # ------------------------------------------------------------------
    # Navigation rail
    # ------------------------------------------------------------------
    def _build_nav(self, parent: tk.Frame):
        nav = tk.Frame(parent, bg=PANEL, width=164)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)

        # App title
        tk.Label(
            nav, text="XQCN\nPowerTools",
            bg=PANEL, fg=ACCENT,
            font=(FONT_UI, 11, "bold"),
            anchor="center", justify="center",
        ).pack(pady=(20, 12), padx=10)

        ttk.Separator(nav, orient="horizontal").pack(fill="x", padx=14, pady=(0, 10))

        nav_items = [
            ("nv_browser",     "NV Browser"),
            ("xqcn_compare",   "XQCN Compare"),
            ("xqcn_editor",    "XQCN Editor ⚠ Exp."),
        ]
        for panel_id, label in nav_items:
            btn = tk.Button(
                nav, text=label,
                command=lambda pid=panel_id: self._show_panel(pid),
                bg=SURFACE, fg=SUBTEXT,
                activebackground=SURFACE2, activeforeground=TEXT,
                relief="flat", padx=14, pady=10,
                font=(FONT_UI, 10), cursor="hand2",
                highlightthickness=0, bd=0,
                anchor="w", width=16,
            )
            btn.pack(fill="x", padx=10, pady=3)
            btn.bind("<Enter>", lambda e, b=btn, pid=panel_id: self._nav_hover(b, pid, True))
            btn.bind("<Leave>", lambda e, b=btn, pid=panel_id: self._nav_hover(b, pid, False))
            self._nav_buttons[panel_id] = btn

    def _nav_hover(self, btn: tk.Button, panel_id: str, entering: bool):
        if panel_id == self._active_panel:
            return
        btn.configure(bg=SURFACE2 if entering else SURFACE,
                      fg=TEXT     if entering else SUBTEXT)

    def _show_panel(self, panel_id: str):
        for pid, btn in self._nav_buttons.items():
            if pid == panel_id:
                btn.configure(bg=ACCENT, fg=PANEL,
                              activebackground="#74a8f5", activeforeground=PANEL)
            else:
                btn.configure(bg=SURFACE, fg=SUBTEXT,
                              activebackground=SURFACE2, activeforeground=TEXT)
        for pid, frame in self._panels.items():
            if pid == panel_id:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        self._active_panel = panel_id

    # ------------------------------------------------------------------
    # Panel builders
    # ------------------------------------------------------------------
    def _build_xqcn_compare_panel(self, content_host: tk.Frame):
        panel = tk.Frame(content_host, bg=BG)
        self._panels["xqcn_compare"] = panel
        self._build_header(panel)
        self._build_stats(panel)
        self._build_filter(panel)
        ttk.Separator(panel, orient="horizontal").pack(fill="x")
        self._build_paned(panel)

    def _build_placeholder_panel(self, content_host: tk.Frame, panel_id: str,
                                  title: str, accent_color: str):
        panel = tk.Frame(content_host, bg=BG)
        self._panels[panel_id] = panel
        inner = tk.Frame(panel, bg=BG)
        inner.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(inner, text=title, bg=BG, fg=accent_color,
                 font=(FONT_UI, 26, "bold")).pack()
        tk.Label(inner, text="Coming Soon", bg=BG, fg=SUBTEXT,
                 font=(FONT_UI, 13)).pack(pady=(6, 0))

    def _build_header(self, parent: tk.Frame):
        hf = ttk.Frame(parent, style="Header.TFrame")
        hf.pack(fill="x", padx=0, pady=0)

        # Padding
        inner = tk.Frame(hf, bg=PANEL)
        inner.pack(fill="x", padx=10, pady=8)

        # File A
        ttk.Label(inner, text="File A:", style="Header.TLabel").pack(side="left", padx=(0, 4))
        self._lbl_a = ttk.Label(inner, text="(none)", style="Filename.TLabel", width=30, anchor="w")
        self._lbl_a.pack(side="left", padx=(0, 6))
        tk.Button(
            inner, text="Open A…",
            command=self._open_a,
            bg=SURFACE, fg=TEXT,
            activebackground=SURFACE2, activeforeground=TEXT,
            relief="flat", padx=10, pady=4,
            font=(FONT_UI, 10), cursor="hand2",
            highlightthickness=0,
        ).pack(side="left", padx=(0, 16))

        # File B
        ttk.Label(inner, text="File B:", style="Header.TLabel").pack(side="left", padx=(0, 4))
        self._lbl_b = ttk.Label(inner, text="(none)", style="Filename.TLabel", width=30, anchor="w")
        self._lbl_b.pack(side="left", padx=(0, 6))
        tk.Button(
            inner, text="Open B…",
            command=self._open_b,
            bg=SURFACE, fg=TEXT,
            activebackground=SURFACE2, activeforeground=TEXT,
            relief="flat", padx=10, pady=4,
            font=(FONT_UI, 10), cursor="hand2",
            highlightthickness=0,
        ).pack(side="left", padx=(0, 16))

        # Compare button
        tk.Button(
            inner, text="⇄ Compare",
            command=self._run_compare,
            bg=ACCENT, fg=PANEL,
            activebackground="#74a8f5", activeforeground=PANEL,
            relief="flat", padx=14, pady=4,
            font=(FONT_UI, 10, "bold"), cursor="hand2",
            highlightthickness=0,
        ).pack(side="left")

        # Export button (right-aligned)
        tk.Button(
            inner, text="⬇ Export…",
            command=self._export_text_file,
            bg=SURFACE, fg=GREEN,
            activebackground=SURFACE2, activeforeground=GREEN,
            relief="flat", padx=12, pady=4,
            font=(FONT_UI, 10), cursor="hand2",
            highlightthickness=0,
        ).pack(side="right")

    def _build_stats(self, parent: tk.Frame):
        sf = ttk.Frame(parent, style="Stats.TFrame")
        sf.pack(fill="x", padx=10, pady=4)

        self._stat_labels = {}
        stats = [
            ("total",   "Total: 0",     TEXT),
            ("match",   "Match: 0",     GREEN),
            ("only_a",  "Only A: 0",    YELLOW),
            ("only_b",  "Only B: 0",    YELLOW),
            ("differ",  "Different: 0", RED),
        ]
        for key, text, colour in stats:
            lbl = tk.Label(
                sf, text=text,
                bg=BG, fg=colour,
                font=(FONT_UI, 10, "bold"),
            )
            lbl.pack(side="left", padx=(0, 20))
            self._stat_labels[key] = lbl

    def _build_filter(self, parent: tk.Frame):
        ff = ttk.Frame(parent, style="Filter.TFrame")
        ff.pack(fill="x", padx=0, pady=0)

        inner = tk.Frame(ff, bg=PANEL)
        inner.pack(fill="x", padx=10, pady=6)

        tk.Label(inner, text="Search:", bg=PANEL, fg=SUBTEXT,
                 font=(FONT_UI, 10)).pack(side="left", padx=(0, 6))

        search_entry = tk.Entry(
            inner, textvariable=self._search_var,
            bg=SURFACE, fg=TEXT, insertbackground=TEXT,
            relief="flat", font=(FONT_MONO, 10), width=30,
            highlightthickness=1, highlightbackground=SURFACE2,
            highlightcolor=ACCENT,
        )
        search_entry.pack(side="left", padx=(0, 16), ipady=3)

        tk.Label(inner, text="Filter:", bg=PANEL, fg=SUBTEXT,
                 font=(FONT_UI, 10)).pack(side="left", padx=(0, 6))

        for option in ("All", "Match", "Only A", "Only B", "Different"):
            rb = ttk.Radiobutton(
                inner, text=option,
                variable=self._filter_var, value=option,
                style="Filter.TRadiobutton",
                command=self._apply_filter,
            )
            rb.pack(side="left", padx=4)

    def _build_paned(self, parent: tk.Frame):
        pw = tk.PanedWindow(
            parent, orient="vertical",
            bg=SURFACE2, sashwidth=5, sashrelief="flat",
            handlesize=0,
        )
        pw.pack(fill="both", expand=True, padx=0, pady=0)

        # --- Top pane: Treeview ---
        top_frame = tk.Frame(pw, bg=BG)
        pw.add(top_frame, minsize=200, stretch="always")

        cols = ("name", "nv_name", "section", "status", "len_a", "len_b")
        self._tree = ttk.Treeview(
            top_frame,
            columns=cols,
            show="headings",
            style="Custom.Treeview",
            selectmode="browse",
        )
        self._tree.heading("name",    text="Name / Path",  anchor="w")
        self._tree.heading("nv_name", text="NV Name",      anchor="w")
        self._tree.heading("section", text="Section",      anchor="w")
        self._tree.heading("status",  text="Status",       anchor="center")
        self._tree.heading("len_a",   text="Len A",        anchor="e")
        self._tree.heading("len_b",   text="Len B",        anchor="e")

        self._tree.column("name",    width=380, stretch=True,  anchor="w")
        self._tree.column("nv_name", width=260, stretch=True,  anchor="w")
        self._tree.column("section", width=160, stretch=False, anchor="w")
        self._tree.column("status",  width=100, stretch=False, anchor="center")
        self._tree.column("len_a",   width=70,  stretch=False, anchor="e")
        self._tree.column("len_b",   width=70,  stretch=False, anchor="e")

        # Row colour tags
        self._tree.tag_configure("match",    foreground=GREEN)
        self._tree.tag_configure("only_one", foreground=YELLOW)
        self._tree.tag_configure("differ",   foreground=RED)
        self._tree.tag_configure("neutral",  foreground=TEXT)
        self._tree.tag_configure("section_header", foreground=ACCENT,
                                  font=(FONT_UI, 10, "bold"),
                                  background=PANEL)

        vsb = ttk.Scrollbar(top_frame, orient="vertical",
                             command=self._tree.yview,
                             style="Custom.Vertical.TScrollbar")
        hsb = ttk.Scrollbar(top_frame, orient="horizontal",
                             command=self._tree.xview,
                             style="Custom.Horizontal.TScrollbar")
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._tree.pack(fill="both", expand=True)

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # --- Bottom pane: Notebook ---
        bot_frame = tk.Frame(pw, bg=PANEL)
        pw.add(bot_frame, minsize=160, stretch="never")

        nb = ttk.Notebook(bot_frame, style="Custom.TNotebook")
        nb.pack(fill="both", expand=True)

        # Tab: Item Info
        info_frame = tk.Frame(nb, bg=PANEL)
        nb.add(info_frame, text="Item Info")
        self._info_text = self._make_text_widget(info_frame, TEXT)

        # Tab: Payload A
        pa_frame = tk.Frame(nb, bg=PANEL)
        nb.add(pa_frame, text="Payload A")
        self._text_a = self._make_text_widget(pa_frame, GREEN)

        # Tab: Payload B
        pb_frame = tk.Frame(nb, bg=PANEL)
        nb.add(pb_frame, text="Payload B")
        self._text_b = self._make_text_widget(pb_frame, YELLOW)

        # Tab: Diff View (side-by-side with diff highlighting)
        dv_frame = tk.Frame(nb, bg=PANEL)
        nb.add(dv_frame, text="Diff View")
        self._diff_text_a, self._diff_text_b = self._make_diff_pair(dv_frame)

        # Set initial pane sizes after window is drawn
        self.after(100, lambda: pw.sash_place(0, 0, 480))

    def _make_text_widget(self, parent: tk.Frame, fg_colour: str) -> tk.Text:
        frame = tk.Frame(parent, bg=PANEL)
        frame.pack(fill="both", expand=True)

        vsb = ttk.Scrollbar(frame, orient="vertical",
                             style="Custom.Vertical.TScrollbar")
        hsb = ttk.Scrollbar(frame, orient="horizontal",
                             style="Custom.Horizontal.TScrollbar")

        txt = tk.Text(
            frame,
            bg=PANEL, fg=fg_colour,
            font=(FONT_MONO, 10),
            relief="flat",
            state="disabled",
            wrap="none",
            selectbackground=SURFACE2,
            selectforeground=TEXT,
            insertbackground=TEXT,
            yscrollcommand=vsb.set,
            xscrollcommand=hsb.set,
        )
        vsb.configure(command=txt.yview)
        hsb.configure(command=txt.xview)

        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True)
        return txt

    def _make_diff_pair(self, parent: tk.Frame):
        """Two side-by-side hex Text widgets with a shared vertical scrollbar."""
        outer = tk.Frame(parent, bg=PANEL)
        outer.pack(fill="both", expand=True)

        # Column labels
        hdr = tk.Frame(outer, bg=PANEL)
        hdr.pack(fill="x")
        for label, fg in (("  File A", GREEN), ("  File B", YELLOW)):
            tk.Label(hdr, text=label, bg=PANEL, fg=fg,
                     font=(FONT_UI, 9, "bold")).pack(side="left", expand=True, anchor="w")

        body = tk.Frame(outer, bg=PANEL)
        body.pack(fill="both", expand=True)

        vsb = ttk.Scrollbar(body, orient="vertical",
                             style="Custom.Vertical.TScrollbar")
        vsb.pack(side="right", fill="y")

        def _sync_yview(*args):
            ta.yview(*args)
            tb.yview(*args)

        vsb.configure(command=_sync_yview)

        def _yscroll_a(first, last):
            vsb.set(first, last)
            tb.yview_moveto(first)

        def _yscroll_b(first, last):
            vsb.set(first, last)
            ta.yview_moveto(first)

        common = dict(bg=PANEL, font=(FONT_MONO, 10), relief="flat",
                      state="disabled", wrap="none",
                      selectbackground=SURFACE2, selectforeground=TEXT)

        ta = tk.Text(body, fg=GREEN,   **common, yscrollcommand=_yscroll_a)
        tb = tk.Text(body, fg=YELLOW,  **common, yscrollcommand=_yscroll_b)

        ta.tag_configure("diff", foreground=RED, background="#3d1a24")
        tb.tag_configure("diff", foreground=RED, background="#3d1a24")

        ta.pack(side="left", fill="both", expand=True)
        tk.Frame(body, bg=SURFACE2, width=1).pack(side="left", fill="y")
        tb.pack(side="left", fill="both", expand=True)

        return ta, tb

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------
    def _open_a(self):
        path = filedialog.askopenfilename(
            title="Open File A",
            filetypes=[("XQCN files", "*.xqcn"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self._entries_a = parse_xqcn(path)
            self._file_a = path
            name = path.split("/")[-1].split("\\")[-1]
            self._lbl_a.configure(text=name)
            self._diff = {}
            self._mode = "single"
            self._populate_tree_single()
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to open File A:\n{exc}")

    def _open_b(self):
        path = filedialog.askopenfilename(
            title="Open File B",
            filetypes=[("XQCN files", "*.xqcn"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self._entries_b = parse_xqcn(path)
            self._file_b = path
            name = path.split("/")[-1].split("\\")[-1]
            self._lbl_b.configure(text=name)
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to open File B:\n{exc}")

    def _export_text_file(self):
        if self._mode == "none":
            messagebox.showwarning("Export", "No file loaded. Please open a file first.")
            return

        path = filedialog.asksaveasfilename(
            title="Export Report",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        import datetime
        lines = []
        SEP  = "=" * 80
        DASH = "-" * 80

        # --- Header ---
        lines += [
            SEP,
            "XQCN PowerTools — Export Report",
            f"Generated : {datetime.datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}",
            SEP,
            "",
        ]
        name_a = (self._file_a or "(none)").replace("\\", "/").rsplit("/", 1)[-1]
        name_b = (self._file_b or "(none)").replace("\\", "/").rsplit("/", 1)[-1]
        lines += [
            f"File A : {name_a}",
            f"File B : {name_b}",
            "",
        ]

        # --- Stats ---
        filter_val = self._filter_var.get()
        search     = self._search_var.get()

        if self._mode == "diff":
            total   = len(self._diff)
            match   = sum(1 for s, _, __ in self._diff.values() if s == "match")
            only_a  = sum(1 for s, _, __ in self._diff.values() if s == "only_a")
            only_b  = sum(1 for s, _, __ in self._diff.values() if s == "only_b")
            differ  = sum(1 for s, _, __ in self._diff.values() if s == "differ")
            lines += [
                f"Total: {total}  |  Match: {match}  |  Only A: {only_a}  |  Only B: {only_b}  |  Different: {differ}",
            ]
        else:
            lines += [f"Total: {len(self._entries_a)}"]

        lines += [
            f"Filter : {filter_val}" + (f"  |  Search: \"{search}\"" if search else ""),
            "",
            SEP,
            "",
        ]

        # --- Entries (respect active filter + search) ---
        status_label = {
            "match":  "Match  ",
            "only_a": "Only A ",
            "only_b": "Only B ",
            "differ": "Differ ",
            "single": "       ",
        }
        fmap = {
            "Match": "match", "Only A": "only_a",
            "Only B": "only_b", "Different": "differ",
        }

        if self._mode == "single":
            source_items = [
                ("single", e, None)
                for e in self._entries_a.values()
            ]
        else:
            source_items = [
                (status, ea, eb)
                for status, ea, eb in self._diff.values()
            ]

        # Filter
        filtered = []
        for item in source_items:
            status, ea, eb = item
            entry  = ea if ea is not None else eb
            nv_name = self._lookup_nv_name(entry)
            if filter_val != "All" and status != fmap.get(filter_val):
                continue
            if search and (
                search.lower() not in entry.label.lower()
                and search.lower() not in entry.section.lower()
                and search.lower() not in nv_name.lower()
            ):
                continue
            filtered.append((status, ea, eb, entry, nv_name))

        # Group by section
        sections: dict = {}
        for item in filtered:
            sections.setdefault(item[3].section, []).append(item)

        for sec in sorted(sections):
            items = sections[sec]
            # Sort: differ first, then only_a/only_b, then match
            items.sort(key=lambda x: (
                x[0] not in ("differ",),
                x[0] not in ("only_a", "only_b"),
                x[3].label,
            ))
            lines += [f"[ {sec} ]  ({len(items)} entries)", DASH]
            for status, ea, eb, entry, nv_name in items:
                tag = status_label.get(status, "       ")
                len_a = f"{ea.length} bytes" if ea else "—"
                len_b = f"{eb.length} bytes" if eb else "—"
                lines.append(f"  [{tag.strip()}]  {entry.label}")
                if nv_name:
                    lines.append(f"            NV Name : {nv_name}")
                    nv_desc = self._lookup_nv_desc(entry)
                    if nv_desc:
                        lines.append(f"            Desc    : {nv_desc}")
                if self._mode == "diff":
                    lines.append(f"            Len A   : {len_a}   Len B : {len_b}")
                else:
                    lines.append(f"            Length  : {len_a}")
                lines.append("")
            lines.append("")

        if not sections:
            lines.append("  (no entries match the current filter)")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            messagebox.showinfo("Export", f"Report saved to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc))

    def _run_compare(self):
        if not self._entries_a:
            messagebox.showwarning("Warning", "Please open File A first.")
            return
        if not self._entries_b:
            messagebox.showwarning("Warning", "Please open File B first.")
            return
        self._diff = diff_entries(self._entries_a, self._entries_b)
        self._mode = "diff"
        self._populate_tree_diff()

    # ------------------------------------------------------------------
    # Tree population
    # ------------------------------------------------------------------
    def _clear_tree(self):
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        self._tree_items = {}

    def _update_stats(self, entries_for_total: dict, diff: dict):
        total = len(entries_for_total)
        match   = sum(1 for s, _, __ in diff.values() if s == "match")
        only_a  = sum(1 for s, _, __ in diff.values() if s == "only_a")
        only_b  = sum(1 for s, _, __ in diff.values() if s == "only_b")
        differ  = sum(1 for s, _, __ in diff.values() if s == "differ")

        self._stat_labels["total"].configure(text=f"Total: {total}")
        self._stat_labels["match"].configure(text=f"Match: {match}")
        self._stat_labels["only_a"].configure(text=f"Only A: {only_a}")
        self._stat_labels["only_b"].configure(text=f"Only B: {only_b}")
        self._stat_labels["differ"].configure(text=f"Different: {differ}")

    def _populate_tree_single(self):
        self._clear_tree()
        self._update_stats(self._entries_a, {})
        # Group by section
        sections: dict[str, list] = {}
        for key, entry in self._entries_a.items():
            sections.setdefault(entry.section, []).append(entry)

        search = self._search_var.get().lower()

        for sec in sorted(sections):
            entries = sorted(sections[sec], key=lambda e: e.label)
            filtered = []
            for e in entries:
                nv_name = self._lookup_nv_name(e)
                if not search or search in e.label.lower() or search in sec.lower() or search in nv_name.lower():
                    filtered.append((e, nv_name))
            if not filtered:
                continue
            sec_iid = self._tree.insert(
                "", "end",
                values=(f"  {sec}", "", "", "", "", ""),
                tags=("section_header",),
            )
            for entry, nv_name in filtered:
                iid = self._tree.insert(
                    sec_iid, "end",
                    values=(entry.label, nv_name, entry.section, "", entry.length, ""),
                    tags=("neutral",),
                    open=False,
                )
                self._tree_items[iid] = entry.key
            self._tree.item(sec_iid, open=True)

    def _populate_tree_diff(self):
        self._clear_tree()
        all_entries = {}
        for key, (status, ea, eb) in self._diff.items():
            entry = ea if ea is not None else eb
            all_entries[key] = entry
        self._update_stats(all_entries, self._diff)

        filter_val = self._filter_var.get()
        search     = self._search_var.get().lower()

        status_map = {
            "match":  ("Match",   "match"),
            "only_a": ("Only A",  "only_one"),
            "only_b": ("Only B",  "only_one"),
            "differ": ("Differ",  "differ"),
        }

        # Group by section
        sections: dict[str, list] = {}
        for key, (status, ea, eb) in self._diff.items():
            entry = ea if ea is not None else eb
            sections.setdefault(entry.section, []).append((key, status, ea, eb))

        for sec in sorted(sections):
            items = sorted(sections[sec], key=lambda x: (x[1] != "differ", x[1] != "only_a", x[1] != "only_b", (x[2] or x[3]).label))
            filtered = []
            for key, status, ea, eb in items:
                entry = ea if ea is not None else eb
                nv_name = self._lookup_nv_name(entry)
                # Filter by radio
                if filter_val != "All":
                    fmap = {
                        "Match": "match",
                        "Only A": "only_a",
                        "Only B": "only_b",
                        "Different": "differ",
                    }
                    if status != fmap.get(filter_val):
                        continue
                # Filter by search
                if search and search not in entry.label.lower() and search not in sec.lower() and search not in nv_name.lower():
                    continue
                filtered.append((key, status, ea, eb, nv_name))

            if not filtered:
                continue

            sec_iid = self._tree.insert(
                "", "end",
                values=(f"  {sec}", "", "", "", "", ""),
                tags=("section_header",),
            )
            for key, status, ea, eb, nv_name in filtered:
                entry = ea if ea is not None else eb
                status_text, tag = status_map.get(status, (status, "neutral"))
                len_a = ea.length if ea else ""
                len_b = eb.length if eb else ""
                iid = self._tree.insert(
                    sec_iid, "end",
                    values=(entry.label, nv_name, entry.section, status_text, len_a, len_b),
                    tags=(tag,),
                )
                self._tree_items[iid] = key
            self._tree.item(sec_iid, open=True)

    def _apply_filter(self):
        if self._mode == "single":
            self._populate_tree_single()
        elif self._mode == "diff":
            self._populate_tree_diff()

    # ------------------------------------------------------------------
    # Selection handler
    # ------------------------------------------------------------------
    def _on_tree_select(self, event):
        sel = self._tree.selection()
        if not sel:
            return
        iid = sel[0]
        key = self._tree_items.get(iid)
        if key is None:
            # Section header row
            self._clear_detail()
            return

        if self._mode == "single":
            ea = self._entries_a.get(key)
            eb = None
            status = "single"
        elif self._mode == "diff":
            diff_entry = self._diff.get(key)
            if diff_entry is None:
                self._clear_detail()
                return
            status, ea, eb = diff_entry
        else:
            self._clear_detail()
            return

        self._populate_detail(key, status, ea, eb)

    def _clear_detail(self):
        for txt in (self._info_text, self._text_a, self._text_b,
                    self._diff_text_a, self._diff_text_b):
            self._set_text(txt, "")

    def _populate_detail(self, key: str, status: str, ea, eb):
        entry = ea if ea is not None else eb

        # Info tab
        nv_name = self._lookup_nv_name(entry)
        nv_desc = self._lookup_nv_desc(entry)
        info_lines = [
            f"Path/Name : {entry.label}",
        ]
        if nv_name:
            info_lines.append(f"NV Name   : {nv_name}")
        if nv_desc:
            info_lines.append(f"Description: {nv_desc}")
        info_lines += [
            f"Section   : {entry.section}",
            f"Key       : {key}",
            f"Status    : {status}",
        ]
        if ea:
            info_lines.append(f"Length A  : {ea.length} bytes")
        if eb:
            info_lines.append(f"Length B  : {eb.length} bytes")
        self._set_text(self._info_text, "\n".join(info_lines))

        # Payload A
        if ea:
            self._set_text(self._text_a, hex_dump(ea.hex_value))
        else:
            self._set_text(self._text_a, "(not present in File A)")

        # Payload B
        if eb:
            self._set_text(self._text_b, hex_dump(eb.hex_value))
        else:
            self._set_text(self._text_b, "(not present in File B)")

        # Diff View
        if ea and eb:
            self._populate_diff_view(ea.hex_value, eb.hex_value)
        else:
            self._set_text(self._diff_text_a, "(load both files and compare to use Diff View)")
            self._set_text(self._diff_text_b, "")

    def _set_text(self, widget: tk.Text, content: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    # ------------------------------------------------------------------
    # Diff View population
    # ------------------------------------------------------------------
    def _populate_diff_view(self, hex_a: str, hex_b: str):
        """Fill the side-by-side diff pane, tagging differing bytes."""
        bytes_a = hex_a.strip().split() if hex_a.strip() else []
        bytes_b = hex_b.strip().split() if hex_b.strip() else []
        max_len = max(len(bytes_a), len(bytes_b), 1)
        COLS    = 16

        for w in (self._diff_text_a, self._diff_text_b):
            w.configure(state="normal")
            w.delete("1.0", "end")

        for i in range(0, max_len, COLS):
            chunk_a = bytes_a[i:i + COLS]
            chunk_b = bytes_b[i:i + COLS]
            offset  = f"{i:04X}  "

            for w, chunk, other in (
                (self._diff_text_a, chunk_a, chunk_b),
                (self._diff_text_b, chunk_b, chunk_a),
            ):
                w.insert("end", offset)
                # Hex bytes
                for j in range(COLS):
                    if j < len(chunk):
                        byte    = chunk[j]
                        is_diff = j >= len(other) or byte != other[j]
                        w.insert("end", byte + " ", ("diff",) if is_diff else ())
                    else:
                        w.insert("end", "   ")
                w.insert("end", " ")
                # ASCII
                for j in range(COLS):
                    if j < len(chunk):
                        byte    = chunk[j]
                        b       = int(byte, 16)
                        char    = chr(b) if 0x20 <= b < 0x7F else "."
                        is_diff = j >= len(other) or byte != other[j]
                        w.insert("end", char, ("diff",) if is_diff else ())
                    else:
                        w.insert("end", " ")
                w.insert("end", "\n")

        for w in (self._diff_text_a, self._diff_text_b):
            w.configure(state="disabled")

    # ------------------------------------------------------------------
    # XQCN Editor panel
    # ------------------------------------------------------------------
    def _build_xqcn_editor_panel(self, content_host: tk.Frame):
        panel = tk.Frame(content_host, bg=BG)
        self._panels["xqcn_editor"] = panel

        # Experimental warning banner
        tk.Label(
            panel,
            text="⚠  EXPERIMENTAL  —  This feature is under active development and may contain bugs."
                 "  Always verify saved files before use and keep backups of originals.  ⚠",
            bg="#45375a", fg="#f9e2af",
            font=(FONT_UI, 9, "bold"),
            anchor="center", pady=5,
        ).pack(fill="x")

        # Header bar
        hf    = tk.Frame(panel, bg=PANEL)
        hf.pack(fill="x")
        inner = tk.Frame(hf, bg=PANEL)
        inner.pack(fill="x", padx=10, pady=8)

        tk.Button(
            inner, text="Open…",
            command=self._editor_open_file,
            bg=SURFACE, fg=TEXT,
            activebackground=SURFACE2, activeforeground=TEXT,
            relief="flat", padx=10, pady=4,
            font=(FONT_UI, 10), cursor="hand2",
            highlightthickness=0,
        ).pack(side="left", padx=(0, 8))

        self._editor_lbl = tk.Label(
            inner, text="(no file loaded)",
            bg=PANEL, fg=SUBTEXT,
            font=(FONT_UI, 10), anchor="w",
        )
        self._editor_lbl.pack(side="left", fill="x", expand=True, padx=(0, 12))

        tk.Button(
            inner, text="💾 Save As…",
            command=self._editor_save,
            bg=ACCENT, fg=PANEL,
            activebackground="#74a8f5", activeforeground=PANEL,
            relief="flat", padx=14, pady=4,
            font=(FONT_UI, 10, "bold"), cursor="hand2",
            highlightthickness=0,
        ).pack(side="right", padx=(4, 0))

        for text, cmd in (
            ("☐ Deselect All", self._editor_deselect_all),
            ("☑ Select All",   self._editor_select_all),
        ):
            tk.Button(
                inner, text=text, command=cmd,
                bg=SURFACE, fg=SUBTEXT,
                activebackground=SURFACE2, activeforeground=TEXT,
                relief="flat", padx=10, pady=4,
                font=(FONT_UI, 10), cursor="hand2",
                highlightthickness=0,
            ).pack(side="right", padx=(4, 0))

        # Stats bar
        sf = tk.Frame(panel, bg=BG)
        sf.pack(fill="x", padx=10, pady=4)
        self._editor_stat_labels = {}
        for key, text, colour in (
            ("total",    "Total: 0",    TEXT),
            ("included", "Included: 0", GREEN),
            ("removed",  "Removed: 0",  RED),
        ):
            lbl = tk.Label(sf, text=text, bg=BG, fg=colour,
                           font=(FONT_UI, 10, "bold"))
            lbl.pack(side="left", padx=(0, 20))
            self._editor_stat_labels[key] = lbl

        ttk.Separator(panel, orient="horizontal").pack(fill="x")

        # Treeview
        tf   = tk.Frame(panel, bg=BG)
        tf.pack(fill="both", expand=True)

        cols = ("check", "name", "nv_name", "section", "length")
        self._editor_tree = ttk.Treeview(
            tf, columns=cols, show="headings",
            style="Custom.Treeview", selectmode="browse",
        )
        self._editor_tree.heading("check",   text="✓",           anchor="center")
        self._editor_tree.heading("name",    text="Name / Path", anchor="w")
        self._editor_tree.heading("nv_name", text="NV Name",     anchor="w")
        self._editor_tree.heading("section", text="Section",     anchor="w")
        self._editor_tree.heading("length",  text="Len",         anchor="e")

        self._editor_tree.column("check",   width=50,  stretch=False, anchor="center")
        self._editor_tree.column("name",    width=400, stretch=True,  anchor="w")
        self._editor_tree.column("nv_name", width=260, stretch=True,  anchor="w")
        self._editor_tree.column("section", width=160, stretch=False, anchor="w")
        self._editor_tree.column("length",  width=70,  stretch=False, anchor="e")

        self._editor_tree.tag_configure("included",  foreground=TEXT)
        self._editor_tree.tag_configure("removed",   foreground=SURFACE2)
        self._editor_tree.tag_configure("sec_header",
                                         foreground=ACCENT,
                                         font=(FONT_UI, 10, "bold"),
                                         background=PANEL)

        vsb = ttk.Scrollbar(tf, orient="vertical",   command=self._editor_tree.yview,
                             style="Custom.Vertical.TScrollbar")
        hsb = ttk.Scrollbar(tf, orient="horizontal", command=self._editor_tree.xview,
                             style="Custom.Horizontal.TScrollbar")
        self._editor_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right",  fill="y")
        hsb.pack(side="bottom", fill="x")
        self._editor_tree.pack(fill="both", expand=True)

        self._editor_tree.bind("<Button-1>",        self._editor_on_click)
        self._editor_tree.bind("<Double-Button-1>", self._editor_on_double_click)

    # ------------------------------------------------------------------
    # Editor file operations
    # ------------------------------------------------------------------
    def _editor_open_file(self):
        path = filedialog.askopenfilename(
            title="Open XQCN for editing",
            filetypes=[("XQCN files", "*.xqcn"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self._editor_entries = parse_xqcn(path)
            self._editor_file    = path
            self._editor_checked = set(self._editor_entries.keys())
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            self._editor_lbl.configure(text=name, fg=TEXT)
            self._editor_populate_tree()
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to open file:\n{exc}")

    def _editor_populate_tree(self):
        for iid in self._editor_tree.get_children():
            self._editor_tree.delete(iid)
        self._editor_tree_items    = {}
        self._editor_section_iids  = {}
        self._editor_section_items = {}

        sections: dict[str, list] = {}
        for key, entry in self._editor_entries.items():
            sections.setdefault(entry.section, []).append(entry)

        for sec in sorted(sections):
            entries = sorted(sections[sec], key=lambda e: e.label)
            total   = len(entries)
            n_on    = sum(1 for e in entries if e.key in self._editor_checked)
            sym     = "☑" if n_on == total else ("☐" if n_on == 0 else "◑")

            sec_iid = self._editor_tree.insert(
                "", "end",
                values=(f"{sym} {n_on}/{total}", f"  {sec}", "", "", ""),
                tags=("sec_header",),
            )
            self._editor_section_iids[sec]  = sec_iid
            self._editor_section_items[sec] = []

            for entry in entries:
                nv_name = self._lookup_nv_name(entry)
                check   = "☑" if entry.key in self._editor_checked else "☐"
                tag     = "included" if entry.key in self._editor_checked else "removed"
                iid = self._editor_tree.insert(
                    sec_iid, "end",
                    values=(check, entry.label, nv_name, entry.section, entry.length),
                    tags=(tag,),
                )
                self._editor_tree_items[iid] = entry.key
                self._editor_section_items[sec].append(iid)

            self._editor_tree.item(sec_iid, open=True)

        self._editor_update_stats()

    def _editor_update_stats(self):
        total    = len(self._editor_entries)
        included = len(self._editor_checked)
        self._editor_stat_labels["total"].configure(text=f"Total: {total}")
        self._editor_stat_labels["included"].configure(text=f"Included: {included}")
        self._editor_stat_labels["removed"].configure(text=f"Removed: {total - included}")

    def _editor_refresh_row(self, iid: str, key: str):
        entry   = self._editor_entries[key]
        nv_name = self._lookup_nv_name(entry)
        check   = "☑" if key in self._editor_checked else "☐"
        tag     = "included" if key in self._editor_checked else "removed"
        self._editor_tree.item(iid,
                                values=(check, entry.label, nv_name, entry.section, entry.length),
                                tags=(tag,))

    def _editor_refresh_section_header(self, sec: str):
        iids    = self._editor_section_items.get(sec, [])
        total   = len(iids)
        n_on    = sum(1 for iid in iids
                      if self._editor_tree_items.get(iid) in self._editor_checked)
        sym     = "☑" if n_on == total else ("☐" if n_on == 0 else "◑")
        sec_iid = self._editor_section_iids.get(sec)
        if sec_iid:
            old = self._editor_tree.item(sec_iid, "values")
            self._editor_tree.item(sec_iid,
                                    values=(f"{sym} {n_on}/{total}", old[1], old[2], old[3], old[4]))

    def _editor_on_click(self, event):
        region = self._editor_tree.identify_region(event.x, event.y)
        col    = self._editor_tree.identify_column(event.x)
        iid    = self._editor_tree.identify_row(event.y)
        if not iid or region != "cell" or col != "#1":
            return

        key = self._editor_tree_items.get(iid)
        if key is None:
            # Section header — toggle all items in section
            sec_name = self._editor_tree.item(iid, "values")[1].strip()
            self._editor_toggle_section(sec_name)
        else:
            if key in self._editor_checked:
                self._editor_checked.discard(key)
            else:
                self._editor_checked.add(key)
            self._editor_refresh_row(iid, key)
            self._editor_refresh_section_header(self._editor_entries[key].section)
            self._editor_update_stats()

    def _editor_toggle_section(self, sec: str):
        iids   = self._editor_section_items.get(sec, [])
        keys   = [self._editor_tree_items[i] for i in iids if self._editor_tree_items.get(i)]
        all_on = all(k in self._editor_checked for k in keys)
        for iid, key in zip(iids, keys):
            if all_on:
                self._editor_checked.discard(key)
            else:
                self._editor_checked.add(key)
            self._editor_refresh_row(iid, key)
        self._editor_refresh_section_header(sec)
        self._editor_update_stats()

    def _editor_on_double_click(self, event):
        iid = self._editor_tree.identify_row(event.y)
        if not iid:
            return
        key = self._editor_tree_items.get(iid)
        if key is not None:
            self._editor_show_preview(key)

    def _editor_show_preview(self, key: str):
        entry = self._editor_entries.get(key)
        if entry is None:
            return
        nv_name = self._lookup_nv_name(entry)
        nv_desc = self._lookup_nv_desc(entry)

        win = tk.Toplevel(self)
        win.title(f"Preview — {entry.label}")
        win.geometry("760x520")
        win.configure(bg=BG)
        win.resizable(True, True)

        info_lines = [f"Path/Name : {entry.label}"]
        if nv_name:
            info_lines.append(f"NV Name   : {nv_name}")
        if nv_desc:
            info_lines.append(f"Desc      : {nv_desc}")
        info_lines += [
            f"Section   : {entry.section}",
            f"Length    : {entry.length} bytes",
        ]
        tk.Label(win, text="\n".join(info_lines),
                 bg=BG, fg=TEXT, font=(FONT_MONO, 10),
                 anchor="w", justify="left").pack(fill="x", padx=14, pady=(12, 6))
        ttk.Separator(win, orient="horizontal").pack(fill="x", padx=14)

        hf  = tk.Frame(win, bg=PANEL)
        hf.pack(fill="both", expand=True, padx=14, pady=8)
        vsb = ttk.Scrollbar(hf, orient="vertical",   style="Custom.Vertical.TScrollbar")
        hsb = ttk.Scrollbar(hf, orient="horizontal", style="Custom.Horizontal.TScrollbar")
        txt = tk.Text(hf, bg=PANEL, fg=GREEN, font=(FONT_MONO, 10),
                      relief="flat", state="disabled", wrap="none",
                      selectbackground=SURFACE2, selectforeground=TEXT,
                      yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.configure(command=txt.yview)
        hsb.configure(command=txt.xview)
        vsb.pack(side="right",  fill="y")
        hsb.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True)
        txt.configure(state="normal")
        txt.insert("1.0", hex_dump(entry.hex_value) if entry.hex_value.strip() else "(empty payload)")
        txt.configure(state="disabled")

        tk.Button(win, text="Close", command=win.destroy,
                  bg=SURFACE, fg=TEXT,
                  activebackground=SURFACE2, activeforeground=TEXT,
                  relief="flat", padx=20, pady=6,
                  font=(FONT_UI, 10), cursor="hand2",
                  highlightthickness=0).pack(pady=(0, 12))

    def _editor_select_all(self):
        self._editor_checked = set(self._editor_entries.keys())
        self._editor_populate_tree()

    def _editor_deselect_all(self):
        self._editor_checked.clear()
        self._editor_populate_tree()

    def _editor_save(self):
        if not self._editor_file:
            messagebox.showwarning("Save", "No file loaded.")
            return
        fname    = self._editor_file.replace("\\", "/").rsplit("/", 1)
        dirname  = fname[0] if len(fname) == 2 else "."
        basename = fname[-1]
        stem, ext = (basename.rsplit(".", 1) if "." in basename else (basename, "xqcn"))
        default_name = f"{stem}_edited.{ext}"

        dest = filedialog.asksaveasfilename(
            title="Save edited XQCN",
            initialdir=dirname,
            initialfile=default_name,
            defaultextension=f".{ext}",
            filetypes=[("XQCN files", "*.xqcn"), ("All files", "*.*")],
        )
        if not dest:
            return
        try:
            save_xqcn_filtered(self._editor_file, dest, self._editor_checked)
            removed = len(self._editor_entries) - len(self._editor_checked)
            messagebox.showinfo("Saved",
                                f"Saved to:\n{dest}\n\n"
                                f"{len(self._editor_checked)} entries kept, "
                                f"{removed} removed.")
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    app = XQCNPowerTools()
    app.mainloop()


if __name__ == "__main__":
    main()
