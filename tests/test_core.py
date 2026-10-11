"""Tests for the Qt-free core, run against throwaway XDG directories."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from iconchanger import desktop, manager
from iconchanger.manager import IconManager

FIREFOX = """\
[Desktop Entry]
Version=1.0
Name=Firefox
Name[de]=Firefox Browser
Comment=Browse the web
Exec=/usr/lib/firefox/firefox %u
Icon=firefox
Icon[de]=firefox-de
Type=Application
Categories=Network;WebBrowser;

[Desktop Action new-window]
Name=New Window
Exec=/usr/lib/firefox/firefox --new-window %u
Icon=window-new
"""

STEAM_GAME = """\
[Desktop Entry]
Name=Barotrauma
Comment=Play this game on Steam
Exec=steam steam://rungameid/602960
Icon=steam_icon_602960
Terminal=false
Type=Application
Categories=Game;
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    home_share = tmp_path / "home" / "share"
    system = tmp_path / "usr" / "share"
    (home_share / "applications").mkdir(parents=True)
    (system / "applications").mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(home_share))
    monkeypatch.setenv("XDG_DATA_DIRS", str(system))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("ICON_CHANGER_TESTING", "1")
    monkeypatch.setattr(desktop, "extra_data_dirs", lambda: [])

    image = tmp_path / "pictures" / "cool.png"
    image.parent.mkdir()
    image.write_bytes(b"\x89PNG fake")
    stored = manager.icons_dir() / "abc.png"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"\x89PNG fake")

    class Env:
        user = home_share / "applications"
        sys_apps = system / "applications"
        icon = stored
    return Env


def entry(desktop_id: str) -> desktop.DesktopEntry:
    return {e.id: e for e in desktop.scan()}[desktop_id]


# ------------------------------------------------------------------ editing

def test_set_keys_replaces_main_group_only():
    out = desktop.set_keys(FIREFOX, {"Icon": "/x.png", "X-New": "1"})
    main, action = out.split("[Desktop Action new-window]")
    assert "Icon=/x.png" in main and "Icon=firefox\n" not in main
    assert "Icon[de]" not in main  # localized icon would override ours
    assert "Name[de]=Firefox Browser" in main
    assert main.index("Icon=/x.png") < main.index("Type=Application")  # kept its position
    assert "X-New=1" in main
    assert "Icon=window-new" in action  # actions untouched


def test_set_keys_adds_missing_and_removes():
    text = "[Desktop Entry]\nName=A\nType=Application\n"
    assert "Icon=/a.png" in desktop.set_keys(text, {"Icon": "/a.png"})
    assert "Icon" not in desktop.set_keys(FIREFOX.split("\n\n")[0], {"Icon": None})
    with pytest.raises(ValueError):
        desktop.set_keys("Name=x\n", {"Icon": "y"})


# ----------------------------------------------------------- system entries

def test_apply_and_reset_system_app(env):
    system_file = env.sys_apps / "firefox.desktop"
    system_file.write_text(FIREFOX)
    mgr = IconManager()

    mgr.apply("firefox.desktop", env.icon)

    override = env.user / "firefox.desktop"
    text = override.read_text()
    assert f"Icon={env.icon}" in text
    assert "X-IconChanger-OriginalIcon=firefox" in text
    assert "Exec=/usr/lib/firefox/firefox %u" in text
    assert system_file.read_text() == FIREFOX  # system file never touched
    e = entry("firefox.desktop")
    assert e.customized and e.icon == str(env.icon) and e.original_icon == "firefox"
    assert json.loads(manager.state_file().read_text())["customizations"]["firefox.desktop"]["mode"] == "override"

    # Changing the icon again keeps the original icon name.
    other = env.icon.with_name("other.png")
    other.write_bytes(b"x")
    mgr.apply("firefox.desktop", other)
    assert "X-IconChanger-OriginalIcon=firefox" in override.read_text()

    mgr.reset("firefox.desktop")
    assert not override.exists()
    assert not entry("firefox.desktop").customized
    assert entry("firefox.desktop").icon == "firefox"
    assert mgr.customizations == {}


def test_subdirectory_ids(env):
    (env.sys_apps / "kde4").mkdir()
    (env.sys_apps / "kde4" / "foo.desktop").write_text(FIREFOX)
    IconManager().apply("kde4-foo.desktop", env.icon)
    assert entry("kde4-foo.desktop").customized


# ------------------------------------------------------------- user entries

def test_in_place_reset_restores_exactly(env):
    game = env.user / "Barotrauma.desktop"
    game.write_text(STEAM_GAME)
    game.chmod(0o755)
    mgr = IconManager()

    mgr.apply("Barotrauma.desktop", env.icon)
    assert f"Icon={env.icon}" in game.read_text()
    assert (manager.backups_dir() / "Barotrauma.desktop").read_text() == STEAM_GAME
    assert game.stat().st_mode & 0o777 == 0o755

    mgr.apply("Barotrauma.desktop", env.icon)  # re-applying must not overwrite the backup
    assert (manager.backups_dir() / "Barotrauma.desktop").read_text() == STEAM_GAME

    mgr.reset("Barotrauma.desktop")
    assert game.read_text() == STEAM_GAME
    assert not (manager.backups_dir() / "Barotrauma.desktop").exists()


def test_in_place_reset_keeps_later_edits(env):
    game = env.user / "Barotrauma.desktop"
    game.write_text(STEAM_GAME)
    mgr = IconManager()
    mgr.apply("Barotrauma.desktop", env.icon)
    game.write_text(game.read_text().replace("Terminal=false", "Terminal=true"))

    mgr.reset("Barotrauma.desktop")
    text = game.read_text()
    assert "Icon=steam_icon_602960" in text and "Terminal=true" in text
    assert "X-IconChanger" not in text


def test_in_place_symlink_is_restored(env, tmp_path):
    target = tmp_path / "opt" / "app.desktop"
    target.parent.mkdir()
    target.write_text(STEAM_GAME)
    link = env.user / "app.desktop"
    link.symlink_to(target)
    mgr = IconManager()

    mgr.apply("app.desktop", env.icon)
    assert not link.is_symlink() and target.read_text() == STEAM_GAME  # target left alone

    mgr.reset("app.desktop")
    assert link.is_symlink() and link.resolve() == target


# ------------------------------------------------------------------ repair

def test_sync_follows_updates_uninstall_and_reinstall(env):
    system_file = env.sys_apps / "firefox.desktop"
    system_file.write_text(FIREFOX)
    mgr = IconManager()
    mgr.apply("firefox.desktop", env.icon)
    override = env.user / "firefox.desktop"
    assert mgr.sync() == []

    # package update changes Exec -> copy rebuilt with our icon
    system_file.write_text(FIREFOX.replace("/usr/lib/firefox/firefox", "/usr/bin/firefox"))
    assert "updated" in mgr.sync()[0]
    assert "Exec=/usr/bin/firefox %u" in override.read_text()
    assert f"Icon={env.icon}" in override.read_text()

    # uninstall -> no ghost launcher left behind
    system_file.unlink()
    assert "uninstalled" in mgr.sync()[0]
    assert not override.exists()
    assert "firefox.desktop" not in {e.id for e in desktop.scan()}

    # reinstall -> custom icon comes back
    system_file.write_text(FIREFOX)
    assert "reinstalled" in mgr.sync()[0]
    assert f"Icon={env.icon}" in override.read_text()

    # state survives a restart
    assert IconManager().customizations["firefox.desktop"].suspended is False


def test_sync_forgets_externally_removed_entries(env):
    (env.sys_apps / "firefox.desktop").write_text(FIREFOX)
    mgr = IconManager()
    mgr.apply("firefox.desktop", env.icon)
    (env.user / "firefox.desktop").unlink()
    mgr.sync()
    assert mgr.customizations == {}


def test_lost_state_is_recovered(env):
    (env.sys_apps / "firefox.desktop").write_text(FIREFOX)
    IconManager().apply("firefox.desktop", env.icon)
    manager.state_file().unlink()

    mgr = IconManager()
    assert "recovered" in mgr.sync()[0]
    assert mgr.customizations["firefox.desktop"].icon == str(env.icon)
    mgr.reset("firefox.desktop")
    assert not (env.user / "firefox.desktop").exists()


def test_reset_all(env):
    (env.sys_apps / "firefox.desktop").write_text(FIREFOX)
    (env.user / "Barotrauma.desktop").write_text(STEAM_GAME)
    mgr = IconManager()
    mgr.apply("firefox.desktop", env.icon)
    mgr.apply("Barotrauma.desktop", env.icon)
    assert mgr.reset_all() == 2
    assert not any(e.customized for e in desktop.scan())
    assert (env.user / "Barotrauma.desktop").read_text() == STEAM_GAME


def test_hidden_entries(env, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "Hyprland")
    (env.sys_apps / "kde-only.desktop").write_text(FIREFOX.replace("Type=Application", "Type=Application\nOnlyShowIn=KDE;"))
    (env.sys_apps / "gone.desktop").write_text(FIREFOX)
    (env.user / "gone.desktop").write_text("[Desktop Entry]\nType=Application\nName=x\nHidden=true\n")
    entries = {e.id: e for e in desktop.scan()}
    assert entries["kde-only.desktop"].no_display
    assert "gone.desktop" not in entries  # user deleted it via Hidden=true


# ------------------------------------------------------------------ images

@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication([])


def test_import_image_makes_square_png(env, qt_app, tmp_path):
    from PyQt6.QtGui import QColor, QImage

    from iconchanger import images
    src = tmp_path / "photo.jpg"
    img = QImage(1200, 600, QImage.Format.Format_RGB32)
    img.fill(QColor("red"))
    assert img.save(str(src), "JPG")

    stored = images.import_image(src)
    assert stored.parent == manager.icons_dir() and stored.suffix == ".png"
    result = QImage(str(stored))
    assert result.width() == result.height() == images.MAX_SIZE
    assert result.pixelColor(256, 5).alpha() == 0       # transparent padding
    assert result.pixelColor(256, 256).red() > 200      # picture centred

    assert images.import_image(src) == stored  # same picture -> no duplicate copy
    src.unlink()  # the original may disappear - the stored copy stays
    assert stored.exists()


def test_import_rejects_non_images(env, qt_app, tmp_path):
    from iconchanger import images
    bad = tmp_path / "notes.png"
    bad.write_text("hello")
    with pytest.raises(ValueError):
        images.import_image(bad)
