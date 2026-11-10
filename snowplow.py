"""Automatically clear a desktop floor with one passing snowplow."""

from dataclasses import dataclass, field
from math import asin, hypot, radians, sqrt

from simulation import Rect


POLL_INTERVAL = 0.5
SUSTAIN_TIME = 5.0
MIN_DEPTH = 12.0
MIN_COVERAGE = 0.60
COOLDOWN = 120.0
PLOW_SPEED = 150.0
PLOW_SCALE = 1.5
VEHICLE_LENGTH = int(126 * PLOW_SCALE)
VEHICLE_HEIGHT = int(64 * PLOW_SCALE)
CONTACT_INSET = 8 * PLOW_SCALE
MAX_CHIPS = 18
PANEL_GAP = 2.0
LIFT_SPEED = 480.0
DROP_SPEED = 180.0
REAR_AXLE = (126 - 24) * PLOW_SCALE
MAX_PITCH = radians(22)
# The visible front contour of the scoop in sprite coordinates.
SCOOP_EDGE = ((121, 38), (123, 39), (120, 43), (118, 49), (119, 54),
              (122, 59), (126, 63), (125, 64))
SCOOP_HEIGHT = (64 - SCOOP_EDGE[0][1]) * PLOW_SCALE
_SCOOP_SEGMENTS = tuple(
    ((x1 - 126) * PLOW_SCALE, (y1 - 64) * PLOW_SCALE,
     (y2 - 64) * PLOW_SCALE, (x2 - x1) / (y2 - y1))
    for (x1, y1), (x2, y2) in zip(SCOOP_EDGE, SCOOP_EDGE[1:])
)


@dataclass(slots=True)
class _Route:
    screen: Rect
    floor: Rect
    blade_x: float
    next_spray_x: float
    collected: float = 0.0
    lift: float = 0.0
    rear_lift: float = 0.0
    cargo_lift: float = 0.0
    pushed: list[tuple[float, ...]] = field(default_factory=list)

    @property
    def baseline(self) -> float:
        return self.floor[3] - self.lift

    @property
    def cargo_baseline(self) -> float:
        return self.floor[3] - self.cargo_lift


def _follow_height(current, target, elapsed):
    return min(current + LIFT_SPEED * elapsed, max(target, current - DROP_SPEED * elapsed))


def _panel_lift(support, floor_y, overhang=0.0):
    rise = floor_y - support[1] if support is not None else 0.0
    return rise + min(PANEL_GAP, rise) + overhang if rise > 0 else 0.0


@dataclass(slots=True)
class _Chip:
    x: float
    y: float
    radius: float
    duration: float
    age: float = 0.0


class Snowplow:
    def __init__(self):
        self.clear()

    def clear(self) -> None:
        self._route: _Route | None = None
        self._chips: list[_Chip] = []
        self._qualified_since: dict[tuple[Rect, Rect], float] = {}
        self._elapsed = self._next_poll = self._cooldown_until = 0.0

    def step(self, simulation, snowmen, dt: float, *, paused=False, visible=None) -> None:
        if paused:
            self._qualified_since.clear()
            return
        if dt <= 0:
            return
        self._elapsed += dt
        if self._route is not None:
            self._advance(simulation, snowmen, dt, visible)
        elif self._elapsed + 1e-9 >= max(self._next_poll, self._cooldown_until):
            self._poll(simulation)

    def _poll(self, simulation) -> None:
        self._next_poll = self._elapsed + POLL_INTERVAL
        qualifying = {}
        for screen, floor in simulation.desktop_floors():
            if simulation.floor_snow_coverage(screen, MIN_DEPTH) < MIN_COVERAGE:
                continue
            key = screen, floor
            since = self._qualified_since.get(key, self._elapsed)
            if self._elapsed - since + 1e-9 >= SUSTAIN_TIME:
                left = floor[0]
                self._route = _Route(screen, floor, left - 40.0, left)
                self._qualified_since.clear()
                return
            qualifying[key] = since
        self._qualified_since = qualifying

    def _finish(self) -> None:
        self._route = None
        self._chips.clear()
        self._cooldown_until = self._elapsed + COOLDOWN
        self._qualified_since.clear()

    def _advance(self, simulation, snowmen, dt: float, visible) -> None:
        route = self._route
        if (route.screen, route.floor) not in simulation.desktop_floors():
            self._finish()
            return
        for chip in self._chips:
            chip.age += dt
        self._chips[:] = [chip for chip in self._chips if chip.age < chip.duration]

        previous_tip = route.blade_x
        route.blade_x += PLOW_SPEED * dt
        previous = previous_tip - CONTACT_INSET
        contact = route.blade_x - CONTACT_INSET
        right, floor_y = route.floor[2:]
        front_support = simulation.taskbar_support(route.screen, contact)
        rear_support = simulation.taskbar_support(route.screen, route.blade_x - REAR_AXLE)
        target_lift = _panel_lift(front_support, floor_y)
        previous_lift = route.lift
        # Keep the lip on the ground until it reaches the live taskbar edge.
        # Splitting this step keeps the preceding ground cells in the sweep.
        if (front_support is not None and previous_lift == 0.0 and target_lift > 0.0
                and previous < front_support[0] <= contact):
            edge = front_support[0]
            self._sweep(simulation, snowmen, previous, edge, 0.0, 0.0, visible)
            previous = edge
        route.lift = _follow_height(previous_lift, target_lift, (contact - previous) / PLOW_SPEED)
        rear_target = _panel_lift(rear_support, floor_y)
        route.rear_lift = _follow_height(route.rear_lift, rear_target, dt)
        if route.pushed:
            front = overhang = 0.0
            for dx, dy, radius, *_ in route.pushed:
                front = max(front, dx + radius)
                overhang = max(overhang, dy)
            cargo_support = simulation.taskbar_support(route.screen, route.blade_x + front)
            cargo_target = _panel_lift(cargo_support, floor_y, overhang)
            route.cargo_lift = max(route.lift, _follow_height(route.cargo_lift, cargo_target, dt))
        else:
            route.cargo_lift = route.lift
        self.capture_held(snowmen, previous_tip, visible=visible)
        end = min(right, contact)
        if previous < end:
            end_lift = previous_lift + (route.lift - previous_lift) * (end - previous) / (contact - previous)
            self._sweep(simulation, snowmen, previous, end, previous_lift, end_lift, visible)
        if route.blade_x >= right + max(VEHICLE_LENGTH, CONTACT_INSET):
            self._finish()

    def _sweep(self, simulation, snowmen, left, right, previous_lift, lift, visible) -> None:
        route = self._route
        removed = simulation.sweep_desktop_floor(
            route.screen, left, right, blade_lift=lift, previous_lift=previous_lift,
            panel_gap=PANEL_GAP, blade_height=SCOOP_HEIGHT, visible=visible,
        )
        route.collected += removed
        if removed and right >= route.next_spray_x:
            self._burst(right, route.floor[3] - lift - 10 * PLOW_SCALE, 9, count=2)
            route.next_spray_x = right + 64
        for x, y, radius, volume in snowmen.collect_desktop_figures(
                simulation, route.screen, left, right, blade_lift=lift,
                panel_gap=PANEL_GAP, visible=visible):
            route.collected += volume
            self._burst(x, y, radius, count=4)

    def _burst(self, x, y, radius, *, count) -> None:
        room = min(count, MAX_CHIPS - len(self._chips))
        for index in range(room):
            self._chips.append(_Chip(
                x + (index - 1) * radius * .28,
                y - index * radius * .24,
                min(5.5, max(2.0, radius * (.19 + index * .025))),
                .27 + .055 * index,
            ))

    def capture_held(self, snowmen, previous=None, *, visible=None) -> None:
        """Transfer a held ball or stack only when it touches the moving scoop."""
        route = self._route
        if route is None:
            return
        held = snowmen.drag_scene()
        if not held:
            return
        x, bottom, *radii = held[0]
        current = route.blade_x
        previous = current if previous is None else previous
        if x - radii[0] > current or x + radii[0] < previous - CONTACT_INSET:
            return
        left, top, right, floor_y = route.floor
        baseline = route.baseline
        first_contact = None
        y = bottom - baseline
        for radius in radii:
            center_y = y - radius
            for offset, y1, y2, slope in _SCOOP_SEGMENTS:
                if center_y + radius < y1 or center_y - radius > y2:
                    continue
                # Slide this segment right to its first tangent with the circle.
                tangent_y = min(y2, max(y1, center_y + radius * slope / hypot(1, slope)))
                reach = sqrt(max(0.0, radius * radius - (tangent_y - center_y) ** 2))
                edge = offset + slope * (tangent_y - y1)
                first = x - reach - edge
                if first > current or (first_contact is not None and first >= first_contact):
                    continue
                contact_x, contact_y = x - reach, tangent_y
                if first < previous:
                    # The pointer may have moved into the scoop between plow frames.
                    # Check that the nearest point is still inside the circle.
                    relative_x = x - previous - offset
                    projection = (center_y - y1 + slope * relative_x) / (1 + slope * slope)
                    projection = min(y2 - y1, max(0.0, projection))
                    contact_y = y1 + projection
                    contact_x = previous + offset + slope * projection
                    if (contact_x - x) ** 2 + (contact_y - center_y) ** 2 > radius * radius:
                        continue
                contact_y += baseline
                if (left <= contact_x < right and top <= contact_y < floor_y
                        and (visible is None or visible(0, contact_x, contact_y))):
                    first_contact = first
            y -= radius * 1.8
        if first_contact is not None:
            route.pushed.append((x - first_contact, bottom - route.cargo_baseline, *radii))
            route.collected += snowmen.release_to_plow()

    def pushed_scene(self, rect: Rect):
        route = self._route
        if route is None or route.screen != rect:
            return ()
        blade_x, baseline = route.blade_x, route.cargo_baseline
        return tuple((blade_x + dx, baseline + dy, *radii) for dx, dy, *radii in route.pushed)

    def pitch(self, rect: Rect) -> float:
        route = self._route
        if route is None or route.screen != rect:
            return 0.0
        rise = (route.rear_lift - route.lift) / REAR_AXLE
        return max(-MAX_PITCH, min(MAX_PITCH, asin(max(-1.0, min(1.0, rise)))))

    def scene(self, rect: Rect):
        route = self._route
        if route is None or route.screen != rect:
            return None
        blade_x = route.blade_x
        left, _, right, _ = route.floor
        baseline = route.baseline
        fill = min(1.0, route.collected / max(1.0, (right - left) * MIN_DEPTH))
        hopper_x, hopper_y = blade_x - 98 * PLOW_SCALE, baseline - 43 * PLOW_SCALE
        chips = []
        for chip in self._chips:
            t = chip.age / chip.duration
            x = chip.x + (hopper_x - chip.x) * t
            y = chip.y + (hopper_y - chip.y) * t - 96 * PLOW_SCALE * t * (1 - t)
            chips.append((x, y, chip.radius * (1 - .55 * t)))
        return blade_x, baseline, fill, tuple(chips)
