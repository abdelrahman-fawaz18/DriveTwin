"""Explicit-frame terrain sampling; no network, external files, or hidden flat fallback."""

import math
from dataclasses import dataclass, replace
from datetime import date

from .contracts import MAX_VERTICES, Origin, RoadPoint, RouteContext
from .geometry import interpolate
from .validation import choice, digest, integer, number, sequence, text


@dataclass(frozen=True)
class ElevationMetadata:
    dataset_id: str
    source: str
    content_sha256: str
    horizontal_frame: str
    vertical_datum: str
    nominal_resolution_m: float
    dataset_date: str
    license: str
    kind: str = "observed"
    date_basis: str = "catalog_datetime"

    def __post_init__(self) -> None:
        for name in ("dataset_id", "source", "horizontal_frame", "vertical_datum", "license"):
            text(getattr(self, name), name)
        digest(self.content_sha256, "terrain content digest")
        number(self.nominal_resolution_m, "terrain resolution", 0.01, 1_000)
        choice(self.kind, "elevation dataset kind", ("observed", "synthetic"))
        choice(
            self.date_basis,
            "dataset date basis",
            ("catalog_datetime", "acquisition", "retrieval", "synthetic"),
        )
        if not isinstance(self.dataset_date, str):
            raise ValueError("Dataset date required")
        date.fromisoformat(self.dataset_date)


@dataclass(frozen=True)
class ElevationGrid:
    """Row-major sample values, with origin at the first cell center, in meters."""

    metadata: ElevationMetadata
    width: int
    height: int
    origin_x_m: float
    origin_y_m: float
    step_x_m: float
    step_y_m: float
    values: tuple[float | None, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, ElevationMetadata):
            raise ValueError("Elevation metadata required")
        integer(self.width, "grid width", 2, 10_000)
        integer(self.height, "grid height", 2, 10_000)
        if self.width * self.height > 1_000_000:
            raise ValueError("Grid exceeds the cell budget")
        for name in ("origin_x_m", "origin_y_m"):
            number(getattr(self, name), name, -50_000_000, 50_000_000)
        for name in ("step_x_m", "step_y_m"):
            number(getattr(self, name), name, -1_000, 1_000)
            if abs(getattr(self, name)) < 0.01:
                raise ValueError("Grid spacing is too small")
        if not isinstance(self.values, tuple) or len(self.values) != self.width * self.height:
            raise ValueError("Grid values must match dimensions")
        for value in self.values:
            if value is not None:
                number(value, "terrain elevation", -1_000, 10_000)

    def sample(self, x_m: float, y_m: float) -> float | None:
        number(x_m, "sample x", -50_000_000, 50_000_000)
        number(y_m, "sample y", -50_000_000, 50_000_000)
        column = (x_m - self.origin_x_m) / self.step_x_m
        row = (y_m - self.origin_y_m) / self.step_y_m
        if not 0 <= column <= self.width - 1 or not 0 <= row <= self.height - 1:
            return None
        left, top = min(int(column), self.width - 2), min(int(row), self.height - 2)
        tx, ty = column - left, row - top
        corners = (
            (left, top, (1 - tx) * (1 - ty)),
            (left + 1, top, tx * (1 - ty)),
            (left, top + 1, (1 - tx) * ty),
            (left + 1, top + 1, tx * ty),
        )
        terms = []
        for col, r, weight in corners:
            if weight == 0:
                continue
            value = self.values[r * self.width + col]
            if value is None:
                return None
            terms.append(weight * value)
        return math.fsum(terms)


def densify(shape: tuple[RoadPoint, ...], spacing_m: float) -> tuple[RoadPoint, ...]:
    """Keep every original vertex and add distance-spaced samples between them."""
    sequence(shape, "shape", RoadPoint, 2, MAX_VERTICES)
    number(spacing_m, "sampling spacing", 0.1, 1_000)
    counts = [
        max(1, math.ceil(math.hypot(b.x_m - a.x_m, b.y_m - a.y_m) / spacing_m))
        for a, b in zip(shape, shape[1:])
    ]
    if 1 + sum(counts) > MAX_VERTICES:
        raise ValueError("Densified shape exceeds vertex budget")
    result = [shape[0]]
    for a, b, count in zip(shape[:-1], shape[1:], counts, strict=True):
        result.extend(interpolate(a, b, i / count) for i in range(1, count + 1))
    return tuple(result)


def enrich_route(context: RouteContext, grid: ElevationGrid, *, spacing_m: float) -> RouteContext:
    """Replace surface heights from one declared grid, preserving known structure profiles.

    Missing terrain stays missing. Bridge/tunnel geometry requires separately
    verified road height. No smoothing can remove an intermediate crest here.
    """
    if context.horizontal_frame != grid.metadata.horizontal_frame:
        raise ValueError("Route and terrain horizontal frames differ; transform explicitly first")
    if context.vertical_datum != grid.metadata.vertical_datum:
        raise ValueError("Route and terrain vertical datums differ")
    if spacing_m < grid.metadata.nominal_resolution_m:
        raise ValueError("Sampling finer than the dataset resolution needs a separate policy")
    occurrences = []
    count = 0
    for occurrence in context.occurrences:
        if occurrence.structure != "surface":
            if occurrence.elevation_quality not in ("verified_road", "synthetic"):
                raise ValueError("Structure needs a road-height profile")
            occurrences.append(occurrence)
            count += len(occurrence.shape)
            continue
        shape = tuple(
            replace(p, elevation_m=grid.sample(p.x_m, p.y_m))
            for p in densify(occurrence.shape, spacing_m)
        )
        count += len(shape)
        if count > MAX_VERTICES:
            raise ValueError("Enriched route exceeds total vertex budget")
        quality = "synthetic" if grid.metadata.kind == "synthetic" else "terrain"
        if any(p.elevation_m is None for p in shape):
            quality = "missing"
        occurrences.append(replace(occurrence, shape=shape, elevation_quality=quality))
    origin = Origin(grid.metadata.kind, grid.metadata.dataset_id, grid.metadata.content_sha256)
    return replace(context, elevation_origin=origin, occurrences=tuple(occurrences))
