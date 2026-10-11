# FULLY VIBECODED I AM NOT RESPONSIBLE FOR ANYTHING THAT HAPPENS WHILE USING APP

# Icon Changer

A small PyQt6 app for CachyOS / Arch (and any other Linux desktop) that lets you
give **any installed application a custom icon**, and put the original back with one click.

![workflow](iconchanger/resources/icon-changer.svg)

## Install

```sh
sudo pacman -S --needed python-pyqt6 qt6-svg   # once
./install.sh
```

Then open **Icon Changer** from your launcher (rofi, wofi, KDE menu, …) or run `icon-changer`.
Everything is installed for your user only. Nothing needs root.

To remove it: `./install.sh --uninstall` (it offers to restore all original icons first).

You can also run it straight from this folder without installing: `python3 -m iconchanger`
(this needs the system Python, because the project's `.venv` doesn't have PyQt6).

## Using it

1. Pick an app on the left. Use the search box to find it quickly.
2. Click **Choose Image…**, or drop an image onto the *New icon* box.
   PNG, SVG, JPG, WebP, ICO, BMP, GIF and more are accepted.
3. Press **Apply Icon**.

**Reset to Original** gives the app its own icon back. Apps with a custom icon are shown
in bold with a ✔ badge, and *Changed icons only* lists just those.

Every image you use is copied into the **Saved icons** library
(`~/.local/share/icon-changer/icons`). The original picture can be moved or deleted
without breaking anything, and you can re-use an image with one click.

## How it works (and why it's safe)

Launchers, docks, taskbars and file managers all look for an app's `.desktop` entry
in `~/.local/share/applications` before the system folders. Icon Changer writes the
modified copy there:

| App type | What happens | Reset |
|---|---|---|
| System apps (`/usr/share/applications`, Flatpak, Snap) | A copy with a new `Icon=` line is written to `~/.local/share/applications`. **System files are never touched**, so pacman updates can't undo the change. | The copy is deleted. |
| Entries already in your home folder (Steam games, web apps, …) | A backup is saved, then only the `Icon=` line is changed. | The backup is restored byte-for-byte. If you edited the entry since, only the icon line is reverted. |

Changed entries are tagged with `X-IconChanger-*` keys, so they can always be
recognised and reset, even if the app's own data is lost.

**Keeping icons after updates.** When an app is updated, its system entry may change.
Icon Changer rebuilds your copy so it stays current. When an app is uninstalled, the
copy is removed so no dead launcher is left behind, and the custom icon comes back if
you reinstall the app. This runs every time Icon Changer starts. With
*Settings → Keep Custom Icons After App Updates* (on by default) it also runs in the
background through a small systemd user unit (`icon-changer-sync.path`) whenever packages
change application entries.

## Good to know

- Windows that are already open keep their old icon until the app is restarted.
  Some panels cache icons too. Restart Waybar with `killall -SIGUSR2 waybar` if needed.
- A few apps draw their own window icon and ignore the launcher icon in the taskbar.
  Menus and launchers will still show the new icon.
- Changes are per user. On a multi-user machine, each user picks their own icons.

## Command line

```
icon-changer              open the app
icon-changer --list       show apps with a custom icon
icon-changer --sync       repair icons after app updates/removals
icon-changer --reset-all  give every app its original icon back
```

## Development

```sh
python3 -m pytest tests      # runs against throwaway XDG folders, never your real menus
```

Code layout: `desktop.py` (finding and editing `.desktop` files), `manager.py`
(apply, reset, repair, state; no Qt), `images.py` (importing pictures), `gui.py` (the window).
