"""
Wind overlay for the circular radar.
Uses Open-Meteo (no key). Color field + directional arrows.
Arrow density adapts to radar range.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import pygame

logger = logging.getLogger(__name__)

# Cache
_cache: dict[str, Any] = {
    "fetched_at": 0.0,
    "data": None,          # list of dicts with lat, lon, speed, direction, gust
    "surface": None,
}
CACHE_TTL_S = 5 * 60       # 5 minutes is plenty for wind


def _speed_to_color(speed_kt: float) -> tuple[int, int, int, int]:
    """Simple blue → green → yellow → red scale."""
    s = max(0.0, min(speed_kt, 50.0))
    if s < 10:
        t = s / 10
        return (40, 100 + int(100 * t), 220, 140)
    elif s < 20:
        t = (s - 10) / 10
        return (40, 200, 220 - int(120 * t), 160)
    elif s < 35:
        t = (s - 20) / 15
        return (220, 220 - int(80 * t), 40, 180)
    else:
        return (220, 40, 40, 200)


def _fetch_grid(lat: float, lon: float, radius_km: float, step_km: float = 12.0) -> list[dict]:
    """Fetch a grid of wind points from Open-Meteo."""
    points = []
    n = max(3, int((radius_km * 2) / step_km))
    for i in range(n):
        for j in range(n):
            dlat = (i - n / 2) * (step_km / 111.0)
            dlon = (j - n / 2) * (step_km / (111.0 * math.cos(math.radians(lat))))
            points.append((lat + dlat, lon + dlon))

    if not points:
        return []

    lats = ",".join(f"{p[0]:.4f}" for p in points)
    lons = ",".join(f"{p[1]:.4f}" for p in points)

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        "&current=wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        "&wind_speed_unit=kn"
        "&models=best_match"
    )

    try:
        import urllib.request
        import json
        req = urllib.request.Request(url, headers={"User-Agent": "FlightScnrPi-wind/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("Open-Meteo wind fetch failed: %s", exc)
        return []

    results = []
    items = data if isinstance(data, list) else [data]
    for item, (plat, plon) in zip(items, points):
        cur = item.get("current") or {}
        results.append({
            "lat": plat,
            "lon": plon,
            "speed": cur.get("wind_speed_10m") or 0.0,
            "direction": cur.get("wind_direction_10m") or 0.0,
            "gust": cur.get("wind_gusts_10m") or 0.0,
        })
    return results


def _arrow_density(radius_km: float) -> float:
    """Return target spacing in km for arrows.
    Wider range → sparser arrows. Airport-level zoom → denser.
    """
    if radius_km <= 15:
        return 4.0
    if radius_km <= 40:
        return 8.0
    if radius_km <= 100:
        return 14.0
    return 22.0


def _build_surface(points: list[dict], radius_km: float, side: int) -> pygame.Surface | None:
    if not points:
        return None

    surf = pygame.Surface((side, side), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))

    scale = side / (radius_km * 2)
    for p in points:
        dx = (p["lon"] - points[0]["lon"]) * 111.0 * math.cos(math.radians(p["lat"]))
        dy = (points[0]["lat"] - p["lat"]) * 111.0
        px = side // 2 + int(dx * scale)
        py = side // 2 + int(dy * scale)
        color = _speed_to_color(p["speed"])
        r = max(8, int(6 * scale))
        pygame.draw.circle(surf, color, (px, py), r)

    spacing = _arrow_density(radius_km)
    arrow_points = [p for i, p in enumerate(points) if i % max(1, int(spacing / 12)) == 0]

    for p in arrow_points:
        dx = (p["lon"] - points[0]["lon"]) * 111.0 * math.cos(math.radians(p["lat"]))
        dy = (points[0]["lat"] - p["lat"]) * 111.0
        px = side // 2 + int(dx * scale)
        py = side // 2 + int(dy * scale)

        ang = math.radians((p["direction"] + 180) % 360)
        length = min(28, max(10, int(p["speed"] * 0.8)))
        ex = px + int(math.sin(ang) * length)
        ey = py - int(math.cos(ang) * length)

        pygame.draw.line(surf, (255, 255, 255, 200), (px, py), (ex, ey), 2)
        head = 6
        pygame.draw.line(surf, (255, 255, 255, 220),
                         (ex, ey),
                         (ex - int(math.sin(ang - 0.4) * head),
                          ey + int(math.cos(ang - 0.4) * head)), 2)
        pygame.draw.line(surf, (255, 255, 255, 220),
                         (ex, ey),
                         (ex - int(math.sin(ang + 0.4) * head),
                          ey + int(math.cos(ang + 0.4) * head)), 2)

    mask = pygame.Surface((side, side), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255, 255, 255, 255), (side // 2, side // 2), side // 2)
    surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return surf


def blit_wind_overlay(surface: pygame.Surface, lat: float, lon: float, radius_km: float) -> None:
    """Blit wind color field + arrows onto the radar surface."""
    now = time.time()
    side = min(surface.get_width(), surface.get_height())

    need_refresh = (
        _cache["data"] is None
        or now - _cache["fetched_at"] > CACHE_TTL_S
        or _cache.get("radius_km") != radius_km
    )

    if need_refresh:
        step = 10.0 if radius_km < 50 else 16.0
        points = _fetch_grid(lat, lon, radius_km, step_km=step)
        _cache["data"] = points
        _cache["fetched_at"] = now
        _cache["radius_km"] = radius_km
        _cache["surface"] = None

    if not _cache["data"]:
        return

    if _cache["surface"] is None or _cache["surface"].get_width() != side:
        _cache["surface"] = _build_surface(_cache["data"], radius_km, side)

    if _cache["surface"]:
        surface.blit(_cache["surface"], (0, 0))
