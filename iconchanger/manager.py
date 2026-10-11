"""Applying, resetting and repairing custom icons (no Qt dependency).

How an icon change works
------------------------
Desktop environments, launchers (rofi, wofi, KDE, GNOME, ...), docks and
taskbars all pick the *first* ``<id>.desktop`` they find, and
``~/.local/share/applications`` is searched before the system directories.

* System apps get a copy of their entry in the user directory with a new
  ``Icon=`` line (mode ``override``). The package's own file is never touched,
  so pacman updates can't undo the change, and reset just deletes the copy.
* Entries that already live in the user directory (Steam games, web apps, ...)
  are edited in place after a backup is taken (mode ``in-place``).

Every chosen image is copied into ``~/.local/share/icon-changer/icons`` first,
so moving or deleting the original picture doesn't break the icon.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import APP_ID, desktop
from .desktop import KEY_MODE, KEY_ORIGINAL_ICON, MODE_IN_PLACE, MODE_OVERRIDE

SYNC_UNIT = f"{APP_ID}-sync"


def storage_dir() -> Path:
    return desktop.data_home() / APP_ID


def icons_dir() -> Path:
    return storage_dir() / "icons"


def backups_dir() -> Path:
    return storage_dir() / "backups"


def state_file() -> Path:
    return storage_dir() / "state.json"


def sha256(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    """Write via a temp file + rename, which also replaces a symlink with a real file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


@dataclass
class Customization:
    desktop_id: str
    icon: str                       # stored image in icons_dir()
    mode: str                       # MODE_OVERRIDE or MODE_IN_PLACE
    original_icon: str = ""
    source_sha256: str | None = None   # override: system file the copy was made from
    written_sha256: str | None = None  # in-place: content we wrote (to know if it was edited since)
    suspended: bool = False            # override removed because the app was uninstalled
    applied_at: float = 0.0


class IconManager:
    def __init__(self, refresh_caches: bool = True, background: bool = True):
        """``background``: refresh desktop caches in a thread (GUI) instead of
        waiting for them (command line, where the process exits right after)."""
        self.customizations: dict[str, Customization] = {}
        self.settings: dict = {"auto_sync": True}
        self._refresh_caches = refresh_caches and not os.environ.get("ICON_CHANGER_TESTING")
        self._background = background
        self.load()

    # ------------------------------------------------------------------ state

    def load(self) -> None:
        try:
            data = json.loads(state_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        known = {f.name for f in fields(Customization)}
        for desktop_id, item in data.get("customizations", {}).items():
            try:
                self.customizations[desktop_id] = Customization(**{k: v for k, v in item.items() if k in known})
            except TypeError:
                continue
        self.settings.update(data.get("settings", {}))

    def save(self) -> None:
        data = {
            "version": 1,
            "settings": self.settings,
            "customizations": {k: asdict(v) for k, v in sorted(self.customizations.items())},
        }
        write_atomic(state_file(), json.dumps(data, indent=2) + "\n", 0o600)

    def icons_in_use(self) -> set[Path]:
        return {Path(c.icon) for c in self.customizations.values()}

    # ------------------------------------------------------------ operations

    def apply(self, desktop_id: str, icon_path: Path) -> None:
        """Make ``desktop_id`` use ``icon_path`` (an image already in icons_dir())."""
        icon_path = Path(icon_path).absolute()
        if not icon_path.is_file():
            raise FileNotFoundError(f"Image not found: {icon_path}")
        loc = desktop.index().get(desktop_id)
        if loc is None:
            raise LookupError(f"Application {desktop_id} is not installed")

        user_file = loc.user_file
        user_values = desktop.parse_main_group(desktop.read_text(user_file)) if user_file else {}
        user_mode = user_values.get(KEY_MODE)

        if user_file and user_mode != MODE_OVERRIDE:
            self._apply_in_place(desktop_id, user_file, user_values, icon_path)
        elif loc.system_file:
            self._apply_override(desktop_id, loc.system_file, user_file, icon_path)
        else:
            raise LookupError(f"Can't find the original entry for {desktop_id}")
        self.save()
        self.refresh_caches()

    def _apply_override(self, desktop_id: str, system_file: Path, user_file: Path | None, icon: Path) -> None:
        source = desktop.read_text(system_file)
        original = desktop.unescape(desktop.parse_main_group(source).get("Icon", ""))
        target = user_file or desktop.user_apps_dir() / desktop_id
        write_atomic(target, self._customized_text(source, icon, original, MODE_OVERRIDE))
        self.customizations[desktop_id] = Customization(
            desktop_id, str(icon), MODE_OVERRIDE, original,
            source_sha256=sha256(source), applied_at=time.time(),
        )

    def _apply_in_place(self, desktop_id: str, user_file: Path, values: dict, icon: Path) -> None:
        if values.get(KEY_MODE) == MODE_IN_PLACE:
            original = desktop.unescape(values.get(KEY_ORIGINAL_ICON, ""))
        else:
            original = desktop.unescape(values.get("Icon", ""))
            backup = backups_dir() / desktop_id
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists() or backup.is_symlink():
                backup.unlink()
            shutil.copy2(user_file, backup, follow_symlinks=False)
        text = self._customized_text(desktop.read_text(user_file), icon, original, MODE_IN_PLACE)
        write_atomic(user_file, text, _file_mode(user_file))
        self.customizations[desktop_id] = Customization(
            desktop_id, str(icon), MODE_IN_PLACE, original,
            written_sha256=sha256(text), applied_at=time.time(),
        )

    @staticmethod
    def _customized_text(text: str, icon: Path, original: str, mode: str) -> str:
        return desktop.set_keys(text, {
            "Icon": desktop.escape(str(icon)),
            KEY_ORIGINAL_ICON: desktop.escape(original),
            KEY_MODE: mode,
        })

    def reset(self, desktop_id: str) -> None:
        """Give ``desktop_id`` its original icon back."""
        loc = desktop.index().get(desktop_id)
        user_file = loc.user_file if loc else None
        cust = self.customizations.pop(desktop_id, None)
        if user_file:
            values = desktop.parse_main_group(desktop.read_text(user_file))
            mode = values.get(KEY_MODE)
            if mode == MODE_OVERRIDE:
                user_file.unlink()
            elif mode == MODE_IN_PLACE:
                self._reset_in_place(desktop_id, user_file, values, cust)
        self.save()
        self.refresh_caches()

    def _reset_in_place(self, desktop_id: str, user_file: Path, values: dict, cust: Customization | None) -> None:
        backup = backups_dir() / desktop_id
        has_backup = backup.exists() or backup.is_symlink()
        current = desktop.read_text(user_file)
        if has_backup and cust and cust.written_sha256 == sha256(current):
            os.replace(backup, user_file)  # untouched since we changed it: restore exactly
            return
        # The file was edited after we changed it: only undo our own change.
        original = desktop.unescape(values.get(KEY_ORIGINAL_ICON, ""))
        text = desktop.set_keys(current, {
            "Icon": desktop.escape(original) if original else None,
            KEY_ORIGINAL_ICON: None,
            KEY_MODE: None,
        })
        write_atomic(user_file, text, _file_mode(user_file))
        if has_backup:
            backup.unlink()

    def reset_all(self) -> int:
        ids = set(self.customizations)
        ids |= {e.id for e in desktop.scan() if e.customized}
        for desktop_id in sorted(ids):
            self.reset(desktop_id)
        return len(ids)

    # ------------------------------------------------------------------ repair

    def sync(self) -> list[str]:
        """Keep custom icons healthy after apps are updated, removed or reinstalled.

        * app updated     -> rebuild our copy from the new system entry
        * app uninstalled -> remove our copy so no dead launcher is left behind
        * app reinstalled -> put the custom icon back
        Returns a human-readable list of what was done.
        """
        log: list[str] = []
        idx = desktop.index()
        self._adopt_unknown(idx, log)

        for desktop_id, cust in list(self.customizations.items()):
            loc = idx.get(desktop_id) or desktop.Location()
            user_file = loc.user_file
            user_mode = desktop.parse_main_group(desktop.read_text(user_file)).get(KEY_MODE) if user_file else None
            ours = user_mode == cust.mode

            if cust.mode == MODE_IN_PLACE:
                if not ours:
                    del self.customizations[desktop_id]
                    log.append(f"{desktop_id}: entry was removed or replaced, forgetting it")
                continue

            if loc.system_file is None:
                if ours:
                    user_file.unlink()
                    log.append(f"{desktop_id}: app uninstalled, custom icon put on hold")
                if user_file and not ours:
                    del self.customizations[desktop_id]
                else:
                    cust.suspended = True
            elif user_file and not ours:
                del self.customizations[desktop_id]
                log.append(f"{desktop_id}: replaced by another entry, forgetting it")
            elif user_file is None and not cust.suspended:
                del self.customizations[desktop_id]
                log.append(f"{desktop_id}: custom entry was deleted, forgetting it")
            else:
                source = desktop.read_text(loc.system_file)
                if cust.suspended or user_file is None or sha256(source) != cust.source_sha256:
                    target = user_file or desktop.user_apps_dir() / desktop_id
                    write_atomic(target, self._customized_text(source, Path(cust.icon), cust.original_icon, MODE_OVERRIDE))
                    log.append(f"{desktop_id}: " + ("app reinstalled, custom icon restored" if cust.suspended
                                                    else "app updated, custom icon re-applied"))
                    cust.source_sha256 = sha256(source)
                    cust.suspended = False
                    cust.original_icon = desktop.unescape(desktop.parse_main_group(source).get("Icon", ""))

            if desktop_id in self.customizations and not Path(cust.icon).exists():
                log.append(f"{desktop_id}: warning - stored image {cust.icon} is missing")

        self.save()
        if log:
            self.refresh_caches()
        return log

    def _adopt_unknown(self, idx: dict[str, desktop.Location], log: list[str]) -> None:
        """Re-learn entries we changed earlier if the state file was lost."""
        for desktop_id, loc in idx.items():
            if desktop_id in self.customizations or not loc.user_file:
                continue
            values = desktop.parse_main_group(desktop.read_text(loc.user_file))
            mode = values.get(KEY_MODE)
            if mode not in (MODE_OVERRIDE, MODE_IN_PLACE):
                continue
            text = desktop.read_text(loc.user_file)
            self.customizations[desktop_id] = Customization(
                desktop_id, desktop.unescape(values.get("Icon", "")), mode,
                desktop.unescape(values.get(KEY_ORIGINAL_ICON, "")),
                written_sha256=sha256(text) if mode == MODE_IN_PLACE else None,
                source_sha256=sha256(desktop.read_text(loc.system_file)) if loc.system_file else None,
            )
            log.append(f"{desktop_id}: recovered custom icon")

    # ----------------------------------------------------------- side effects

    def refresh_caches(self) -> None:
        """Ask the desktop's menu caches to pick up the change (best effort)."""
        if not self._refresh_caches:
            return
        commands = []
        if shutil.which("update-desktop-database"):
            commands.append(["update-desktop-database", "-q", str(desktop.user_apps_dir())])
        sycoca = shutil.which("kbuildsycoca6") or shutil.which("kbuildsycoca5")
        if sycoca:  # KDE's service cache, also used by KDE apps outside Plasma
            commands.append([sycoca])

        def run() -> None:
            for cmd in commands:
                try:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
                except (OSError, subprocess.SubprocessError):
                    pass

        if self._background:
            threading.Thread(target=run, daemon=True).start()
        else:
            run()


def _file_mode(path: Path) -> int:
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return 0o644


# --------------------------------------------------------------------------
# Background repair (systemd user units)
# --------------------------------------------------------------------------

def _units_dir() -> Path:
    return desktop.config_home() / "systemd" / "user"


def autosync_installed() -> bool:
    return (_units_dir() / f"{SYNC_UNIT}.path").exists()


def _quote(arg: str) -> str:
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def install_autosync() -> None:
    """Re-apply custom icons whenever the system's application entries change
    (package updates, installs and removals)."""
    if os.environ.get("ICON_CHANGER_TESTING"):
        return
    package_parent = str(Path(__file__).resolve().parent.parent)
    watched = "\n".join(f"PathChanged={d}" for d in desktop.system_apps_dirs())
    units = {
        f"{SYNC_UNIT}.service": (
            "[Unit]\n"
            "Description=Icon Changer: keep custom app icons after updates\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            # Let a package transaction finish first; the repair rescans everything anyway.
            f"ExecStartPre={shutil.which('sleep') or '/usr/bin/sleep'} 3\n"
            f"Environment={_quote('PYTHONPATH=' + package_parent)}\n"
            f"ExecStart={_quote(sys.executable)} -m iconchanger --sync\n"
        ),
        f"{SYNC_UNIT}.path": (
            "[Unit]\n"
            "Description=Icon Changer: watch for application updates\n\n"
            "[Path]\n"
            f"{watched}\n"
            f"Unit={SYNC_UNIT}.service\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        ),
    }
    enabled_link = _units_dir() / "default.target.wants" / f"{SYNC_UNIT}.path"
    unchanged = all(_read_or_none(_units_dir() / name) == text for name, text in units.items())
    if unchanged and enabled_link.exists():
        return
    for name, text in units.items():
        write_atomic(_units_dir() / name, text)
    _systemctl("daemon-reload")
    _systemctl("enable", f"{SYNC_UNIT}.path")
    _systemctl("restart", f"{SYNC_UNIT}.path")


def _read_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def uninstall_autosync() -> None:
    _systemctl("disable", "--now", f"{SYNC_UNIT}.path")
    for name in (f"{SYNC_UNIT}.path", f"{SYNC_UNIT}.service"):
        (_units_dir() / name).unlink(missing_ok=True)
    _systemctl("daemon-reload")


def _systemctl(*args: str) -> None:
    if not shutil.which("systemctl"):
        raise RuntimeError("systemctl is not available")
    result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)
    if result.returncode != 0 and args[0] != "disable":
        raise RuntimeError(result.stderr.strip() or f"systemctl {' '.join(args)} failed")
