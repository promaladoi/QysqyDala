"""Snowfall and window-edge accumulation in physical desktop pixels."""

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import chain
from math import ceil, exp, floor, inf, pi, sin, sqrt
from operator import itemgetter
from random import Random
from typing import NamedTuple


Rect = tuple[int, int, int, int]
Window = tuple[int, int, int, int, int]
Point = tuple[float, float]
Platform = tuple[int, int, int, int, float]
GroundSpan = tuple[int, int, int, Platform | None]
Visibility = Callable[[int, float, float], bool]
DepthSnapshot = tuple[int, int, int, list[float]]
_POINT_Y = itemgetter(1)

MAX_DEPTH = 27
GROUND_DEPTH = 42
CELL_WIDTH = 7
WINDOW_CORNER_INSET = 12
MAX_FLAKES = 1000
LANDING_BAND = 32
SHOVEL_REACH = 52
SHOVEL_STEP = 8
SHOVEL_TAIL = 12


def _platform_top(platform: Platform, x: float) -> float:
    left, top, right, _, radius = platform
    inset = max(0.0, left + radius - x, x - (right - radius))
    if inset == 0:
        return top
    return top + radius - sqrt(max(0.0, radius * radius - inset * inset))


def _ground_spans(ground: Rect, platforms: Sequence[Platform]) -> tuple[GroundSpan, ...]:
    """Split the floor only where a visible local platform can support snow."""
    left, top, right, bottom = ground
    if left >= right or top >= bottom:
        return ()
    raised = []
    boundaries = {left, right}
    for x1, y1, x2, y2, radius in platforms:
        if x1 >= x2 or y1 >= y2 or y1 >= bottom or y2 <= top:
            continue
        if y1 + y2 <= top + bottom or y2 - y1 > (bottom - top) / 2:
            continue  # Top/side taskbars clip rendering without lifting the bottom snow.
        radius = min(max(0.0, radius), (x2 - x1) / 2, (y2 - y1) / 2)
        # Only the arc above the floor acts as support during a partial reveal.
        hidden = max(0.0, radius - (bottom - y1))
        inset = radius - sqrt(max(0.0, radius * radius - hidden * hidden))
        start, end = max(left, ceil(x1 + inset)), min(right, floor(x2 - inset))
        if start < end:
            raised.append((start, end, (x1, y1, x2, y2, radius)))
            boundaries.update((start, end))
    edges = sorted(boundaries)
    spans = []
    for start, end in zip(edges, edges[1:]):
        middle = (start + end) / 2
        platform = min((shape for a, b, shape in raised if a <= middle < b),
                       key=lambda shape: _platform_top(shape, middle), default=None)
        spans.append((start, end, bottom, platform))
    return tuple(spans)


class DriftPolygon(list[Point]):
    """List-compatible bank outline with its supporting window and paint bounds."""

    __slots__ = ("hwnd", "left", "right", "top", "bottom", "curved")

    def __init__(self, points: list[Point], hwnd: int, left: int, right: int, curved: bool):
        super().__init__(points)
        self.hwnd = hwnd
        self.left, self.right = left, right
        self.top = min(map(_POINT_Y, self))
        self.bottom = max(map(_POINT_Y, self)) if curved else self[0][1]
        self.curved = curved


class BallLanding(NamedTuple):
    owner: int
    x: float
    y: float


@dataclass(slots=True)
class Flake:
    x: float
    y: float
    radius: float
    alpha: int
    speed: float
    wind: float
    phase: float
    frequency: float
    volume: float = 0.0
    may_bounce: bool = False
    _next_landing_base: float = field(default=-inf, repr=False, compare=False)
    shoveled: bool = False


@dataclass(slots=True)
class _Pile:
    width: int
    depths: list[float]
    max_depth: int = MAX_DEPTH
    vx: float = 0.0
    vy: float = 0.0
    revision: int = 0

    def depth_at(self, x: float) -> float:
        if not self.depths:
            return 0.0
        count = len(self.depths)
        index = min(max(x * count / self.width - 0.5, 0.0), count - 1)
        left = int(index)
        fraction = index - left
        return self.depths[left] * (1 - fraction) + self.depths[min(left + 1, count - 1)] * fraction

    def deposit(self, x: float, radius: float, volume: float = 0.0) -> None:
        if not self.depths:
            self.depths = [0.0] * ceil(self.width / CELL_WIDTH)
        count = len(self.depths)
        center = min(count - 1, int(x * count / self.width))
        weights = ((-2, 0.28), (-1, 0.78), (0, 1.65), (1, 0.78), (2, 0.28))
        if volume:
            total = sum(weight for offset, weight in weights if 0 <= center + offset < count)
            radius = volume * count / (total * self.width)
        changed = False
        for offset, weight in weights:
            index = center + offset
            if 0 <= index < count:
                depth = self.depths[index]
                settled = min(self.max_depth, depth + weight * radius)
                if settled != depth:
                    self.depths[index] = settled
                    changed = True
        if changed:
            self.revision += 1

    def slide(self, width: int, offset: float) -> list[tuple[float, float]]:
        """Translate a pile's mass across its support, returning off-edge portions."""
        if not self.depths:
            self.width = width
            return []
        if width == self.width and abs(offset) < 0.05:
            return []
        old_step = self.width / len(self.depths)
        new_count = ceil(width / CELL_WIDTH)
        new_step = width / new_count
        new_depths = [0.0] * new_count
        left_volume = right_volume = left_moment = right_moment = 0.0
        for i, depth in enumerate(self.depths):
            if not depth:
                continue
            start = i * old_step + offset
            end = start + old_step
            if start < 0:
                edge = min(end, 0.0)
                volume = depth * (edge - start)
                left_volume += volume
                left_moment += volume * (start + edge) / 2
                start = 0.0
            if end > width:
                edge = max(start, width)
                volume = depth * (end - edge)
                right_volume += volume
                right_moment += volume * (edge + end) / 2
                end = width
            if start < end:
                for j in range(int(start / new_step), min(new_count, ceil(end / new_step))):
                    cell_start, cell_end = j * new_step, (j + 1) * new_step
                    overlap_start = start if start > cell_start else cell_start
                    overlap_end = end if end < cell_end else cell_end
                    if overlap_end > overlap_start:
                        new_depths[j] += depth * (overlap_end - overlap_start) / new_step
        self.width = width
        self.depths = new_depths if any(new_depths) else []
        self.revision += 1
        return [(moment / volume, volume) for moment, volume in
                ((left_moment, left_volume), (right_moment, right_volume)) if volume >= 1.0]

    def loosen(self, fraction: float) -> Iterator[tuple[float, float]]:
        """Take small groups from a pile when its support drops away."""
        if not self.depths:
            return
        self.revision += 1
        step = self.width / len(self.depths)
        stride = ceil(len(self.depths) / 7)
        for start in range(0, len(self.depths), stride):
            end = min(start + stride, len(self.depths))
            volume = 0.0
            moment = 0.0
            for index in range(start, end):
                removed = self.depths[index] * fraction
                self.depths[index] -= removed
                volume += removed * step
                moment += (index + 0.5) * step * removed * step
            if volume >= 1.0:
                yield moment / volume, volume


@dataclass(slots=True)
class _Surface:
    left: int
    right: int
    y: int
    clearance: int
    window_left: int
    pile: _Pile
    hwnd: int = 0
    platform: Platform | None = None
    floor_y: int = 0
    drawn_revision: int = -1
    shape: DriftPolygon | None = None
    rendered_depths: DepthSnapshot | None = None
    outline: "_BankOutline | None" = None

    def baseline_at(self, x: float) -> float:
        return min(self.floor_y, _platform_top(self.platform, x)) if self.platform else self.y

    def depth_at(self, x: float) -> float:
        return min(self.clearance, self.pile.depth_at(x - self.window_left))

    def crossing_time(self, x: float, y: float, dx: float, dy: float) -> float | None:
        time = (self.y - y) / dy
        contact_x = x + dx * time
        on_flat = 0 <= time <= 1 and self.left <= contact_x < self.right
        if self.platform is None:
            return time if on_flat else None

        left, top, right, _, radius = self.platform
        middle_left, middle_right = left + radius, right - radius
        first = time if on_flat and middle_left <= contact_x <= middle_right else None
        center_y = top + radius
        start_y = y - center_y
        length_squared = dx * dx + dy * dy
        for center_x, direction in ((middle_left, -1), (middle_right, 1)):
            # Enter the upper quarter of the corner circle along the ball's path.
            start_x = x - center_x
            projection = start_x * dx + start_y * dy
            offset = start_x * start_x + start_y * start_y - radius * radius
            discriminant = projection * projection - length_squared * offset
            if discriminant < 0:
                continue
            time = (-projection - sqrt(discriminant)) / length_squared
            contact_x, contact_y = x + dx * time, y + dy * time
            if (0 <= time <= 1 and (first is None or time < first)
                    and self.left <= contact_x < self.right
                    and direction * (contact_x - center_x) >= 0
                    and contact_y <= min(center_y, self.floor_y)):
                first = time
        return first

    def visible_depths(self) -> DepthSnapshot | None:
        """Snapshot only the cells that can affect this clipped bank."""
        depths = self.pile.depths
        count = len(depths)
        if not count:
            return None
        scale = count / self.pile.width
        start = max(0, int((self.left - self.window_left) * scale - 0.5) - 1)
        end = min(count, int((self.right - self.window_left) * scale - 0.5) + 3)
        return self.pile.width, count, start, depths[start:end]


@lru_cache(maxsize=192)
def _outline_samples(width: int, offset: int, pile_width: int, count: int):
    """Precompute interpolation and crest geometry shared across moving banks."""
    segments = max(2, min(640, ceil(width / CELL_WIDTH)))
    step = width / segments
    scale = count / pile_width
    samples = []
    styles = []
    for index in range(segments + 1):
        x = index * step
        cell = min(max((offset + x) * scale - 0.5, 0.0), count - 1)
        left = int(cell)
        samples.append((left, min(left + 1, count - 1), cell - left))
        local = offset + x
        wave = 0.5 + 0.32 * sin(local / 34) + 0.18 * sin(local / 14 + 0.7)
        styles.append((min(x, width - x), wave))
    return step, tuple(samples), tuple(styles)


@lru_cache(maxsize=96)
def _outline_curve(width: int, segments: int, left: int, right: int, radius: float,
                   floor_offset: float):
    """Cache capsule arcs in local coordinates so vertical motion reuses them."""
    platform = (left, 0, right, 0, radius)

    def baseline(x):
        return min(floor_offset, _platform_top(platform, x))

    step = width / segments
    nodes = tuple(baseline(index * step) for index in range(segments + 1))
    midpoints = tuple(baseline((index + 0.5) * step) for index in range(segments))
    return_x = set()
    for low, high in ((left, left + radius), (right - radius, right)):
        low, high = max(0, low), min(width, high)
        if low < high:
            count = min(24, ceil((high - low) / 2))
            for index in range(count + 1):
                x = low + (high - low) * index / count
                if 0 < x < width:
                    return_x.add(x)
    underside = tuple((x, baseline(x)) for x in sorted(return_x, reverse=True))
    return nodes, midpoints, underside


class _BankOutline:
    """Keep the intermediate samples so small deposits only rebuild nearby vertices."""

    def __init__(self, surface: _Surface, pile_width: int, count: int):
        start, end, y = surface.left, surface.right, surface.y
        width = end - start
        self.step, self.samples, self.styles = _outline_samples(
            width, start - surface.window_left, pile_width, count,
        )
        self.sample_left = tuple(left for left, _, _ in self.samples)
        self.sample_right = tuple(right for _, right, _ in self.samples)
        self.segments = len(self.samples) - 1
        self.raw = [0.0] * len(self.samples)
        self.profile = [0.0] * len(self.samples)
        self.points = [()] * self.segments
        self.taper = None
        if surface.platform is None:
            self.nodes = self.midpoints = (y,) * len(self.samples)
            self.underside = ()
        else:
            left, _, right, _, radius = surface.platform
            nodes, midpoints, underside = _outline_curve(
                width, self.segments, left - start, right - start, radius,
                min(radius, surface.floor_y - y),
            )
            self.nodes = tuple(y + offset for offset in nodes)
            self.midpoints = tuple(y + offset for offset in midpoints)
            self.underside = tuple((start + x, y + offset) for x, offset in underside)

    def update(self, surface: _Surface, current: DepthSnapshot,
               previous: DepthSnapshot | None) -> DriftPolygon | None:
        segments = self.segments
        raw_start, raw_end = 0, segments + 1
        if previous is not None:
            _, _, offset, new_depths = current
            old_depths = previous[3]
            left, right = 0, len(new_depths) - 1
            while old_depths[left] == new_depths[left]:
                left += 1
            while old_depths[right] == new_depths[right]:
                right -= 1
            raw_start = bisect_left(self.sample_right, offset + left)
            raw_end = bisect_right(self.sample_left, offset + right)

        depths = surface.pile.depths
        raw = self.raw
        clearance = surface.clearance
        for index in range(raw_start, raw_end):
            left, right, fraction = self.samples[index]
            depth = depths[left] * (1 - fraction) + depths[right] * fraction
            raw[index] = depth if depth < clearance else clearance
        peak = max(raw)
        if peak < 0.45:
            self.taper = None
            return None
        start, end = surface.left, surface.right
        taper = min((end - start) / 2, max(8, min(30, peak)))
        first, last = 1, segments
        if taper == self.taper:
            # Smoothing reads the adjacent samples on either side.
            first, last = max(1, raw_start - 1), min(segments, raw_end + 1)
        self.taper = taper
        crest_start = 0.68 * clearance
        profile = self.profile
        for index in range(first, last):
            edge_distance, wave = self.styles[index]
            depth = 0.70 * raw[index] + 0.15 * (raw[index - 1] + raw[index + 1])
            if depth > crest_start:
                # Saturated cells retain an irregular crest.
                crest = (depth / clearance - 0.68) / 0.32
                depth *= 1.0 - 0.21 * crest * crest * wave
            if edge_distance < taper:
                edge = edge_distance / taper
                depth *= edge * edge * (3.0 - 2.0 * edge)
            profile[index] = depth

        step, nodes, midpoints = self.step, self.nodes, self.midpoints
        # The midpoint and optional bend vertex also depend on neighbors.
        for index in range(max(0, first - 1), min(segments, last + 1)):
            depth = profile[index]
            next_depth = profile[index + 1]
            midpoint = (start + (index + 0.5) * step, midpoints[index] - (depth + next_depth) / 2)
            if index and abs(profile[index - 1] - 2 * depth + next_depth) >= 0.45:
                rounded = (profile[index - 1] + 6 * depth + next_depth) / 8
                self.points[index] = (start + index * step, nodes[index] - rounded), midpoint
            else:
                self.points[index] = (midpoint,)
        points = [(start, nodes[0])]
        points.extend(chain.from_iterable(self.points))
        points.append((end, nodes[-1]))
        points.extend(self.underside)
        return DriftPolygon(points, surface.hwnd, start, end, surface.platform is not None)


def _limit_spans(
    spans: list[tuple[int, int, int]], blocker_left: int, blocker_right: int, clearance: int
) -> list[tuple[int, int, int]]:
    """Clip the drift against a higher window without covering that window."""
    result = []
    for left, right, limit in spans:
        start = max(left, blocker_left)
        end = min(right, blocker_right)
        if start >= end or clearance >= limit:
            result.append((left, right, limit))
            continue
        if left < start:
            result.append((left, start, limit))
        if clearance:
            result.append((start, end, clearance))
        if end < right:
            result.append((end, right, limit))
    return result


class SnowSimulation:
    def __init__(self, seed: int | None = None) -> None:
        self.particles: list[Flake] = []
        self._random = Random(seed)
        self._monitors: tuple[Rect, ...] = ()
        self._monitor_weights: list[int] = []
        self._grounds: tuple[Rect, ...] = ()
        self._platforms: tuple[tuple[Platform, ...], ...] = ()
        self._ground_profiles: tuple[tuple[GroundSpan, ...], ...] = ()
        self._curve_reach = 0.0
        self._ground_piles: list[_Pile] = []
        self._desktop_bounds: tuple[int, int, int] = (0, 0, 0)
        self._windows: tuple[Window, ...] = ()
        self._window_rank: dict[int, int] = {}
        self._piles: dict[int, _Pile] = {}
        self._surfaces: list[_Surface] = []
        self._surface_ys: list[int] = []
        self._landing_bands: dict[int, list[_Surface]] = {}
        self._adjacent_landing_bands: dict[int, list[_Surface]] = {}
        self._occupied_landing_bands: tuple[int, ...] = ()
        self._previous_landing_bands = None
        self._stable_landing_frames = 0
        self._first_landing_y = float("inf")
        self._sliding = False
        self._spawn_balance = 0.0
        self._needs_seed = True
        self._time = 0.0
        self._melt_elapsed = 0.0

    def configure(self, monitors: Sequence[Rect], grounds: Sequence[Rect] | None = None) -> None:
        """Set active monitors using half-open (left, top, right, bottom) bounds."""
        screens = tuple(dict.fromkeys(rect for rect in monitors if rect[0] < rect[2] and rect[1] < rect[3]))
        floors = screens if grounds is None else tuple(grounds)
        if len(floors) != len(screens):
            raise ValueError("Supply one ground rectangle for each monitor")
        if self._monitors == screens:
            self.update_grounds(floors)
            return
        self._monitors = screens
        self._grounds = floors
        self._platforms = tuple(() for _ in screens)
        self._update_ground_profiles()
        # Ground depth stays in monitor coordinates while taskbars change the exposed floor.
        self._ground_piles = [_Pile(right - left, [], GROUND_DEPTH) for left, _, right, _ in screens]
        self._monitor_weights = [right - left for left, _, right, _ in screens]
        if screens:
            self._desktop_bounds = (
                min(left for left, _, _, _ in screens),
                max(right for _, _, right, _ in screens),
                max(bottom for _, _, _, bottom in screens),
            )
        self.clear()
        self._rebuild_surfaces()

    def update_grounds(self, grounds: Sequence[Rect],
                       platforms: Sequence[Sequence[Platform]] | None = None) -> None:
        """Move the exposed floor without clearing its snow or changing monitor topology."""
        floors = tuple(grounds)
        if len(floors) != len(self._monitors):
            raise ValueError("Supply one ground rectangle for each monitor")
        shapes = (tuple(() for _ in floors) if platforms is None
                  else tuple(tuple(panel) for panel in platforms))
        if len(shapes) != len(floors):
            raise ValueError("Supply one platform sequence for each monitor")
        if floors != self._grounds or shapes != self._platforms:
            self._grounds = floors
            self._platforms = shapes
            self._update_ground_profiles()
            self._rebuild_surfaces()

    def _update_ground_profiles(self):
        self._ground_profiles = tuple(_ground_spans(ground, shapes)
                                      for ground, shapes in zip(self._grounds, self._platforms))
        self._curve_reach = max((platform[4] for spans in self._ground_profiles
                                 for _, _, _, platform in spans if platform), default=0.0)

    def ground_y_at(self, x: float, y: float) -> int | None:
        """Return the floor nearest an existing ground figure, even if temporarily covered."""
        nearest = None
        best_distance = float("inf")
        for screen, ground, spans in zip(self._monitors, self._grounds, self._ground_profiles):
            if screen[0] <= x < screen[2]:
                bottom = ground[3]
                for left, right, _, platform in spans:
                    if left <= x < right:
                        if platform:
                            bottom = round(min(bottom, _platform_top(platform, x)))
                        break
                distance = abs(bottom - y)
                if distance == 0:
                    return bottom
                if nearest is None or distance < best_distance:
                    nearest, best_distance = bottom, distance
        return nearest

    def update_windows(self, windows: Sequence[Window], elapsed: float = 1 / 60) -> None:
        """Set (HWND, left, top, right, bottom) for visible windows, frontmost first."""
        snapshot = tuple(window for window in windows if window[1] < window[3] and window[2] < window[4])
        changed = snapshot != self._windows
        if not changed and not self._sliding:
            return
        elapsed = min(0.12, max(1 / 240, elapsed))
        previous = {window[0]: window for window in self._windows}
        current = {}
        self._sliding = False
        for hwnd, left, top, right, _ in snapshot:
            pile = self._piles.get(hwnd)
            if pile is None:
                pile = _Pile(right - left, [])
            elif pile.depths:
                _, old_left, old_top, _, _ = previous[hwnd]
                self._move_pile(pile, left, top, right - left, old_left, old_top, elapsed)
                self._sliding |= bool(pile.vx or pile.vy)
            else:
                pile.width = right - left
                pile.vx = pile.vy = 0.0
            current[hwnd] = pile
        if changed:
            for hwnd, left, top, _, _ in self._windows:
                if hwnd not in current:
                    pile = self._piles[hwnd]
                    for x, volume in pile.loosen(1.0):
                        self._spill(left + x, top - 3, volume, pile.vx, pile.vy)
        self._piles = current
        self._windows = snapshot
        if changed:
            self._window_rank = {window[0]: index for index, window in enumerate(snapshot)}
            self._rebuild_surfaces()

    def _move_pile(self, pile, left, top, width, old_left, old_top, dt):
        dx, dy = left - old_left, top - old_top
        # Static friction prevents a one-pixel frame wobble from shaking loose snow.
        if abs(dx) <= 1 and abs(pile.vx) < 7 and width == pile.width:
            pile.vx = 0.0
        else:
            target = min(2400, max(-2400, dx / dt)) if width == pile.width else 0.0
            pile.vx += (target - pile.vx) * (1 - exp(-dt / 0.16))
            shift = pile.vx * dt - dx
            for x, volume in pile.slide(width, shift):
                self._spill(left + x, min(top, old_top) - 3, volume, pile.vx, 0.0)
        if dy > 3:
            speed = min(2400, dy / dt)
            # A fast downward tug removes support faster than gravity can follow.
            acceleration = (speed - pile.vy) / dt
            if speed > 240 and acceleration > 1200:
                fraction = min(0.48, (acceleration - 1200) / 42000)
                for x, volume in pile.loosen(fraction):
                    self._spill(left + x, old_top - 3, volume, pile.vx, min(pile.vy, 160))
            pile.vy = speed
        else:
            pile.vy = 0.0

    def _rebuild_surfaces(self) -> None:
        surfaces = []
        previous: list[Rect] = []
        existing = {(s.hwnd, s.left, s.right, s.y, s.clearance, s.window_left, s.platform, s.floor_y): s
                    for s in self._surfaces}

        def exposed(left, right, top, clearance, origin, pile, hwnd, platform=None, floor_y=0):
            surface = existing.get((hwnd, left, right, top, clearance, origin, platform, floor_y))
            if surface is None or surface.pile is not pile:
                surface = _Surface(left, right, top, clearance, origin, pile, hwnd, platform, floor_y)
            return surface

        for hwnd, left, top, right, bottom in self._windows:
            pile = self._piles[hwnd]
            inset = min(WINDOW_CORNER_INSET, (right - left) // 6)
            for screen_left, screen_top, screen_right, screen_bottom in self._monitors:
                start, end = max(left + inset, screen_left), min(right - inset, screen_right)
                if start >= end or not screen_top < top <= screen_bottom:
                    continue
                spans = [(start, end, min(MAX_DEPTH, top - screen_top))]
                for blocker_left, blocker_top, blocker_right, blocker_bottom in previous:
                    if (blocker_top <= top and blocker_bottom > top - MAX_DEPTH and
                            blocker_left < end and blocker_right > start):
                        clearance = max(0, top - blocker_bottom)
                        spans = _limit_spans(spans, blocker_left, blocker_right, clearance)
                        if not spans:
                            break
                surfaces.extend(exposed(a, b, top, limit, left, pile, hwnd) for a, b, limit in spans)
            previous.append((left, top, right, bottom))
        for profiles, ground, pile, screen in zip(
                self._ground_profiles, self._grounds, self._ground_piles, self._monitors):
            for left, right, floor_y, platform in profiles:
                y = floor_y if platform is None else platform[1]
                clearance = min(GROUND_DEPTH, y - ground[1])
                if clearance <= 0:
                    continue
                spans = [(left, right, clearance)]
                for blocker_left, blocker_top, blocker_right, blocker_bottom in previous:
                    if (blocker_top < y and blocker_bottom > y - GROUND_DEPTH and
                            blocker_left < right and blocker_right > left):
                        limit = max(0, y - blocker_bottom)
                        spans = _limit_spans(spans, blocker_left, blocker_right, limit)
                        if not spans:
                            break
                curve = platform if platform and platform[4] else None
                surfaces.extend(exposed(a, b, y, limit, screen[0], pile, 0, curve, floor_y)
                                for a, b, limit in spans)
        self._surfaces = sorted(surfaces, key=lambda surface: surface.y)
        self._surface_ys = [surface.y for surface in self._surfaces]
        bands: dict[int, list[_Surface]] = {}
        adjacent: dict[int, list[_Surface]] = {}
        first_landing_y = float("inf")
        for surface in self._surfaces:
            high = surface.y + (surface.platform[4] if surface.platform else 0)
            low = surface.y - surface.clearance
            if low < first_landing_y:
                first_landing_y = low
            first = floor(low / LANDING_BAND)
            last = floor(high / LANDING_BAND)
            for band in range(first, last + 1):
                bands.setdefault(band, []).append(surface)
                adjacent.setdefault(band, []).append(surface)
            adjacent.setdefault(last + 1, []).append(surface)
        self._landing_bands = bands
        self._adjacent_landing_bands = adjacent
        self._first_landing_y = first_landing_y

    def clear(self) -> None:
        """Drop all flakes and drifts, retaining the current display and window geometry."""
        self.particles.clear()
        for pile in (*self._piles.values(), *self._ground_piles):
            pile.depths.clear()
            pile.vx = pile.vy = 0.0
            pile.revision += 1
        self._sliding = False
        self._spawn_balance = 0.0
        self._needs_seed = True
        self._melt_elapsed = 0.0

    def surface_y(self, x: float, y: float, reach: float = 40) -> int | None:
        """Find the closest exposed support beneath the pointer."""
        surface = self._nearby_surface(x, y, reach)
        return round(surface.baseline_at(x)) if surface else None

    def owner_rank(self, hwnd: int) -> int:
        """Order known windows front first, then ground, then vanished owners."""
        return self._window_rank.get(hwnd, len(self._window_rank) + (hwnd != 0))

    def support_at(self, x: float, y: float, reach: float = 40, *, hwnd: int | None = None,
                   visible: Visibility | None = None) -> tuple[int, int, int, int] | None:
        """Return (HWND or 0 for ground, left, right, y) for the closest visible ledge."""
        surface = self._nearby_surface(x, y, reach, hwnd=hwnd, visible=visible)
        if surface is not None:
            return surface.hwnd, surface.left, surface.right, round(surface.baseline_at(x))
        return None

    def support_spans(self) -> Iterator[tuple[int, int, int, int]]:
        """Yield exposed (owner, left, right, midpoint baseline) spans."""
        for surface in self._surfaces:
            midpoint = (surface.left + surface.right) / 2
            yield surface.hwnd, surface.left, surface.right, round(surface.baseline_at(midpoint))

    def ball_landing(self, old_x: float, old_base_y: float, new_x: float, new_base_y: float, *,
                     ignore_owner: int | None = None) -> BallLanding | None:
        """Find the first exposed ledge crossed by a crafted ball's bottom."""
        dy = new_base_y - old_base_y
        if dy <= 0:
            return None
        dx = new_x - old_x
        left, right = sorted((old_x, new_x))
        first = bisect_left(self._surface_ys, old_base_y - self._curve_reach)
        last = bisect_right(self._surface_ys, new_base_y)
        earliest = (float("inf"), 0)
        landing = None
        for index in range(first, last):
            surface = self._surfaces[index]
            if surface.hwnd == ignore_owner or right < surface.left or left >= surface.right:
                continue
            time = surface.crossing_time(old_x, old_base_y, dx, dy)
            if time is None:
                continue
            priority = time, self.owner_rank(surface.hwnd)
            if priority < earliest:
                x = old_x + dx * time
                y = surface.baseline_at(x)
                # A rounded dock corner can dip behind a window below its nominal top.
                if surface.platform and any(
                    left <= x < right and top < y <= bottom
                    for _, left, top, right, bottom in self._windows
                ):
                    continue
                earliest = priority
                landing = BallLanding(surface.hwnd, x, y)
        return landing

    def ball_outside(self, x: float, baseline_y: float, radius: float) -> bool:
        """Check whether the whole ball has left the desktop sides or bottom."""
        left, right, bottom = self._desktop_bounds
        return x + radius < left or x - radius >= right or baseline_y - 2 * radius >= bottom

    def _nearby_surface(self, x: float, y: float, reach: float,
                        above: float | None = None, *, hwnd: int | None = None,
                        visible: Visibility | None = None) -> _Surface | None:
        min_y = y - (reach if above is None else min(reach, above))
        first = bisect_left(self._surface_ys, min_y - self._curve_reach)
        last = bisect_right(self._surface_ys, y + reach)
        best = reach
        nearest = None
        for index in range(first, last):
            surface = self._surfaces[index]
            if surface.left <= x < surface.right:
                if hwnd is not None and surface.hwnd != hwnd:
                    continue
                ledge = surface.baseline_at(x)
                if ledge < min_y:
                    continue
                distance = abs(ledge - y)
                if distance < best or (nearest is None and distance <= best):
                    if visible is None or visible(surface.hwnd, x, y):
                        best, nearest = distance, surface
        return nearest

    def collect(self, x: float, y: float, radius: float, *, minimum: float = 0.0,
                maximum: float | None = None, reach: float | None = None,
                above: float | None = None, hwnd: int | None = None,
                visible: Visibility | None = None) -> float:
        """Remove exposed snow intersecting a ball and return the harvested area."""
        if radius <= 0 or (maximum is not None and maximum <= 0):
            return 0.0
        surface = self._nearby_surface(x, y + radius, radius if reach is None else reach, above,
                                       hwnd=hwnd, visible=visible)
        if surface is None or not surface.pile.depths:
            return 0.0
        pile = surface.pile
        step = pile.width / len(pile.depths)
        left = max(x - radius, surface.left) - surface.window_left
        right = min(x + radius, surface.right) - surface.window_left
        portions = []
        for index in range(int(left / step), min(len(pile.depths), ceil(right / step))):
            overlap = min(right, (index + 1) * step) - max(left, index * step)
            depth = min(pile.depths[index], surface.clearance)
            portions.append((index, depth * overlap))
        available = sum(volume for _, volume in portions)
        removed = min(available, maximum) if maximum is not None else available
        if available <= 0 or removed < minimum:
            return 0.0
        scale = removed / (available * step)
        for index, volume in portions:
            pile.depths[index] = max(0.0, pile.depths[index] - volume * scale)
        pile.revision += 1
        return removed

    def shovel(self, x0: float, y0: float, x1: float, y1: float, *,
               hwnd: int | None = None, visible: Visibility | None = None) -> float:
        """Push settled snow ahead of a short horizontal stroke on the same ledge."""
        dx = x1 - x0
        if abs(dx) < 0.5:
            return 0.0
        surface = self._nearby_surface(x0, y0, SHOVEL_REACH, 8, hwnd=hwnd, visible=visible)
        if surface is None or not surface.pile.depths:
            return 0.0
        direction = 1 if dx > 0 else -1
        end_x = surface.right if direction > 0 else surface.left
        travel = min(abs(dx), abs(end_x - x0) + 1)
        pieces = ceil(travel / SHOVEL_STEP)
        pile = surface.pile
        origin = surface.window_left
        inset = min(WINDOW_CORNER_INSET, pile.width // 6) if surface.hwnd else 0
        real_end = (surface.right == origin + pile.width - inset if direction > 0
                    else surface.left == origin + inset)
        replace = None
        if len(self.particles) >= MAX_FLAKES:
            # An ordinary flake can yield its slot to snow moved by the player.
            replace = next((i for i in range(len(self.particles) - 1, -1, -1)
                            if not self.particles[i].volume), None)
        can_spill = len(self.particles) < MAX_FLAKES or replace is not None
        moved = 0.0
        overflow = 0.0
        previous = x0
        for index in range(1, pieces + 1):
            x = x0 + direction * travel * index / pieces
            y = y0 + (y1 - y0) * (x - x0) / dx
            crossed_edge = not surface.left <= x < surface.right
            blade_x = min(surface.right - 0.001, max(surface.left, x))
            offset_y = y - surface.baseline_at(blade_x)
            if not -SHOVEL_REACH <= offset_y <= 8:
                break
            if visible is not None and not visible(surface.hwnd, blade_x, y):
                break
            off_window = bool(crossed_edge and real_end and surface.hwnd)
            swept, excess = self._shovel_strip(surface, previous, blade_x, direction,
                                               offset_y, can_spill, real_end, off_window, visible)
            moved += swept
            if excess or (off_window and overflow):
                overflow += excess
                launch_x = blade_x
                launch_top = surface.baseline_at(blade_x)
                if off_window:
                    launch_x = origin + pile.width + 1 if direction > 0 else origin - 1
                else:
                    launch_top -= surface.depth_at(blade_x)
            if crossed_edge:
                break
            previous = x
        if overflow:
            if replace is not None:
                self.particles.pop(replace)
            radius = min(7.5, max(1.25, 0.45 * sqrt(overflow)))
            self.release_ball(launch_x, launch_top - radius - 1, radius,
                              direction * 125, -42, volume=overflow, shoveled=True)
        if moved:
            pile.revision += 1
        return moved

    def _shovel_strip(self, surface: _Surface, start: float, blade: float, direction: int,
                      offset_y: float, can_spill: bool, real_end: bool, off_window: bool,
                      visible: Visibility | None) -> tuple[float, float]:
        pile = surface.pile
        depths = pile.depths
        cell_width = pile.width / len(depths)
        origin = surface.window_left
        first = max(0, ceil((surface.left - origin) / cell_width))
        end = min(len(depths), floor((surface.right - origin) / cell_width + 1e-9))
        if first >= end:
            return 0.0, 0.0
        boundary = min(end, max(first, round((blade - origin) / cell_width)))
        tail = (start - origin - direction * SHOVEL_TAIL) / cell_width
        if direction > 0:
            sources = range(boundary - 1, max(first, floor(tail)) - 1, -1)
            targets = range(boundary, min(end, boundary + 4))
        else:
            sources = range(boundary, min(end, ceil(tail)))
            targets = range(boundary - 1, max(first, boundary - 4) - 1, -1)

        def accessible(index):
            if visible is None:
                return True
            x = origin + (index + 0.5) * cell_width
            return visible(surface.hwnd, x, surface.baseline_at(x) + offset_y)

        limit = min(surface.clearance, pile.max_depth)
        available = []
        for index in sources:
            if not accessible(index):
                break
            volume = min(depths[index], limit) * cell_width
            if volume > 0:
                available.append((index, volume))
        if not available:
            return 0.0, 0.0
        free = []
        clear_ahead = len(targets) == 4 or real_end
        for index in targets:
            if not accessible(index):
                clear_ahead = False
                break
            capacity = (limit - depths[index]) * cell_width
            if capacity > 0:
                free.append((index, capacity))

        budget = sum(volume for _, volume in available)
        placed = 0.0 if off_window and can_spill else min(budget, sum(capacity for _, capacity in free))
        overflow = budget - placed if can_spill and (off_window or clear_ahead) else 0.0
        moved = placed + overflow
        if moved <= 0:
            return 0.0, 0.0

        remaining = moved
        for index, volume in available:
            amount = min(remaining, volume)
            depths[index] = max(0.0, depths[index] - amount / cell_width)
            remaining -= amount
            if remaining <= 0:
                break
        remaining = placed
        for index, capacity in free:
            amount = min(remaining, capacity)
            depths[index] += amount / cell_width
            remaining -= amount
            if remaining <= 0:
                break
        return moved, overflow

    def release_ball(self, x: float, y: float, radius: float, vx: float, vy: float,
                     *, volume: float | None = None, shoveled: bool = False) -> None:
        """Send a clump into free fall so it can land on another surface."""
        amount = pi * radius * radius if volume is None else volume
        if len(self.particles) < MAX_FLAKES and radius > 0 and amount > 0:
            self.particles.append(Flake(x, y, radius, 246, vy, vx, 0.0, 0.0,
                                        amount, may_bounce=radius >= 7, shoveled=shoveled))

    @staticmethod
    def _deposit_shoveled(surface: _Surface, x: float, amount: float) -> float:
        """Pack moved snow onto this visible support and return any remainder."""
        pile = surface.pile
        count = len(pile.depths) or ceil(pile.width / CELL_WIDTH)
        step = pile.width / count
        left = max(0, ceil((surface.left - surface.window_left) / step))
        right = min(count, floor((surface.right - surface.window_left) / step + 1e-9))
        if left >= right:
            return amount
        if not pile.depths:
            pile.depths = [0.0] * count
        center = min(right - 1, max(left, floor((x - surface.window_left) / step)))
        limit = min(surface.clearance, pile.max_depth)
        remaining = amount
        for offset in (0, -1, 1, -2, 2):
            index = center + offset
            if left <= index < right:
                depth = pile.depths[index]
                added = min(remaining, max(0.0, limit - depth) * step)
                pile.depths[index] = depth + added / step
                remaining -= added
                if remaining <= 0:
                    break
        if remaining < amount:
            pile.revision += 1
        return remaining

    def _spill(self, x: float, y: float, volume: float, vx: float, vy: float) -> None:
        count = min(5, max(1, ceil(volume / 44)))
        each = volume / count
        radius = min(5.4, max(1.15, sqrt(each) * 0.42))
        for _ in range(count):
            self.release_ball(x + self._random.uniform(-3, 3), y - radius,
                              radius, vx * 0.72 + self._random.uniform(-38, 38),
                              vy + self._random.uniform(-38, 18), volume=each)

    def _spawn(self, count: int, *, initial: bool = False) -> None:
        rng = self._random
        count = min(count, MAX_FLAKES - len(self.particles))
        for left, top, right, bottom in rng.choices(self._monitors, weights=self._monitor_weights, k=count):
            radius = 0.85 + 2.25 * rng.random() ** 1.7
            self.particles.append(
                Flake(
                    x=rng.uniform(left, right),
                    y=rng.uniform(top, bottom) if initial else top - 4.0,
                    radius=radius,
                    alpha=rng.randint(138, 245),
                    speed=83 + 38 * radius + rng.uniform(-15, 30),
                    wind=rng.uniform(-13, 30),
                    phase=rng.uniform(0, 6.283),
                    frequency=rng.uniform(0.8, 2.6),
                )
            )

    def _landing(self, flake: Flake, x: float, y: float) -> _Surface | None:
        old_base, new_base = flake.y + flake.radius, y + flake.radius
        band = floor(new_base / LANDING_BAND)
        band_start = band * LANDING_BAND
        if old_base >= band_start:
            candidates = self._landing_bands.get(band)
        elif old_base >= band_start - LANDING_BAND:
            # This band and the one immediately above it are indexed together.
            candidates = self._adjacent_landing_bands.get(band)
        else:
            # A fast clump can cross several bands.
            first = bisect_left(self._surface_ys, old_base - self._curve_reach)
            last = bisect_right(self._surface_ys, new_base + GROUND_DEPTH)
            candidates = self._surfaces[first:last]
        if not candidates:
            return None
        earliest = new_base
        landing = None
        for surface in candidates:
            if surface.left <= x < surface.right:
                baseline = surface.baseline_at(x)
                top = baseline - surface.depth_at(x)
                if flake.shoveled and top < old_base <= baseline:
                    # Another clump can raise the bank into this one during the same frame.
                    top = old_base
                if old_base <= top <= earliest:
                    earliest = top
                    landing = surface
        return landing

    def _landing_on_open_desktop(self, flake: Flake, x: float, y: float) -> _Surface | None:
        # There is nothing to land on until the flake approaches the floor or taskbar.
        if y + flake.radius < self._first_landing_y:
            return None
        return self._landing(flake, x, y)

    def _landing_with_stationary_windows(self, flake: Flake, x: float, y: float) -> _Surface | None:
        base = y + flake.radius
        if base < flake._next_landing_base:
            return None
        landing = self._landing(flake, x, y)
        if landing is None and not flake.volume:
            band = floor(base / LANDING_BAND)
            if band not in self._landing_bands:
                occupied = self._occupied_landing_bands
                next_band = bisect_right(occupied, band)
                flake._next_landing_base = (
                    occupied[next_band] * LANDING_BAND if next_band < len(occupied) else inf
                )
        return landing

    def _frame_landing(self):
        if not self._windows:
            return self._landing_on_open_desktop
        if self._previous_landing_bands is not self._landing_bands:
            self._previous_landing_bands = self._landing_bands
            self._stable_landing_frames = 0
            return self._landing
        self._stable_landing_frames += 1
        if self._stable_landing_frames < 3:
            return self._landing
        if self._stable_landing_frames == 3:
            # After movement stops, restart from the first possible occupied band.
            occupied = tuple(sorted(self._landing_bands))
            self._occupied_landing_bands = occupied
            first_base = occupied[0] * LANDING_BAND if occupied else inf
            for flake in self.particles:
                flake._next_landing_base = first_base
        return self._landing_with_stationary_windows

    def step(self, dt: float, intensity: float) -> None:
        """Advance by dt seconds at a normalized intensity between 0 and 1."""
        if dt <= 0 or intensity <= 0 or not self._monitors:
            return
        dt = min(dt, 0.12)
        self._time += dt
        rate = min(190, 120 * min(intensity, 1.0) ** 1.25 * sum(self._monitor_weights) / 1920)
        if self._needs_seed:
            self._spawn(ceil(rate * 1.6), initial=True)
            self._needs_seed = False
        self._spawn_balance += rate * dt
        count = int(self._spawn_balance)
        if count:
            self._spawn(count)
            self._spawn_balance -= count

        left, right, bottom = self._desktop_bounds
        left -= 64
        right += 64
        time = self._time
        gravity_step = 760 * dt
        half_gravity_step = 380 * dt
        wind_decay = 1 - 0.55 * dt
        land = self._frame_landing()
        falling = []
        for flake in self.particles:
            if flake.volume:
                x = flake.x + flake.wind * dt
                y = flake.y + (flake.speed + half_gravity_step) * dt
                flake.speed = min(880, flake.speed + gravity_step)
                flake.wind *= wind_decay
            else:
                x = flake.x + (flake.wind + 19 * sin(time * flake.frequency + flake.phase)) * dt
                y = flake.y + flake.speed * dt
            if not left <= x <= right:
                continue
            surface = land(flake, x, y) if y >= flake.y else None
            if surface is not None:
                if flake.shoveled:
                    flake.volume = self._deposit_shoveled(surface, x, flake.volume)
                    if flake.volume > 1e-9:
                        flake.x = x
                        flake.y = surface.baseline_at(x) - surface.depth_at(x) - flake.radius - 0.1
                        flake.speed = -44.0
                        flake.wind = max(75.0, flake.wind) if flake.wind > 0 else min(-75.0, flake.wind)
                        falling.append(flake)
                elif flake.may_bounce:
                    flake.x = x
                    flake.y = surface.baseline_at(x) - surface.depth_at(x) - flake.radius - 0.1
                    flake.speed = -min(112.0, max(42.0, flake.speed * 0.22))
                    flake.wind *= 0.72
                    flake.may_bounce = False
                    falling.append(flake)
                else:
                    surface.pile.deposit(x - surface.window_left, flake.radius, flake.volume)
            elif y - flake.radius < bottom:
                flake.x, flake.y = x, y
                falling.append(flake)
        self.particles = falling

        self._melt_elapsed += dt
        if self._melt_elapsed >= 1.0:
            decay = exp(-0.009 * self._melt_elapsed)
            for pile in (*self._piles.values(), *self._ground_piles):
                if pile.depths:
                    pile.depths = [depth * decay if depth > 0.06 else 0.0 for depth in pile.depths]
                    if not any(pile.depths):
                        pile.depths.clear()
                    pile.revision += 1
            self._melt_elapsed = 0.0

    def drifts(self) -> Iterator[DriftPolygon]:
        """Yield each cached bank. Its HWND is zero for ground and taskbar snow."""
        for surface in self._surfaces:
            if surface.drawn_revision != surface.pile.revision:
                depths = surface.visible_depths()
                if depths != surface.rendered_depths:
                    surface.shape = self._drift_polygon(surface, depths, surface.rendered_depths)
                    surface.rendered_depths = depths
                surface.drawn_revision = surface.pile.revision
            if surface.shape:
                yield surface.shape

    def bank_at(self, x: float, y: float, margin: float = 1.25, *, hwnd: int | None = None,
                visible: Visibility | None = None) -> bool:
        """Hit-test the rendered snow, with room for antialiased edge pixels."""
        return self.bank_owner_at(x, y, margin, hwnd=hwnd, visible=visible) is not None

    def bank_owner_at(self, x: float, y: float, margin: float = 1.25, *,
                      hwnd: int | None = None, visible: Visibility | None = None) -> int | None:
        """Return the frontmost snow owner hit at a point. Ground is owner zero."""
        ground_rank = len(self._window_rank)
        best_rank = ground_rank + 1
        owner = None
        for polygon in self.drifts():
            candidate = polygon.hwnd
            if hwnd is not None and candidate != hwnd:
                continue
            if not polygon.left <= x < polygon.right or not polygon.top - margin <= y <= polygon.bottom + margin:
                continue
            rank = self.owner_rank(candidate)
            if rank >= best_rank:
                continue
            if polygon.curved:
                crossings = []
                x1, y1 = polygon[-1]
                for x2, y2 in polygon:
                    if x1 <= x < x2 or x2 <= x < x1:
                        crossings.append(y1 + (y2 - y1) * (x - x1) / (x2 - x1))
                    x1, y1 = x2, y2
                crossings.sort()
                hit = any(bottom - top > 0.2 and top - margin <= y <= bottom + margin
                          for top, bottom in zip(crossings[::2], crossings[1::2]))
            else:
                index = bisect_right(polygon, (x, float("inf")))
                x1, y1 = polygon[index - 1]
                x2, y2 = polygon[index]
                top = y1 + (y2 - y1) * (x - x1) / (x2 - x1)
                hit = polygon.bottom - top > 0.2 and y + margin >= top
            if hit and (visible is None or visible(candidate, x, y)):
                if hwnd is not None or rank == 0:
                    return candidate
                owner, best_rank = candidate, rank
        return owner

    @staticmethod
    def _drift_polygon(surface: _Surface, current: DepthSnapshot | None = None,
                       previous: DepthSnapshot | None = None) -> DriftPolygon | None:
        if current is None:
            if previous is None:
                # A standalone outline does not change the live surface cache.
                current = surface.visible_depths()
                if current is not None:
                    return _BankOutline(surface, current[0], current[1]).update(surface, current, None)
                return None
            surface.outline = None
            return None
        if surface.outline is None or previous is None or current[:3] != previous[:3]:
            surface.outline = _BankOutline(surface, current[0], current[1])
            previous = None
        return surface.outline.update(surface, current, previous)
