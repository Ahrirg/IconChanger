"""Copying user-chosen pictures into the icon store."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QImage, QImageReader, QPainter

from . import manager

MAX_SIZE = 512
VECTOR_SUFFIXES = (".svg", ".svgz")
SUFFIXES = (".png", ".svg", ".svgz", ".jpg", ".jpeg", ".webp", ".bmp", ".gif",
            ".ico", ".xpm", ".tif", ".tiff", ".tga", ".jp2", ".icns")
FILE_FILTER = "Images (" + " ".join(f"*{s}" for s in SUFFIXES) + ");;All files (*)"


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUFFIXES


def import_image(source: str | Path) -> Path:
    """Copy ``source`` into the icon store and return the stored file.

    SVGs are kept as they are. Everything else is converted to a PNG (the
    format every launcher understands), shrunk to at most 512 px and centred
    on a transparent square so it isn't stretched. Files are named after their
    content, so importing the same picture twice reuses the stored copy, and
    a changed picture gets a new name (which defeats stale icon caches).
    """
    source = Path(source)
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()[:20]
    store = manager.icons_dir()
    store.mkdir(parents=True, exist_ok=True)

    suffix = source.suffix.lower()
    if suffix in VECTOR_SUFFIXES:
        if QImageReader(str(source)).read().isNull():
            raise ValueError(f"{source.name} is not a valid SVG image")
        dest = store / f"{digest}{suffix}"
        if not dest.exists():
            _copy_atomic(source, dest)
        return dest

    dest = store / f"{digest}.png"
    if dest.exists():
        dest.touch()  # move it to the front of the library
        return dest
    image = _read_largest(source)
    image = _fit_square(image)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    if not image.save(str(tmp), "PNG"):
        tmp.unlink(missing_ok=True)
        raise OSError(f"Could not save {dest}")
    os.replace(tmp, dest)
    return dest


def _read_largest(path: Path) -> QImage:
    """Read an image; for multi-size formats (.ico/.icns) pick the biggest one."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    multi_size = path.suffix.lower() in (".ico", ".icns")
    best = QImage()
    for i in range(max(1, reader.imageCount()) if multi_size else 1):
        if i and not reader.jumpToImage(i):
            break
        image = reader.read()
        if not image.isNull() and image.width() * image.height() > best.width() * best.height():
            best = image
    if best.isNull():
        raise ValueError(f"{path.name} could not be opened as an image ({reader.errorString()})")
    return best


def _fit_square(image: QImage) -> QImage:
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if max(image.width(), image.height()) > MAX_SIZE:
        image = image.scaled(QSize(MAX_SIZE, MAX_SIZE), Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    side = max(image.width(), image.height())
    if image.width() == image.height():
        return image
    canvas = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawImage((side - image.width()) // 2, (side - image.height()) // 2, image)
    painter.end()
    return canvas


def _copy_atomic(source: Path, dest: Path) -> None:
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    shutil.copyfile(source, tmp)
    os.replace(tmp, dest)


def library() -> list[Path]:
    """Stored icons, newest first."""
    store = manager.icons_dir()
    if not store.is_dir():
        return []
    files = [p for p in store.iterdir() if p.is_file() and not p.name.startswith(".")]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
