"""Render the existing brand SVG as a Windows icon without extra dependencies."""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_icon(source: Path, destination: Path) -> None:
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError("Invalid brand SVG: " + str(source))
    images = []
    for size in SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("Could not render brand icon")
        images.append(bytes(data))
    offset = 6 + 16 * len(images)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for size, data in zip(SIZES, images):
        directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    destination.write_bytes(bytes(directory) + b"".join(images))


if __name__ == "__main__":
    build_icon(ROOT / "src/selfManger/assets/ui/selfManger.svg",
               ROOT / "src/selfManger/assets/ui/selfManger.ico")
