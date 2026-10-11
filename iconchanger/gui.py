"""The Icon Changer window."""
from __future__ import annotations

import configparser
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from PyQt6.QtCore import QRect, QRectF, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPainter,
                         QPainterPath, QPalette, QPen, QPixmap)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                             QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
                             QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSplitter,
                             QStackedWidget, QStyle, QVBoxLayout, QWidget)

from . import APP_ID, APP_NAME, __version__, desktop, images, manager
from .desktop import DesktopEntry
from .manager import IconManager

RESOURCES = Path(__file__).parent / "resources"
ID_ROLE = Qt.ItemDataRole.UserRole


# --------------------------------------------------------------------------
# Icons
# --------------------------------------------------------------------------

def setup_icon_theme() -> None:
    """Use the same icon theme as the rest of the desktop.

    Outside KDE, Qt often falls back to the bare "hicolor" theme, which would
    show most apps without an icon, so borrow the GTK/KDE setting instead.
    """
    QIcon.setFallbackThemeName("hicolor")
    QIcon.setFallbackSearchPaths(QIcon.fallbackSearchPaths() + [str(desktop.data_home() / "pixmaps")])
    if QIcon.themeName() not in ("", "hicolor"):
        return
    for name in _desktop_icon_themes():
        if any((Path(base) / name / "index.theme").exists() for base in QIcon.themeSearchPaths()):
            QIcon.setThemeName(name)
            return


def _desktop_icon_themes() -> list[str]:
    names = []
    cfg = desktop.config_home()
    for path, section, key in ((cfg / "gtk-4.0" / "settings.ini", "Settings", "gtk-icon-theme-name"),
                               (cfg / "gtk-3.0" / "settings.ini", "Settings", "gtk-icon-theme-name"),
                               (cfg / "kdeglobals", "Icons", "Theme")):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(path, encoding="utf-8")
            names.append(parser.get(section, key).strip().strip("'\""))
        except (configparser.Error, OSError, UnicodeDecodeError):
            pass
    if shutil.which("gsettings"):
        try:
            out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "icon-theme"],
                                 capture_output=True, text=True, timeout=2).stdout
            names.append(out.strip().strip("'\""))
        except (OSError, subprocess.SubprocessError):
            pass
    return [n for n in names if n] + ["breeze", "Papirus", "Adwaita"]


_icon_cache: dict[str, QIcon] = {}


def app_icon(value: str) -> QIcon:
    """Resolve an ``Icon=`` value (theme name or file path) to a QIcon."""
    if value in _icon_cache:
        return _icon_cache[value]
    icon = QIcon()
    if value.startswith("/"):
        if Path(value).is_file():
            icon = QIcon(value)
    elif value:
        # Some entries wrongly use "name.png" instead of "name".
        stem = value.rsplit(".", 1)[0] if value.lower().endswith((".png", ".svg", ".xpm")) else value
        for name in dict.fromkeys((value, stem)):
            if QIcon.hasThemeIcon(name):
                icon = QIcon.fromTheme(name)
                break
        else:
            for folder in QIcon.fallbackSearchPaths():
                for candidate in (Path(folder) / value, *(Path(folder) / f"{stem}{ext}" for ext in (".png", ".svg", ".xpm"))):
                    if candidate.is_file():
                        icon = QIcon(str(candidate))
                        break
                if not icon.isNull():
                    break
    if icon.isNull():
        icon = QIcon.fromTheme("application-x-executable",
                               QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon))
    _icon_cache[value] = icon
    return icon


def badged(icon: QIcon, accent: QColor) -> QIcon:
    """The icon with a small check-mark badge, used for apps we changed."""
    size, r = 64, 26
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    icon.paint(p, QRect(0, 0, size, size))
    badge = QRectF(size - r - 1, size - r - 1, r, r)
    p.setPen(QPen(QColor("white"), 3))
    p.setBrush(accent)
    p.drawEllipse(badge)
    tick = QPainterPath()
    tick.moveTo(badge.left() + r * 0.28, badge.top() + r * 0.53)
    tick.lineTo(badge.left() + r * 0.44, badge.top() + r * 0.69)
    tick.lineTo(badge.left() + r * 0.73, badge.top() + r * 0.35)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("white"), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(tick)
    p.end()
    return QIcon(pixmap)


def themed(name: str, fallback: QStyle.StandardPixmap) -> QIcon:
    return QIcon.fromTheme(name, QApplication.style().standardIcon(fallback))


# --------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------

def tint(widget: QWidget, role: QPalette.ColorRole, alpha: int) -> str:
    """A palette colour as a translucent CSS colour (works on light and dark themes alike)."""
    c = widget.palette().color(role)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


class IconBox(QFrame):
    """A large icon preview with a caption underneath."""

    def __init__(self, caption: str, size: int = 112, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("iconBox")
        self._size = size
        self.setMinimumWidth(size + 100)
        self.picture = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.picture.setFixedSize(size + 60, size + 24)
        self.picture.setWordWrap(True)
        self.caption = QLabel(caption, alignment=Qt.AlignmentFlag.AlignCenter)
        bold = self.caption.font()
        bold.setBold(True)
        self.caption.setFont(bold)
        self.note = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.note.setWordWrap(True)
        self.note.setForegroundRole(QPalette.ColorRole.PlaceholderText)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.addWidget(self.picture, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.caption)
        layout.addWidget(self.note)
        self.setStyleSheet(f"#iconBox {{ border: 1px solid {tint(self, QPalette.ColorRole.WindowText, 45)};"
                           f" border-radius: 14px; background: {tint(self, QPalette.ColorRole.WindowText, 8)}; }}")

    def set_icon(self, icon: QIcon | None, placeholder: str = "") -> None:
        if icon is None:
            self.picture.setPixmap(QPixmap())
            self.picture.setText(placeholder)
        else:
            self.picture.setPixmap(icon.pixmap(QSize(self._size, self._size), self.devicePixelRatioF()))


class DropZone(IconBox):
    """Preview of the new icon. Click it to browse or drop an image on it."""

    clicked = pyqtSignal()

    def __init__(self, caption: str, size: int = 112, parent: QWidget | None = None):
        super().__init__(caption, size, parent)
        self.setObjectName("dropZone")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to choose an image, or drag one here")
        self.set_highlight(False)

    def set_highlight(self, on: bool) -> None:
        if on:
            border, fill = tint(self, QPalette.ColorRole.Highlight, 255), tint(self, QPalette.ColorRole.Highlight, 30)
        else:
            border, fill = tint(self, QPalette.ColorRole.WindowText, 90), tint(self, QPalette.ColorRole.WindowText, 8)
        self.setStyleSheet(f"#dropZone {{ border: 2px dashed {border}; border-radius: 14px; background: {fill}; }}")

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:
        self.set_highlight(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.set_highlight(False)
        super().leaveEvent(event)


def dropped_image(event) -> str | None:
    """The first local image file in a drag-and-drop event, if any."""
    for url in event.mimeData().urls():
        if url.isLocalFile() and images.is_supported(url.toLocalFile()) and Path(url.toLocalFile()).is_file():
            return url.toLocalFile()
    return None


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, mgr: IconManager):
        super().__init__()
        self.mgr = mgr
        self.entries: dict[str, DesktopEntry] = {}
        self.pending: Path | None = None  # image chosen but not applied yet

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(RESOURCES / f"{APP_ID}.svg")))
        self.resize(1060, 740)
        self.setAcceptDrops(True)

        splitter = QSplitter()
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_details())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 700])
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)
        self._build_menus()

        self.reload()
        self.refresh_library()

    # ---------------------------------------------------------------- layout

    def _build_sidebar(self) -> QWidget:
        self.search = QLineEdit(placeholderText="Search apps…", clearButtonEnabled=True)
        self.search.textChanged.connect(self.apply_filter)

        self.filter_box = QComboBox()
        self.filter_box.addItems(["All apps", "Changed icons only"])
        self.filter_box.currentIndexChanged.connect(self.apply_filter)
        self.hidden_check = QCheckBox("Show hidden")
        self.hidden_check.setToolTip("Also list entries that don't appear in the app menu\n"
                                     "(helpers, settings panels, apps meant for other desktops)")
        self.hidden_check.toggled.connect(self.apply_filter)

        self.app_list = QListWidget()
        self.app_list.setIconSize(QSize(32, 32))
        self.app_list.setUniformItemSizes(True)
        self.app_list.setSpacing(1)
        self.app_list.currentItemChanged.connect(self.on_select)

        self.count_label = QLabel()
        self.count_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)

        filters = QHBoxLayout()
        filters.addWidget(self.filter_box, 1)
        filters.addWidget(self.hidden_check)

        sidebar = QWidget()
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 10, 4, 10)
        layout.addWidget(self.search)
        layout.addLayout(filters)
        layout.addWidget(self.app_list, 1)
        layout.addWidget(self.count_label)
        return sidebar

    def _build_details(self) -> QWidget:
        self.stack = QStackedWidget()

        empty = QLabel("👈  Pick an app on the left to change its icon", alignment=Qt.AlignmentFlag.AlignCenter)
        font = empty.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        empty.setFont(font)
        empty.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.stack.addWidget(empty)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        # Header: name, id, description
        self.header_icon = QLabel()
        self.title = QLabel()
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setBold(True)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        self.subtitle = QLabel()
        self.subtitle.setWordWrap(True)
        self.subtitle.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        header = QHBoxLayout()
        header.addWidget(self.header_icon, 0, Qt.AlignmentFlag.AlignTop)
        header.addLayout(titles, 1)
        layout.addLayout(header)

        # Current -> new previews
        self.current_box = IconBox("Current icon")
        self.new_box = DropZone("New icon")
        self.new_box.clicked.connect(self.choose_image)
        arrow = QLabel("➜", alignment=Qt.AlignmentFlag.AlignCenter)
        font = arrow.font()
        font.setPointSizeF(font.pointSizeF() * 2.4)
        arrow.setFont(font)
        arrow.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        previews = QHBoxLayout()
        previews.addStretch()
        previews.addWidget(self.current_box)
        previews.addSpacing(10)
        previews.addWidget(arrow)
        previews.addSpacing(10)
        previews.addWidget(self.new_box)
        previews.addStretch()
        layout.addLayout(previews)

        # Buttons
        self.choose_btn = QPushButton(themed("document-open", QStyle.StandardPixmap.SP_DialogOpenButton), "Choose Image…")
        self.choose_btn.clicked.connect(self.choose_image)
        self.apply_btn = QPushButton(themed("dialog-ok-apply", QStyle.StandardPixmap.SP_DialogApplyButton), "Apply Icon")
        self.apply_btn.clicked.connect(self.apply_pending)
        self.apply_btn.setDefault(True)
        self.reset_btn = QPushButton(themed("edit-undo", QStyle.StandardPixmap.SP_DialogResetButton), "Reset to Original")
        self.reset_btn.clicked.connect(self.reset_current)
        for btn in (self.choose_btn, self.apply_btn, self.reset_btn):
            btn.setMinimumHeight(38)
            btn.setIconSize(QSize(20, 20))
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.choose_btn)
        buttons.addWidget(self.apply_btn)
        buttons.addWidget(self.reset_btn)
        buttons.addStretch()
        layout.addLayout(buttons)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # Saved icons library
        lib_title = QLabel("Saved icons")
        bold = lib_title.font()
        bold.setBold(True)
        lib_title.setFont(bold)
        lib_hint = QLabel("Every image you use is kept here safely, even if you delete the original. "
                          "Click one to use it again.")
        lib_hint.setWordWrap(True)
        lib_hint.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        open_folder = QPushButton(themed("folder-open", QStyle.StandardPixmap.SP_DirOpenIcon), "Open Folder")
        open_folder.setFlat(True)
        open_folder.clicked.connect(self.open_icon_folder)
        lib_header = QHBoxLayout()
        lib_header.addWidget(lib_title, 1)
        lib_header.addWidget(open_folder)
        layout.addLayout(lib_header)
        layout.addWidget(lib_hint)

        self.library = QListWidget()
        self.library.setViewMode(QListView.ViewMode.IconMode)
        self.library.setFlow(QListView.Flow.LeftToRight)
        self.library.setWrapping(False)
        self.library.setMovement(QListView.Movement.Static)
        self.library.setIconSize(QSize(48, 48))
        self.library.setGridSize(QSize(62, 62))
        self.library.setFixedHeight(80)
        self.library.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.library.itemClicked.connect(lambda item: self.set_pending(Path(item.data(ID_ROLE))))
        self.library.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library.customContextMenuRequested.connect(self.library_menu)
        self.library_empty = QLabel("No saved icons yet.")
        self.library_empty.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        layout.addWidget(self.library)
        layout.addWidget(self.library_empty)

        layout.addStretch()
        hint = QLabel("The new icon is used everywhere: app menus and launchers (rofi, wofi, …), docks, "
                      "taskbars and file managers. Apps that are already open, and some panels such as "
                      "Waybar, show it after they are restarted.")
        hint.setWordWrap(True)
        hint.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack.addWidget(scroll)
        return self.stack

    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self._action(file_menu, "Choose Image…", self.choose_image, QKeySequence.StandardKey.Open, "document-open")
        self._action(file_menu, "Apply Icon", self.apply_pending, "Ctrl+Return", "dialog-ok-apply")
        file_menu.addSeparator()
        self._action(file_menu, "Refresh App List", self.reload_all, QKeySequence.StandardKey.Refresh, "view-refresh")
        self._action(file_menu, "Open Saved Icons Folder", self.open_icon_folder, None, "folder-open")
        file_menu.addSeparator()
        self._action(file_menu, "Quit", self.close, QKeySequence.StandardKey.Quit, "application-exit")

        tools = bar.addMenu("&Tools")
        self._action(tools, "Find App", lambda: (self.search.setFocus(), self.search.selectAll()),
                     QKeySequence.StandardKey.Find, "edit-find")
        self._action(tools, "Repair Custom Icons Now", self.repair_now, None, "tools-wizard")
        tools.addSeparator()
        self._action(tools, "Reset All Icons…", self.reset_all, None, "edit-undo")

        settings = bar.addMenu("&Settings")
        self.autosync_action = QAction("Keep Custom Icons After App Updates", self, checkable=True)
        self.autosync_action.setToolTip("Runs a small background service that re-applies your icons when apps are\n"
                                        "updated, and hides them when an app is uninstalled.")
        self.autosync_action.setChecked(bool(self.mgr.settings.get("auto_sync", True)))
        self.autosync_action.toggled.connect(self.toggle_autosync)
        settings.addAction(self.autosync_action)
        settings.setToolTipsVisible(True)

        help_menu = bar.addMenu("&Help")
        self._action(help_menu, f"About {APP_NAME}", self.about, None, "help-about")

    def _action(self, menu: QMenu, text: str, slot, shortcut=None, icon: str | None = None) -> QAction:
        action = QAction(QIcon.fromTheme(icon) if icon else QIcon(), text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        self.addAction(action)
        return action

    # ------------------------------------------------------------- app list

    def reload(self) -> None:
        """Re-read all installed apps, keeping the selection and scroll position."""
        selected = self.current_entry()
        scroll = self.app_list.verticalScrollBar().value()
        entries = desktop.scan()
        self.entries = {e.id: e for e in entries}
        duplicates = Counter(e.name.casefold() for e in entries)
        accent = self.palette().color(QPalette.ColorRole.Highlight)

        self.app_list.blockSignals(True)
        self.app_list.clear()
        for entry in entries:
            label = entry.name
            if duplicates[entry.name.casefold()] > 1:
                label += f"  ({entry.id.removesuffix('.desktop')})"
            icon = app_icon(entry.icon)
            item = QListWidgetItem(badged(icon, accent) if entry.customized else icon, label)
            item.setData(ID_ROLE, entry.id)
            item.setToolTip(f"{entry.name}\n{entry.id}" + ("\n✔ Custom icon" if entry.customized else ""))
            if entry.customized:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.app_list.addItem(item)
            if selected and entry.id == selected.id:
                self.app_list.setCurrentItem(item)
        self.app_list.blockSignals(False)
        self.apply_filter()
        self.app_list.verticalScrollBar().setValue(scroll)
        self.show_entry(self.current_entry())

    def reload_all(self) -> None:
        _icon_cache.clear()
        self.reload()
        self.refresh_library()
        self.statusBar().showMessage(f"Found {len(self.entries)} apps", 4000)

    def apply_filter(self) -> None:
        query = self.search.text().strip()
        only_changed = self.filter_box.currentIndex() == 1
        show_hidden = self.hidden_check.isChecked()
        shown = 0
        for i in range(self.app_list.count()):
            item = self.app_list.item(i)
            entry = self.entries[item.data(ID_ROLE)]
            visible = ((show_hidden or not entry.no_display or entry.customized)
                       and (entry.customized or not only_changed)
                       and (not query or entry.matches(query)))
            item.setHidden(not visible)
            shown += visible
        changed = sum(e.customized for e in self.entries.values())
        self.count_label.setText(f"{shown} apps shown · {changed} with a custom icon")

    def current_entry(self) -> DesktopEntry | None:
        item = self.app_list.currentItem()
        return self.entries.get(item.data(ID_ROLE)) if item else None

    def on_select(self, *_args) -> None:
        self.pending = None
        self.show_entry(self.current_entry())

    # -------------------------------------------------------------- details

    def show_entry(self, entry: DesktopEntry | None) -> None:
        if entry is None:
            self.stack.setCurrentIndex(0)
            return
        self.stack.setCurrentIndex(1)
        icon = app_icon(entry.icon)
        self.header_icon.setPixmap(icon.pixmap(QSize(48, 48), self.devicePixelRatioF()))
        self.title.setText(entry.name)
        path = str(entry.path)
        home = str(Path.home())
        if path.startswith(home + "/"):
            path = "~" + path[len(home):]
        details = [entry.comment or entry.generic_name, path.replace("/", "/\u200b")]  # allow wrapping at "/"
        self.subtitle.setText("\n".join(d for d in details if d))
        self.subtitle.setToolTip(str(entry.path))

        self.current_box.set_icon(icon)
        if entry.customized:
            original = entry.original_icon or "none"
            self.current_box.caption.setText("Current icon (custom)")
            self.current_box.note.setText(f"Original: {Path(original).name if original.startswith('/') else original}")
        else:
            self.current_box.caption.setText("Current icon")
            self.current_box.note.setText("The app's own icon")

        if self.pending:
            self.new_box.set_icon(QIcon(str(self.pending)))
            self.new_box.note.setText(self.pending.name)
        else:
            self.new_box.set_icon(None, "Drop an image here\nor click to browse")
            self.new_box.note.setText("PNG, SVG, JPG, WebP…")

        self.apply_btn.setEnabled(self.pending is not None)
        self.reset_btn.setEnabled(entry.customized)
        self.choose_btn.setEnabled(True)

    def set_pending(self, path: Path) -> None:
        if not self.current_entry():
            self.statusBar().showMessage("First pick the app whose icon you want to change", 5000)
            return
        if QIcon(str(path)).isNull() or not path.is_file():
            QMessageBox.warning(self, APP_NAME, f"“{path.name}” can't be used as an icon - it isn't a readable image.")
            return
        self.pending = path
        self.show_entry(self.current_entry())
        self.statusBar().showMessage("Looks good? Press “Apply Icon” to use it.", 6000)

    # -------------------------------------------------------------- actions

    def choose_image(self) -> None:
        if not self.current_entry():
            self.statusBar().showMessage("First pick the app whose icon you want to change", 5000)
            return
        start = self.pending.parent if self.pending else Path.home() / "Pictures"
        if not start.is_dir():
            start = Path.home()
        path, _ = QFileDialog.getOpenFileName(self, "Choose a new icon", str(start), images.FILE_FILTER)
        if path:
            self.set_pending(Path(path))

    def apply_pending(self) -> None:
        entry, source = self.current_entry(), self.pending
        if not entry or not source:
            return
        try:
            stored = source if source.parent == manager.icons_dir() else images.import_image(source)
            self.mgr.apply(entry.id, stored)
        except Exception as exc:  # show any failure to the user instead of crashing
            QMessageBox.warning(self, APP_NAME, f"Couldn't change the icon of {entry.name}:\n\n{exc}")
            return
        self.pending = None
        self.reload()
        self.refresh_library()
        self.statusBar().showMessage(f"✔ {entry.name} now uses the new icon", 8000)
        self.ensure_autosync()

    def reset_current(self) -> None:
        entry = self.current_entry()
        if not entry:
            return
        try:
            self.mgr.reset(entry.id)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Couldn't reset the icon of {entry.name}:\n\n{exc}")
            return
        self.pending = None
        self.reload()
        self.refresh_library()
        self.statusBar().showMessage(f"↺ {entry.name} has its original icon again", 8000)

    def reset_all(self) -> None:
        count = sum(e.customized for e in self.entries.values())
        if not count:
            QMessageBox.information(self, APP_NAME, "No app has a custom icon right now.")
            return
        answer = QMessageBox.question(
            self, APP_NAME, f"Give all {count} app(s) their original icon back?\n\n"
                            "Your saved images stay in the library, so you can re-apply them later.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            restored = self.mgr.reset_all()
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Resetting failed:\n\n{exc}")
            restored = 0
        self.reload()
        self.refresh_library()
        self.statusBar().showMessage(f"Restored {restored} original icon(s)", 8000)

    def repair_now(self) -> None:
        log = self.mgr.sync()
        self.reload()
        if log:
            QMessageBox.information(self, APP_NAME, "Repaired:\n\n" + "\n".join(log))
        else:
            self.statusBar().showMessage("All custom icons are fine - nothing to repair", 6000)

    # --------------------------------------------------------------- library

    def refresh_library(self) -> None:
        used_by: dict[Path, list[str]] = defaultdict(list)
        for cust in self.mgr.customizations.values():
            entry = self.entries.get(cust.desktop_id)
            used_by[Path(cust.icon)].append(entry.name if entry else cust.desktop_id)
        self.library.clear()
        for path in images.library():
            item = QListWidgetItem(QIcon(str(path)), "")
            item.setData(ID_ROLE, str(path))
            users = used_by.get(path)
            item.setToolTip("Used by: " + ", ".join(sorted(users)) if users else "Not used right now")
            self.library.addItem(item)
        empty = self.library.count() == 0
        self.library.setVisible(not empty)
        self.library_empty.setVisible(empty)

    def library_menu(self, pos) -> None:
        item = self.library.itemAt(pos)
        if not item:
            return
        path = Path(item.data(ID_ROLE))
        in_use = path in self.mgr.icons_in_use()
        menu = QMenu(self)
        use = menu.addAction(QIcon.fromTheme("dialog-ok-apply"), "Use for Selected App")
        delete = menu.addAction(QIcon.fromTheme("edit-delete"), "Delete from Library")
        delete.setEnabled(not in_use)
        if in_use:
            delete.setText("Delete from Library (in use)")
        chosen = menu.exec(self.library.viewport().mapToGlobal(pos))
        if chosen is use:
            self.set_pending(path)
        elif chosen is delete:
            path.unlink(missing_ok=True)
            if self.pending == path:
                self.pending = None
                self.show_entry(self.current_entry())
            self.refresh_library()

    def open_icon_folder(self) -> None:
        manager.icons_dir().mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(manager.icons_dir())))

    # ------------------------------------------------------- background sync

    def ensure_autosync(self) -> None:
        if not self.autosync_action.isChecked() or not shutil.which("systemctl"):
            return
        try:
            manager.install_autosync()
        except Exception as exc:
            self.statusBar().showMessage(f"Background repair service could not be set up: {exc}", 10000)

    def toggle_autosync(self, enabled: bool) -> None:
        self.mgr.settings["auto_sync"] = enabled
        self.mgr.save()
        try:
            if enabled:
                manager.install_autosync()
                self.statusBar().showMessage("Custom icons will be kept after app updates", 6000)
            elif manager.autosync_installed():
                manager.uninstall_autosync()
                self.statusBar().showMessage("Background repair turned off", 6000)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Couldn't change the background service:\n\n{exc}")

    # ----------------------------------------------------------- drag & drop

    def dragEnterEvent(self, event) -> None:
        if self.current_entry() and dropped_image(event):
            event.acceptProposedAction()
            self.new_box.set_highlight(True)

    def dragLeaveEvent(self, event) -> None:
        self.new_box.set_highlight(False)

    def dropEvent(self, event) -> None:
        self.new_box.set_highlight(False)
        path = dropped_image(event)
        if path:
            event.acceptProposedAction()
            self.set_pending(Path(path))

    # ----------------------------------------------------------------- about

    def about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}", (
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>Give any installed app a custom icon.</p>"
            "<p>Icons are changed with a personal copy of the app's launcher in "
            f"<code>{desktop.user_apps_dir()}</code>, which every desktop, launcher and dock prefers over the "
            "system one. System files are never modified, so updates can't undo your icons and "
            "<b>Reset</b> always brings the original back.</p>"
            f"<p>Your images are stored safely in <code>{manager.icons_dir()}</code>, so deleting or moving "
            "the original picture doesn't matter.</p>"
        ))


def run(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(APP_ID)  # lets Wayland compositors and docks match our window to our icon
    setup_icon_theme()

    mgr = IconManager()
    repaired = mgr.sync()  # catch up on app updates/removals since the last run
    window = MainWindow(mgr)
    if repaired:
        print("\n".join(repaired))
        window.statusBar().showMessage(f"Repaired {len(repaired)} custom icon(s) after app changes", 10000)
    if mgr.settings.get("auto_sync", True) and mgr.customizations:
        window.ensure_autosync()  # also re-points the service if the app was moved
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run(sys.argv))
