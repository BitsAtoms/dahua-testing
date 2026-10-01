"""The nine screens: design canvas, title and place in the physical wall.

Canvases are CSS pixels at the fixed device scale factor 2 (see README). The
wall positions are centimetres from the owner's photo, used by the preview.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Screen:
    position_id: str
    title: str
    width: int
    height: int
    wall_cm: tuple[float, float, float, float]
    icon: str = ""
    work: bool = False


SCREENS: dict[str, Screen] = {
    screen.position_id: screen
    for screen in (
        Screen("side_left", "Eventos", 1080, 1920, (0, 18, 39.2, 69.8), icon="photo"),
        Screen("top_left", "Videovigilancia", 1920, 1080, (45, 0, 69.8, 39.2), icon="videowall"),
        Screen("top_right", "Mapa en vivo", 1920, 1080, (115.8, 0, 69.8, 39.2), icon="floorplan"),
        Screen("side_right", "Recorridos · resumen", 1080, 1920, (191.4, 18, 39.2, 69.8), icon="route"),
        Screen("bottom_left", "Trabajo", 1920, 1080, (45, 40.2, 69.8, 39.2), work=True),
        Screen("bottom_right", "Trabajo", 1920, 1080, (115.8, 40.2, 69.8, 39.2), work=True),
        Screen("mini_left", "Salud · partes", 960, 540, (2.35, 93, 34.5, 19.4), icon="pulse"),
        Screen("mini_center", "Consola · supervisor", 960, 540, (98.05, 84, 34.5, 19.4), icon="terminal"),
        Screen("mini_right", "Salud · cámaras", 960, 540, (193.75, 93, 34.5, 19.4), icon="camera"),
    )
}


def screens_document(built: set[str]) -> dict:
    return {
        "screens": [
            {**asdict(screen), "built": screen.position_id in built}
            for screen in SCREENS.values()
        ]
    }
