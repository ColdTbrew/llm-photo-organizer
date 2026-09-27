from __future__ import annotations

import csv
import math
import re
from functools import lru_cache
from pathlib import Path

PLACE_FILE = Path(__file__).with_name("places.tsv")


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    latitude = math.radians(b[0] - a[0])
    longitude = math.radians(b[1] - a[1])
    first = math.radians(a[0])
    second = math.radians(b[0])
    arc = (math.sin(latitude / 2) ** 2 + math.cos(first) * math.cos(second) *
           math.sin(longitude / 2) ** 2)
    return 6371 * 2 * math.asin(min(1, math.sqrt(arc)))


@lru_cache(maxsize=1)
def place_index() -> tuple[dict[tuple[int, int], list[tuple[str, str, float, float]]],
                           dict[str, tuple[float, float]]]:
    grid: dict[tuple[int, int], list[tuple[str, str, float, float]]] = {}
    korean: dict[str, tuple[float, float]] = {}
    with PLACE_FILE.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            name = row["name"]
            country = row["country"]
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])
            grid.setdefault((math.floor(latitude), math.floor(longitude)), []).append(
                (name, country, latitude, longitude))
            if country == "KR":
                korean.setdefault(name, (latitude, longitude))
    return grid, korean


def nearby_place(latitude: float, longitude: float) -> str | None:
    grid, _ = place_index()
    cell = math.floor(latitude), math.floor(longitude)
    candidates = (place for lat in range(cell[0] - 1, cell[0] + 2)
                  for lon in range(cell[1] - 1, cell[1] + 2)
                  for place in grid.get((lat, lon), []))
    nearest = min(candidates, key=lambda place: distance_km((latitude, longitude), (place[2], place[3])),
                  default=None)
    if nearest and distance_km((latitude, longitude), (nearest[2], nearest[3])) <= 25:
        return nearest[0]
    return None


def home_distance_km(latitude: float, longitude: float, home: str) -> float | None:
    _, korean = place_index()
    city = re.sub(r"(특별시|광역시|시|군|구)$", "", home.split()[-1]) if home.strip() else ""
    center = korean.get(city)
    return distance_km((latitude, longitude), center) if center else None
