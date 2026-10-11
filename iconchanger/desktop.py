"""Finding, reading and editing freedesktop.org ``.desktop`` entries.

This module has no Qt dependency so it can be used by the headless
``--sync`` mode as well as by the GUI.
"""
from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from pathlib import Path

MAIN_GROUP = "Desktop Entry"

# Keys we add to entries we changed, so they can always be recognised and
# reverted - even if our own state file is lost.
KEY_ORIGINAL_ICON = "X-IconChanger-OriginalIcon"
KEY_MODE = "X-IconChanger-Mode"

MODE_OVERRIDE = "override"  # we created a user-level copy of a system entry
MODE_IN_PLACE = "in-place"  # we edited an entry that already lived in the user dir


# --------------------------------------------------------------------------
# XDG locations
# --------------------------------------------------------------------------

def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def extra_data_dirs() -> list[Path]:
    """Places that hold app entries but are sometimes missing from XDG_DATA_DIRS
    (e.g. when started from a non-login shell or a systemd unit)."""
    return [
        data_home() / "flatpak" / "exports" / "share",
        Path("/var/lib/flatpak/exports/share"),
        Path("/var/lib/snapd/desktop"),
    ]


def data_dirs() -> list[Path]:
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs: list[Path] = []
    for item in (Path(p) for p in raw.split(":") if p):
        if item not in dirs:
            dirs.append(item)
    for item in extra_data_dirs():
        if item not in dirs and item.is_dir():
            dirs.append(item)
    return dirs


def user_apps_dir() -> Path:
    return data_home() / "applications"


def system_apps_dirs() -> list[Path]:
    """Application directories below the user's own, in precedence order."""
    user = _real(user_apps_dir())
    return [d / "applications" for d in data_dirs() if _real(d / "applications") != user]


def _real(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")


def parse_main_group(text: str) -> dict[str, str]:
    """Return the raw key/values of the [Desktop Entry] group."""
    values: dict[str, str] = {}
    group = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("["):
            if group == MAIN_GROUP:
                break
            group = stripped.strip("[]")
            continue
        if group == MAIN_GROUP and "=" in stripped:
            key, value = stripped.split("=", 1)
            values.setdefault(key.strip(), value.strip())
    return values


def unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append({"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}.get(value[i + 1], value[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


@functools.lru_cache(maxsize=1)
def _locale_suffixes() -> tuple[str, ...]:
    lang = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    lang = lang.split(".")[0].split("@")[0]
    if not lang or lang in ("C", "POSIX"):
        return ("",)
    suffixes = [f"[{lang}]"]
    if "_" in lang:
        suffixes.append(f"[{lang.split('_')[0]}]")
    return (*suffixes, "")


def localized(values: dict[str, str], key: str) -> str:
    for suffix in _locale_suffixes():
        if key + suffix in values:
            return unescape(values[key + suffix])
    return ""


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _current_desktops() -> set[str]:
    return {d.casefold() for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d}


def _hidden_for_desktop(values: dict[str, str]) -> bool:
    desktops = _current_desktops()
    only = {d.casefold() for d in values.get("OnlyShowIn", "").split(";") if d}
    never = {d.casefold() for d in values.get("NotShowIn", "").split(";") if d}
    if only and desktops and not (only & desktops):
        return True
    return bool(never & desktops)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

@dataclass
class Location:
    """Where the files for one desktop-file ID live."""

    user_file: Path | None = None    # in ~/.local/share/applications (wins if present)
    system_file: Path | None = None  # highest-precedence file anywhere else


def index() -> dict[str, Location]:
    """Map every desktop-file ID to its user-level and system-level files."""
    found: dict[str, Location] = {}
    bases = [(user_apps_dir(), True)] + [(d, False) for d in system_apps_dirs()]
    for base, is_user in bases:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.desktop")):
            if not path.is_file():  # broken symlink or similar
                continue
            desktop_id = "-".join(path.relative_to(base).parts)
            loc = found.setdefault(desktop_id, Location())
            if is_user:
                loc.user_file = loc.user_file or path
            else:
                loc.system_file = loc.system_file or path
    return found


@dataclass
class DesktopEntry:
    id: str
    path: Path                # the file that is actually in effect
    name: str
    generic_name: str
    comment: str
    keywords: str
    icon: str                 # Icon= value currently in effect
    no_display: bool          # not shown in menus (NoDisplay / OnlyShowIn / NotShowIn)
    mode: str | None          # set when we changed this entry's icon
    original_icon: str        # Icon= value before we changed it

    @property
    def customized(self) -> bool:
        return self.mode is not None

    def matches(self, query: str) -> bool:
        haystack = " ".join((self.name, self.generic_name, self.id, self.keywords)).casefold()
        return all(term in haystack for term in query.casefold().split())


def load_entry(desktop_id: str, path: Path) -> DesktopEntry | None:
    try:
        values = parse_main_group(read_text(path))
    except OSError:
        return None
    if values.get("Type") != "Application" or _is_true(values.get("Hidden")):
        return None
    mode = values.get(KEY_MODE) or None
    icon = unescape(values.get("Icon", ""))
    return DesktopEntry(
        id=desktop_id,
        path=path,
        name=localized(values, "Name") or desktop_id.removesuffix(".desktop"),
        generic_name=localized(values, "GenericName"),
        comment=localized(values, "Comment"),
        keywords=localized(values, "Keywords").replace(";", " "),
        icon=icon,
        no_display=_is_true(values.get("NoDisplay")) or _hidden_for_desktop(values),
        mode=mode,
        original_icon=unescape(values.get(KEY_ORIGINAL_ICON, "")) if mode else icon,
    )


def scan() -> list[DesktopEntry]:
    """All installed applications, sorted by name."""
    entries = []
    for desktop_id, loc in index().items():
        entry = load_entry(desktop_id, loc.user_file or loc.system_file)
        if entry:
            entries.append(entry)
    entries.sort(key=lambda e: (e.name.casefold(), e.id))
    return entries


# --------------------------------------------------------------------------
# Editing
# --------------------------------------------------------------------------

def set_keys(text: str, updates: dict[str, str | None], drop_localized: tuple[str, ...] = ("Icon",)) -> str:
    """Set keys in the [Desktop Entry] group (``None`` removes the key).

    Existing keys keep their position, new keys go to the end of the group and
    every other line - comments, actions, translations - is left untouched.
    Localized variants (``Icon[de]=``) of ``drop_localized`` keys are removed so
    they can't shadow the new value.
    """
    out: list[str] = []
    pending = dict(updates)
    group = None
    seen_main = False

    def flush() -> None:
        insert_at = len(out)
        while insert_at and not out[insert_at - 1].strip():
            insert_at -= 1
        out[insert_at:insert_at] = [f"{k}={v}" for k, v in pending.items() if v is not None]
        pending.clear()

    for line in text.lstrip("\ufeff").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            if group == MAIN_GROUP:
                flush()
            group = stripped.strip("[]")
            seen_main = seen_main or group == MAIN_GROUP
        elif group == MAIN_GROUP and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                if key in pending and pending[key] is not None:
                    out.append(f"{key}={pending[key]}")
                pending.pop(key, None)
                continue  # replaced, removed, or a duplicate
            base = key.split("[", 1)[0]
            if base != key and base in drop_localized and base in updates:
                continue
        out.append(line)

    if not seen_main:
        raise ValueError("not a valid .desktop file (no [Desktop Entry] group)")
    if group == MAIN_GROUP:
        flush()
    return "\n".join(out) + "\n"


def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
