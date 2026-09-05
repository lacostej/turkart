"""Geometry helpers: track simplification and spatial grouping.

Rides carry a few thousand GPS points each, which is far more than a screen (or a
printed poster) can resolve. Simplifying up front keeps the browser tool
responsive; the full-resolution streams stay on disk for the final render.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

LatLng = Sequence[float]

EARTH_RADIUS_M = 6_371_000.0


def simplify(points: Sequence[LatLng], tolerance_m: float = 8.0) -> list[list[float]]:
    """Simplified track as [lat, lng] pairs."""
    return [[round(points[i][0], 5), round(points[i][1], 5)]
            for i in simplify_indices(points, tolerance_m)]


def simplify_indices(points: Sequence[LatLng], tolerance_m: float = 8.0) -> list[int]:
    """Ramer-Douglas-Peucker, in metres, returning the *indices* that survive.

    Returning indices rather than points lets the caller carry the other streams
    (distance, altitude, time) through the simplification without them drifting
    out of alignment with the geometry.

    Iterative rather than recursive: a 5000-point track with a pathological
    shape can otherwise blow the interpreter's stack.
    """
    if len(points) < 3:
        return list(range(len(points)))

    # Work in a local planar frame so the tolerance is a real distance. Longitude
    # degrees shrink with latitude, so scale them by cos(lat) about the midpoint.
    lat0 = math.radians(points[len(points) // 2][0])
    kx = math.cos(lat0) * math.pi / 180.0 * EARTH_RADIUS_M
    ky = math.pi / 180.0 * EARTH_RADIUS_M
    projected = [(p[1] * kx, p[0] * ky) for p in points]

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        furthest, best = -1.0, start
        ax, ay = projected[start]
        bx, by = projected[end]
        dx, dy = bx - ax, by - ay
        seg_sq = dx * dx + dy * dy
        for i in range(start + 1, end):
            px, py = projected[i]
            if seg_sq == 0:
                dist_sq = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_sq))
                dist_sq = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if dist_sq > furthest:
                furthest, best = dist_sq, i
        if furthest > tolerance_m * tolerance_m:
            keep[best] = True
            stack.append((start, best))
            stack.append((best, end))

    return [i for i, k in enumerate(keep) if k]


def bounds(tracks: Iterable[Sequence[LatLng]]) -> tuple[float, float, float, float] | None:
    """(south, west, north, east) over every point of every track."""
    south = west = math.inf
    north = east = -math.inf
    empty = True
    for track in tracks:
        # Points may carry extra channels (distance, altitude) past lat/lng.
        for point in track:
            lat, lng = point[0], point[1]
            empty = False
            south, north = min(south, lat), max(north, lat)
            west, east = min(west, lng), max(east, lng)
    return None if empty else (south, west, north, east)


def span_km(box: tuple[float, float, float, float]) -> tuple[float, float]:
    """Width and height of a bounding box, in kilometres."""
    south, west, north, east = box
    mid_lat = math.radians((south + north) / 2)
    height = (north - south) * math.pi / 180.0 * EARTH_RADIUS_M / 1000.0
    width = (east - west) * math.cos(mid_lat) * math.pi / 180.0 * EARTH_RADIUS_M / 1000.0
    return width, height


def haversine_km(a: LatLng, b: LatLng) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M / 1000.0 * math.asin(math.sqrt(h))


def cluster(centres: Sequence[LatLng], max_gap_km: float = 40.0) -> list[list[int]]:
    """Single-link clustering of ride centres.

    Answers "can these rides share one map?": any two rides closer than
    ``max_gap_km`` land in the same group, so a group is a set of rides that a
    single map sheet can plausibly cover.
    """
    n = len(centres)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if haversine_km(centres[i], centres[j]) <= max_gap_km:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=len, reverse=True)
