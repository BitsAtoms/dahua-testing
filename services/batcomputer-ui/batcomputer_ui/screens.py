"""The nine screens: design canvas, title and place in the physical wall.

Canvases are CSS pixels at the fixed device scale factor 2 (see README). The
wall positions are centimetres from the owner's photo, used by the preview.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Screen:
    position_id: str
    code: str
    title: str
    width: int
    height: int
    wall_cm: tuple[float, float, float, float]
    work: bool = False


SCREENS: dict[str, Screen] = {
    screen.position_id: screen
    for screen in (
        Screen("side_left", "SL", "Eventos", 1080, 1920, (0, 18, 39.2, 69.8)),
        Screen("top_left", "TL", "Videovigilancia", 1920, 1080, (45, 0, 69.8, 39.2)),
        Screen("top_right", "TR", "Mapa en vivo", 1920, 1080, (115.8, 0, 69.8, 39.2)),
        Screen("side_right", "SR", "Recorridos · resumen", 1080, 1920, (191.4, 18, 39.2, 69.8)),
        Screen("bottom_left", "BL", "Trabajo", 1920, 1080, (45, 40.2, 69.8, 39.2), work=True),
        Screen("bottom_right", "BR", "Trabajo", 1920, 1080, (115.8, 40.2, 69.8, 39.2), work=True),
        Screen("mini_left", "ML", "Salud · partes", 960, 540, (2.35, 93, 34.5, 19.4)),
        Screen("mini_center", "MC", "Consola · supervisor", 960, 540, (98.05, 84, 34.5, 19.4)),
        Screen("mini_right", "MR", "Salud · cámaras", 960, 540, (193.75, 93, 34.5, 19.4)),
    )
}


def screens_document(built: set[str]) -> dict:
    return {
        "screens": [
            {**asdict(screen), "built": screen.position_id in built}
            for screen in SCREENS.values()
        ]
    }
