"""Roll variable-sized snowballs and stack them with explicit mouse drags."""

from dataclasses import dataclass, field
from functools import lru_cache
from math import ceil, hypot, sqrt

from simulation import ImpactPose, MAX_FLAKES

MAX_RADIUS = 36.0
MAX_VOLUME = MAX_RADIUS ** 2
MAX_STACKS = 24
STACK_RADII = (1.0, 0.73, 0.53, 0.40, 0.30)
STACK_HEIGHTS = tuple(1.8 * sum(STACK_RADII[:index]) + 2 * ratio
                      for index, ratio in enumerate(STACK_RADII))
MAX_LAYERS = len(STACK_RADII)
MAX_BALLS = MAX_STACKS * MAX_LAYERS
SUPPORT_REACH = 52
FIGURE_PLACE_REACH = 6
PICKUP_VOLUME = 12.0
GRAVITY = 760.0
SHATTER_SPEED = 745.0
ROLL_FRICTION = 145.0
SETTLE_SPEED = 11.0
FLICK_WINDOW = 0.12
FLICK_RELEASE_GRACE = 0.065
FLICK_MIN_TRAVEL = 24.0
FLICK_MIN_SPEED = 520.0
THROW_MAX_SPEED = 1050.0
THROW_SHATTER_SPEED = 415.0
FLYING_BALL_LAYER = -1


def _radius(volume):
    return max(2.25, sqrt(volume))


@lru_cache(maxsize=256)
def _stack_radii(volume, layers):
    base = _radius(volume)
    return tuple(base * ratio for ratio in STACK_RADII[:layers])


def _swept_x_bounds(x, radius, travel, direction):
    # Every crafted ball is bounded by MAX_RADIUS. Reject distant pairs before
    # calculating their individual radii and exact circle contact.
    limit = x + direction * (travel + radius + MAX_RADIUS)
    return (x, limit) if direction > 0 else (limit, x)


def _swept_circle(dx, dy, separation_x, separation_y, radius):
    approach = dx * separation_x + dy * separation_y
    if approach <= 0:
        return None
    gap = separation_x ** 2 + separation_y ** 2 - radius ** 2
    if gap <= 0:
        return 0.0
    discriminant = approach ** 2 - (dx ** 2 + dy ** 2) * gap
    if discriminant < 0:
        return None
    return gap / (approach + sqrt(discriminant))


@dataclass(slots=True, eq=False)
class Snowman:
    hwnd: int
    x: float
    y: float
    window: tuple[int, int, int, int] | None
    volumes: list[float]

    @property
    def radii(self):
        return _stack_radii(self.volumes[0], len(self.volumes))

    @property
    def crown(self):
        return self.y - _radius(self.volumes[0]) * STACK_HEIGHTS[len(self.volumes) - 1]

    @property
    def top_radius(self):
        return _radius(self.volumes[0]) * STACK_RADII[len(self.volumes) - 1]

    def update(self, snow, positions):
        if self.window is None:
            ground = snow.ground_y_at(self.x, self.y)
            if ground is None:
                return 0, 0
            self.y = ground
            return None
        current = positions.get(self.hwnd)
        if current is None:
            return 0, 0
        left, top, right, _ = current
        dx, dy = left - self.window[0], top - self.window[1]
        if abs(dx) >= 16 or abs(dy) >= 10 or not left <= self.x < right:
            return dx, dy
        self.y = top
        return None

@dataclass(slots=True, eq=False)
class _MovingBall:
    x: float
    y: float
    volume: float
    owner: int
    vx: float = 0.0
    vy: float = 0.0
    rolling: bool = False
    age: float = 0.0
    thrown: bool = False
    radius: float = field(init=False)

    def __post_init__(self):
        self.radius = _radius(self.volume)


@dataclass(slots=True, eq=False)
class _FlyingFigure:
    figure: Snowman
    vx: float
    vy: float
    thrown: bool = False


@dataclass(slots=True)
class _Impact:
    target: Snowman | _MovingBall | _FlyingFigure
    time: float
    x: float
    y: float
    nx: float
    ny: float
    speed: float
    owner: int
    hard: bool
    poses: tuple[ImpactPose, ImpactPose]


def _impact_radii(body):
    if isinstance(body, _MovingBall):
        return (body.radius,)
    figure = body.figure if isinstance(body, _FlyingFigure) else body
    return figure.radii


def _impact_layers(body, baseline):
    for radius in _impact_radii(body):
        yield baseline - radius, radius
        baseline -= 1.8 * radius


class Snowmen:
    def __init__(self):
        self.enabled = False
        self.clear()

    def clear(self):
        self.items = []
        self.moving = []
        self.flying = []
        self.held = 0.0
        self.held_figure = None
        self._figure_offset = None
        self.last_ledge = None
        self._window_snapshot = ()
        self._positions = {}
        self._drag_dt = 1 / 60
        self.cancel_drag()

    @property
    def dragging(self):
        return self.pointer is not None

    @property
    def whole_dragging(self):
        return self.dragging and self.held_figure is not None

    @property
    def visible_flight(self):
        return bool(self.flying) or any(ball.thrown for ball in self.moving)

    @property
    def loose_radius(self):
        return _radius(self.held)

    def step(self, snow, windows, dt=1 / 60, *, visible=None):
        if not self.enabled:
            self.cancel_drag()
        dt = min(0.1, max(0.0, dt))
        if dt:
            self._drag_dt = min(1 / 30, max(1 / 120, dt))
        if not (self.items or self.moving or self.flying or self.held_figure is not None) and (
                not self.held or self.dragging or self.last_ledge is None):
            return
        snapshot = tuple(windows)
        if snapshot != self._window_snapshot:
            self._window_snapshot = snapshot
            self._positions = {window[0]: window[1:] for window in snapshot}
        supported = []
        for figure in self.items:
            motion = figure.update(snow, self._positions)
            if motion is None:
                supported.append(figure)
            else:
                dx, dy = motion
                vx = min(130.0, max(-130.0, -dx * 4.5))
                vy = min(-25.0, max(-90.0, dy * 2.0 - 30.0))
                if len(figure.volumes) > 1:
                    self.flying.append(_FlyingFigure(figure, vx, vy))
                else:
                    self._detach(figure, vx, vy)
        self.items = supported
        if dt and (self.moving or self.flying):
            pieces = min(4, ceil(dt * 60))
            interval = dt / pieces
            for _ in range(pieces):
                if self.moving or self.flying:
                    self._advance(snow, interval, visible)
        if self.held_figure is not None and not self.dragging:
            self._restore_figure(snow, self._positions)
        if self.held and not self.dragging:
            self._park(snow, self._positions)

    def _detach(self, figure, vx, vy, *, rolling=False):
        ball = _MovingBall(figure.x, figure.y, figure.volumes[0], figure.hwnd,
                           vx, 0.0 if rolling else vy, rolling)
        self.moving.append(ball)
        return ball

    @staticmethod
    def _contact_distance(x, center_y, radius, other_x, other_y, other_radius, direction, travel):
        gap = (other_x - x) * direction
        reach = radius + other_radius
        if gap <= 0 or gap > travel + reach:
            return None
        dy = other_y - center_y
        if abs(dy) >= reach:
            return None
        return max(0.0, gap - sqrt(reach * reach - dy * dy))

    def _rolling_hit(self, ball, dx):
        direction = 1 if dx > 0 else -1
        nearest, distance = None, abs(dx)
        center_y = ball.y - ball.radius
        left, right = _swept_x_bounds(ball.x, ball.radius, distance, direction)
        for figure in self.items:
            if figure.hwnd != ball.owner or not left <= figure.x <= right:
                continue
            radius = _radius(figure.volumes[0])
            contact = self._contact_distance(ball.x, center_y, ball.radius, figure.x,
                                             figure.y - radius, radius, direction, distance)
            if contact is not None and contact <= distance:
                nearest, distance = figure, contact
        for other in self.moving:
            if (not left <= other.x <= right or other is ball or not other.rolling or other.owner != ball.owner
                    or (ball.vx - other.vx) * direction <= 0):
                continue
            contact = self._contact_distance(ball.x, center_y, ball.radius, other.x,
                                             other.y - other.radius, other.radius, direction, distance)
            if contact is not None and contact <= distance:
                nearest, distance = other, contact
        return nearest, direction * distance

    def _advance(self, snow, dt, visible):
        balls, flights = tuple(self.moving), tuple(self.flying)
        motion = None
        if flights or any(ball.thrown or abs(ball.vx) + abs(ball.vy) >= THROW_SHATTER_SPEED / 2
                          for ball in balls):
            # Take one snapshot so a moving target's path is independent of update order.
            motion = {ball: (ball.x, ball.y, ball.vx, ball.vy, ball.rolling) for ball in balls}
            motion.update((flight, (flight.figure.x, flight.figure.y, flight.vx, flight.vy, False))
                          for flight in flights)
        broken = set()
        for ball in balls:
            if ball in broken:
                continue
            ball.age += dt
            removed = (self._roll(snow, ball, dt, broken, visible) if ball.rolling
                       else self._fall(snow, ball, dt, motion, broken, visible))
            if removed:
                self.moving.remove(ball)
                continue
            if ball.rolling and abs(ball.vx) < SETTLE_SPEED:
                if self._settle(snow, ball):
                    self.moving.remove(ball)
                    continue
                ball.vx = 26.0 if ball.vx >= 0 else -26.0
            if (not ball.rolling and len(snow.particles) < MAX_FLAKES
                    and snow.ball_outside(ball.x, ball.y, ball.radius)):
                snow.release_ball(ball.x, ball.y - ball.radius, ball.radius,
                                  ball.vx, ball.vy, volume=ball.volume)
                self.moving.remove(ball)
        for flight in flights:
            if flight not in broken and self._advance_figure(snow, flight, dt, motion, broken, visible):
                self.flying.remove(flight)

    def _roll(self, snow, ball, dt, broken, visible):
        if not ball.vx:
            return
        if ball.owner == 0:
            ground = snow.ground_y_at(ball.x, ball.y)
            if ground is not None:
                ball.y = ground
        direction = 1 if ball.vx > 0 else -1
        speed = abs(ball.vx)
        slowed = max(0.0, speed - ROLL_FRICTION * dt)
        travel_time = min(dt, speed / ROLL_FRICTION)
        target, dx = self._rolling_hit(ball, direction * (speed + slowed) * travel_time / 2)
        ball.vx = direction * slowed
        if target is not None:
            target_vx = target.vx if isinstance(target, _MovingBall) else 0.0
            relative = ball.vx - target_vx
            contact_x = ball.x + dx + direction * ball.radius
            if abs(relative) >= THROW_SHATTER_SPEED and (
                    visible is None or visible(ball.owner, contact_x, ball.y - ball.radius)):
                poses = ((ball.x + dx, ball.y, ball.radius),
                         (target.x, target.y, *_impact_radii(target)))
                if self._merge_impact(snow, contact_x, ball.y - ball.radius,
                                      ball.volume, target, broken, poses):
                    return True
            if isinstance(target, Snowman) and len(target.volumes) > 1:
                ball.vx *= -0.45
                ball.x += dx
                return False
            if isinstance(target, Snowman):
                self.items.remove(target)
                target = self._detach(target, 0.0, -24.0, rolling=True)
            impulse = 1.22 * relative / (ball.volume + target.volume)
            ball.vx -= impulse * target.volume
            target.vx += impulse * ball.volume
        x = ball.x + dx
        support = snow.support_at(x, ball.y, 10, hwnd=ball.owner)
        if support is not None:
            ball.x, ball.y = x, support[3]
        elif ball.owner == 0 and (raised := snow.ground_y_at(x, ball.y)) is not None and raised < ball.y:
            ball.vx *= -0.45
        else:
            ball.x, ball.rolling, ball.vy = x, False, 0.0

    def _fall(self, snow, ball, dt, motion, broken, visible):
        x = ball.x + ball.vx * dt
        y = ball.y + (ball.vy + 0.5 * GRAVITY * dt) * dt
        landing = snow.ball_landing(ball.x, ball.y, x, y)
        if motion is not None:
            limit = (landing.y - ball.y) / (y - ball.y) if landing is not None else 1.0
            hit = self._impact_contact(snow, ball, ball.x, ball.y, x, y, dt, limit,
                                       motion, visible, hard_only=not ball.thrown)
            if hit is not None:
                if (hit.hard and self._merge_impact(
                        snow, hit.x, hit.y, ball.volume, hit.target, broken, hit.poses)):
                    return True
                if ball.thrown:
                    self._bounce_ball(ball, x, y, dt, hit)
                    return False
        if landing is not None:
            owner, impact_x, impact_y = landing
            # Use the velocity at first contact, before the end of the frame.
            speed_squared = ball.vy ** 2 + 2 * GRAVITY * (impact_y - ball.y)
            if (speed_squared >= SHATTER_SPEED ** 2
                    and snow.deposit_impact_mound(impact_x, impact_y, ball.volume, hwnd=owner,
                                                   poses=((impact_x, impact_y, ball.radius),))):
                return True
            ball.owner, ball.x, ball.y = owner, impact_x, impact_y
            ball.vx *= 0.73
            ball.vy, ball.rolling = 0.0, True
            ball.thrown = False
        else:
            ball.x, ball.y = x, y
            ball.vy = min(880.0, ball.vy + GRAVITY * dt)
            ball.vx *= 1 - 0.16 * dt
        return False

    def _advance_figure(self, snow, flight, dt, motion, broken, visible):
        figure = flight.figure
        radius = figure.radii[0]
        x = figure.x + flight.vx * dt
        y = figure.y + (flight.vy + 0.5 * GRAVITY * dt) * dt
        landing = snow.ball_landing(figure.x, figure.y, x, y)
        limit = (landing.y - figure.y) / (y - figure.y) if landing is not None else 1.0
        hit = self._impact_contact(snow, flight, figure.x, figure.y, x, y, dt, limit,
                                   motion, visible, hard_only=True)
        volume = sum(figure.volumes)
        if hit is not None and self._merge_impact(snow, hit.x, hit.y, volume, hit.target, broken, hit.poses):
            return True
        if landing is not None:
            owner, impact_x, impact_y = landing
            speed_squared = flight.vy ** 2 + 2 * GRAVITY * (impact_y - figure.y)
            support = snow.support_at(impact_x, impact_y, 2, hwnd=owner)
            if speed_squared < SHATTER_SPEED ** 2 and support is not None:
                location = self._free_position(impact_x, impact_y, support[1], support[2], radius, owner)
                if location is not None and abs(location - impact_x) <= radius:
                    baseline = snow.ground_y_at(location, impact_y) if owner == 0 else impact_y
                    figure.hwnd, figure.x = owner, location
                    figure.y, figure.window = baseline, self._positions.get(owner)
                    self.items.append(figure)
                    return True
            if snow.deposit_impact_mound(impact_x, impact_y, volume, hwnd=owner,
                                         poses=((impact_x, impact_y, *figure.radii),)):
                return True
        figure.x, figure.y = x, y
        flight.vy = min(880.0, flight.vy + GRAVITY * dt)
        flight.vx *= 1 - 0.16 * dt
        return snow.ball_outside(x, figure.crown + 2 * radius, radius)

    def _impact_contact(self, snow, body, old_x, old_y, x, y, dt, limit, motion, visible, *, hard_only):
        dx, dy = x - old_x, y - old_y
        layers = tuple(_impact_layers(body, old_y))
        margin = layers[0][1] + MAX_RADIUS
        left, right = min(old_x, x) - margin, max(old_x, x) + margin
        top = layers[-1][0] - layers[-1][1] + min(0.0, dy) - MAX_RADIUS
        bottom = old_y + max(0.0, dy) + MAX_RADIUS
        nearest = None

        def consider(target, owner, tx, ty, vx, vy, rolling, airborne):
            nonlocal nearest, limit
            drift_x = vx * dt
            if min(tx, tx + drift_x) > right or max(tx, tx + drift_x) < left:
                return
            drift_y = 0.0 if rolling else (vy + 0.5 * GRAVITY * dt) * dt
            target_limit = limit
            if drift_y > 0:
                landing = snow.ball_landing(tx, ty, tx + drift_x, ty + drift_y)
                if landing is not None:
                    target_limit = min(target_limit, (landing.y - ty) / drift_y)
            for target_y, target_radius in _impact_layers(target, ty):
                if min(target_y, target_y + drift_y) > bottom or max(target_y, target_y + drift_y) < top:
                    continue
                for center_y, radius in layers:
                    time = _swept_circle(dx - drift_x, dy - drift_y, tx - old_x,
                                         target_y - center_y, radius + target_radius)
                    if time is None or time > min(limit, target_limit):
                        continue
                    if nearest is not None and time == limit and snow.owner_rank(owner) >= snow.owner_rank(nearest.owner):
                        continue
                    nx = tx - old_x + (drift_x - dx) * time
                    ny = target_y - center_y + (drift_y - dy) * time
                    distance = hypot(nx, ny)
                    if not distance:
                        continue
                    nx, ny = nx / distance, ny / distance
                    gravity = GRAVITY * dt * time
                    source_vy = min(880.0, body.vy + gravity)
                    target_vy = vy if rolling else min(880.0, vy + gravity)
                    speed = (body.vx - vx) * nx + (source_vy - target_vy) * ny
                    hard = (speed >= THROW_SHATTER_SPEED or body.thrown
                            or getattr(target, "thrown", False))
                    if speed <= 0 or (hard_only and not hard):
                        continue
                    cx = old_x + dx * time + radius * nx
                    cy = center_y + dy * time + radius * ny
                    if not airborne and visible is not None and not visible(owner, cx, cy):
                        continue
                    limit = time
                    poses = ((old_x + dx * time, old_y + dy * time, *_impact_radii(body)),
                             (tx + drift_x * time, ty + drift_y * time, *_impact_radii(target)))
                    nearest = _Impact(target, time, cx, cy, nx, ny, speed, owner, hard, poses)

        for figure in self.items:
            if left <= figure.x <= right and figure.y >= top and figure.crown <= bottom:
                consider(figure, figure.hwnd, figure.x, figure.y, 0.0, 0.0, True, False)
        for target in self.moving:
            if target is body:
                continue
            state = motion.get(target)
            if state is None:
                state = target.x, target.y, target.vx, target.vy, target.rolling
            owner = target.owner if target.owner in self._positions else 0
            consider(target, owner, *state, target.thrown)
        for target in self.flying:
            if target is not body:
                consider(target, 0, *motion[target], True)
        return nearest

    def _merge_impact(self, snow, x, y, volume, target, broken, poses):
        if isinstance(target, _MovingBall):
            objects, target_volume = self.moving, target.volume
        elif isinstance(target, _FlyingFigure):
            objects, target_volume = self.flying, sum(target.figure.volumes)
        else:
            objects, target_volume = self.items, sum(target.volumes)
        if not snow.deposit_impact_mound(x, y, volume + target_volume, poses=poses):
            return False
        objects.remove(target)
        broken.add(target)
        return True

    def _bounce_ball(self, ball, x, y, dt, hit):
        ball.x += (x - ball.x) * hit.time - hit.nx * 0.05
        ball.y += (y - ball.y) * hit.time - hit.ny * 0.05
        ball.vy = min(880.0, ball.vy + GRAVITY * dt * hit.time)
        target = hit.target
        if isinstance(target, Snowman) and len(target.volumes) > 1:
            ball.vx -= 1.22 * hit.speed * hit.nx
            ball.vy -= 1.22 * hit.speed * hit.ny
            return
        if isinstance(target, Snowman):
            self.items.remove(target)
            target = self._detach(target, 0.0, -24.0, rolling=True)
        mass = target.volume if isinstance(target, _MovingBall) else sum(target.figure.volumes)
        impulse = 1.22 * hit.speed / (ball.volume + mass)
        ball.vx -= impulse * mass * hit.nx
        ball.vy -= impulse * mass * hit.ny
        target.vx += impulse * ball.volume * hit.nx
        target.vy += impulse * ball.volume * hit.ny
        if isinstance(target, _MovingBall) and target.rolling:
            if target.vy < -SETTLE_SPEED:
                target.rolling = False
            else:
                target.vy = 0.0

    def _settle(self, snow, ball):
        support = snow.support_at(ball.x, ball.y, 2, hwnd=ball.owner)
        if support is None:
            ball.rolling = False
            return False
        owner, left, right, ledge = support
        radius = min(ball.radius, (right - left) / 3)
        x = self._free_position(ball.x, ledge, left, right, radius, owner, ignore=ball)
        if x is None:
            if ball.age < 4:
                return False
            x = ball.x
        if owner == 0:
            ledge = snow.ground_y_at(x, ledge)
        self.items.append(Snowman(owner, x, ledge, self._positions.get(owner), [ball.volume]))
        return True

    def _remember_ledge(self, snow, x, y, *, hwnd=None, visible=None):
        support = snow.support_at(x, y, SUPPORT_REACH, hwnd=hwnd, visible=visible, within_snow=True)
        if support is not None:
            self.last_ledge = (x, support[3], support[0])

    def begin_drag(self, snow, x, y, *, visible=None, now=None):
        if not self.enabled or self.dragging:
            return False
        if self.held_figure is not None and not self._restore_figure(snow, self._positions, visible):
            return False
        bank = snow.bank_owner_at(x, y, visible=visible)
        figure, whole = self._grab_at(snow, x, y, visible)
        if figure is not None and (bank is None or snow.owner_rank(figure.hwnd) <= snow.owner_rank(bank)):
            if whole:
                self.items.remove(figure)
                self.held_figure = figure
                self._figure_offset = figure.x - x, figure.y - y
            else:
                self._take_top(figure)
        elif bank is not None:
            self._remember_ledge(snow, x, y, hwnd=bank, visible=visible)
            self._harvest(snow, x, y, brush=10.0, quota=PICKUP_VOLUME, hwnd=bank, visible=visible)
        else:
            return False
        if now is not None:
            self._flick_samples = [(now, x, y)]
        self.pointer = self.last_sample = (x, y)
        return True

    def _grab_at(self, snow, x, y, visible):
        if self.held:
            return None, False
        best = None
        whole = False
        for figure in reversed(self.items):
            radius = figure.top_radius
            head_hit = hypot(x - figure.x, y - figure.crown - radius) <= radius + 1
            base = _radius(figure.volumes[0])
            base_hit = len(figure.volumes) > 1 and hypot(x - figure.x, y - figure.y + base) <= base + 1
            if ((head_hit or base_hit) and (visible is None or visible(figure.hwnd, x, y))
                    and (best is None or snow.owner_rank(figure.hwnd) < snow.owner_rank(best.hwnd))):
                best = figure
                whole = base_hit and not head_hit
        return best, whole

    def _take_top(self, figure):
        self.last_ledge = (figure.x, figure.y, figure.hwnd)
        self.held = figure.volumes.pop()
        if not figure.volumes:
            self.items.remove(figure)

    def _harvest(self, snow, x, y, brush, quota, *, hwnd=None, visible=None):
        remaining = MAX_VOLUME - self.held
        if remaining <= 0:
            return
        self.held += snow.collect(x, y - brush, brush, maximum=min(quota, remaining),
                                  reach=SUPPORT_REACH + min(24.0, self.loose_radius * 0.7),
                                  above=8, hwnd=hwnd, visible=visible)

    def move_drag(self, snow, x, y, *, visible=None, now=None):
        if not self.enabled or not self.dragging:
            return
        previous = self.pointer
        samples = self._flick_samples
        if previous == (x, y):
            if samples is not None and now is not None:
                self._flick_rest = now
            return
        self.pointer = (x, y)
        if samples is not None and now is not None and now > samples[-1][0]:
            if self._flick_rest is not None and samples[-1][0] < self._flick_rest < now:
                samples.append((self._flick_rest, *previous))
            samples.append((now, x, y))
            if len(samples) > 9:
                del samples[:-9]
        self._flick_rest = None
        if self.held_figure is not None:
            return
        self._remember_ledge(snow, x, y, visible=visible)
        start_x, start_y = self.last_sample
        dx, dy = x - start_x, y - start_y
        distance = hypot(dx, dy)
        if distance < 2:
            return
        self.last_sample = self.pointer
        if not dx:
            return
        if self.held < MAX_VOLUME:
            samples = min(12, ceil(distance / 9))
            spacing = min(abs(dx), 108.0) / samples
            for index in range(1, samples + 1):
                sample_x = start_x + dx * index / samples
                sample_y = start_y + dy * index / samples
                radius = self.loose_radius
                # Concentrate the scoop under the ball so the path visibly thins.
                brush = min(27.0, max(11.0, radius * 0.8))
                self._harvest(snow, sample_x, sample_y, brush,
                              quota=spacing * max(7.0, radius * 0.55), visible=visible)
                if self.held >= MAX_VOLUME:
                    break
        if self.held and (self.items or self.moving) and abs(dy) < max(14, abs(dx) * 0.6):
            self._push_from_drag(snow, start_x, start_y, x, y, visible)

    def _push_from_drag(self, snow, x0, y0, x1, y1, visible):
        support = snow.support_at(x1, y1, SUPPORT_REACH, visible=visible)
        if support is None:
            support = snow.support_at(x0, y0, SUPPORT_REACH, visible=visible)
        if support is None:
            return
        owner = support[0]
        radius = self.loose_radius
        center_y = (y0 + y1) * 0.5 - min(8.0, radius / 2)
        direction = 1 if x1 > x0 else -1
        nearest, distance = None, abs(x1 - x0)
        left, right = _swept_x_bounds(x0, radius, distance, direction)
        for figure in self.items:
            if figure.hwnd != owner or not left <= figure.x <= right:
                continue
            base = _radius(figure.volumes[0])
            contact = self._contact_distance(x0, center_y, radius, figure.x,
                                             figure.y - base, base, direction, distance)
            if contact is not None and contact <= distance:
                nearest, distance = figure, contact
        for other in self.moving:
            if not left <= other.x <= right or not other.rolling or other.owner != owner:
                continue
            contact = self._contact_distance(x0, center_y, radius, other.x,
                                             other.y - other.radius, other.radius, direction, distance)
            if contact is not None and contact <= distance:
                nearest, distance = other, contact
        if nearest is None or (visible is not None and not visible(owner, nearest.x, center_y)):
            return
        mass = nearest.volumes[0] if isinstance(nearest, Snowman) else nearest.volume
        push = min(260.0, max(34.0, abs(x1 - x0) / self._drag_dt * 0.65))
        velocity = direction * push * (0.5 + 0.5 * min(1.0, self.held / mass))
        if isinstance(nearest, Snowman):
            self.items.remove(nearest)
            if len(nearest.volumes) > 1:
                self.flying.append(_FlyingFigure(nearest, velocity, -24.0))
            else:
                self._detach(nearest, velocity, -24.0, rolling=True)
        elif nearest.vx * direction < abs(velocity):
            nearest.vx = velocity

    def end_drag(self, snow, windows, x, y, *, allow_drop=True, visible=None, now=None):
        if not self.dragging:
            return
        positions = {window[0]: window[1:] for window in windows}
        if self.held_figure is not None:
            if allow_drop and self.enabled:
                self.move_drag(snow, x, y, visible=visible, now=now)
                base_x, baseline = self._figure_anchor(x, y)
                self._drop_figure(snow, positions, base_x, baseline, y, visible)
                if self.held_figure is not None:
                    figure = self.held_figure
                    self.held_figure = None
                    ground = snow.ground_y_at(base_x, baseline)
                    figure.x = base_x
                    figure.y = min(baseline, ground) if ground is not None else baseline
                    velocity = self._flick_velocity(now)
                    vx, vy = velocity or (0.0, 0.0)
                    self.flying.append(_FlyingFigure(figure, vx, vy, thrown=velocity is not None))
            self.cancel_drag()
            if self.held_figure is not None:
                self._restore_figure(snow, positions, visible)
            return
        if allow_drop and self.enabled:
            self.move_drag(snow, x, y, visible=visible, now=now)
            if self.held:
                placed = self._place(snow, positions, x, y, visible)
                if not placed:
                    radius = self.loose_radius
                    baseline = y + radius - min(8.0, radius / 2)
                    owner = self.drag_owner(snow, visible)
                    velocity = self._flick_velocity(now)
                    if (velocity is not None and self._ball_count() < MAX_BALLS
                            and not snow.ball_outside(x, baseline, radius)):
                        self.moving.append(_MovingBall(x, baseline, self.held, owner, *velocity, thrown=True))
                        placed = True
                    else:
                        placed = self._launch_drop(snow, x, baseline, owner)
                if placed:
                    self.held = 0.0
                    self.last_ledge = None
        self.cancel_drag()
        if self.held:
            self._park(snow, positions)

    def cancel_drag(self):
        self.pointer = self.last_sample = None
        self._figure_offset = None
        self._flick_samples = None
        self._flick_rest = None

    def release_to_plow(self):
        volume = self.held if self.held_figure is None else sum(self.held_figure.volumes)
        self.held = 0.0
        self.held_figure = None
        self.last_ledge = None
        self.cancel_drag()
        return volume

    def _flick_velocity(self, now):
        samples = self._flick_samples
        if now is None or not samples or len(samples) < 2:
            return None
        last, x, y = samples[-1]
        if now - last > FLICK_RELEASE_GRACE:
            return None
        _, previous_x, previous_y = samples[-2]
        for start, start_x, start_y in reversed(samples[:-1]):
            duration = last - start
            if duration > FLICK_WINDOW:
                break
            # Release polling can observe a real flick less than 12 ms after the last sample.
            duration = max(duration, 0.012)
            dx, dy = x - start_x, y - start_y
            if dx * (x - previous_x) + dy * (y - previous_y) <= 0:
                continue
            distance = hypot(dx, dy)
            if distance >= FLICK_MIN_TRAVEL and distance / duration >= FLICK_MIN_SPEED:
                factor = min(1 / duration, THROW_MAX_SPEED / distance)
                return dx * factor, dy * factor
        return None

    def _figure_anchor(self, x, y):
        dx, dy = self._figure_offset
        return x + dx, y + dy

    def _drop_figure(self, snow, positions, x, y, cursor_y, visible):
        radius = _radius(self.held_figure.volumes[0])
        # A base held from its centre may sit slightly through the ledge.
        # Release above the ledge otherwise requires actual proximity to it.
        reach = max(FIGURE_PLACE_REACH, y - cursor_y)
        support = snow.support_at(x, y, reach)
        if support is None:
            return
        owner, left, right, ledge = support
        if (ledge - y > FIGURE_PLACE_REACH or not left + radius <= x <= right - radius
                or (visible is not None and not visible(owner, x, ledge - 1))
                or not self._position_is_clear(x, ledge, radius, owner)):
            return
        self._put_figure(owner, x, ledge, positions)

    def _restore_figure(self, snow, positions, visible=None):
        figure = self.held_figure
        radius = _radius(figure.volumes[0])
        source_x, source_y = figure.x, figure.y
        window = positions.get(figure.hwnd)
        if figure.window is None:
            source_y = snow.ground_y_at(source_x, source_y)
        elif window is not None:
            source_x += window[0] - figure.window[0]
            source_y = window[1]
        support = None
        if source_y is not None and (figure.hwnd == 0 or window is not None):
            support = snow.support_at(source_x, source_y, 2, hwnd=figure.hwnd)
        if support is not None:
            slot = self._restoration_slot(snow, source_x, support, radius, visible)
            if slot is not None:
                self._put_figure(*slot, positions)
                return True
        best = None
        distance = float("inf")
        y = figure.y if source_y is None else source_y
        for span in snow.support_spans():
            slot = self._restoration_slot(snow, source_x, span, radius, visible)
            if slot is None:
                continue
            _, x, baseline = slot
            gap = (x - source_x) ** 2 + (baseline - y) ** 2
            if gap < distance:
                best, distance = slot, gap
        if best is None:
            return False
        self._put_figure(*best, positions)
        return True

    def _restoration_slot(self, snow, x, span, radius, visible):
        owner, left, right, y = span
        if right - left < 2 * radius:
            return None
        preferred = min(right - radius, max(left + radius, x))
        if owner == 0:
            y = snow.ground_y_at(preferred, y)
            if y is None:
                return None
        x = self._free_position(preferred, y, left, right, radius, owner)
        if x is None:
            return None
        if owner == 0:
            y = snow.ground_y_at(x, y)
            if y is None:
                return None
        if visible is not None and not visible(owner, x, y - 1):
            return None
        return owner, x, y

    def _put_figure(self, owner, x, y, positions):
        figure = self.held_figure
        self.held_figure = None
        figure.hwnd, figure.x, figure.y, figure.window = owner, x, y, positions.get(owner)
        self.items.append(figure)

    def _stack_at(self, snow, x, y, visible):
        radius = self.loose_radius
        candidates = []
        for figure in self.items:
            upper = figure.top_radius
            if len(figure.volumes) >= MAX_LAYERS or radius > upper + 0.75:
                continue
            crown = figure.crown
            if (abs(figure.x - x) > max(12, upper + radius * 0.75) or
                    not crown - 1.6 * radius - 12 <= y <= crown + upper + 3):
                continue
            if visible is not None and not visible(figure.hwnd, x, y):
                continue
            support = snow.support_at(figure.x, figure.y, 2, hwnd=figure.hwnd)
            if support is not None:
                priority = snow.owner_rank(figure.hwnd), hypot(figure.x - x, crown - radius - y)
                candidates.append((priority, figure))
        return min(candidates, key=lambda entry: entry[0], default=((), None))[1]

    def _place(self, snow, positions, x, y, visible):
        if self._ball_count() >= MAX_BALLS:
            return False
        figure = self._stack_at(snow, x, y, visible)
        if figure is not None:
            figure.volumes.append(self.held)
            return True
        support = snow.support_at(x, y, SUPPORT_REACH, visible=visible)
        if support is None or len(self.items) >= MAX_STACKS:
            return False
        hwnd, left, right, ledge = support
        radius = self.loose_radius
        if not left + radius <= x <= right - radius:
            return False
        if any(figure.hwnd == hwnd and abs(figure.y - ledge) < 8 and
               abs(figure.x - x) < _radius(figure.volumes[0]) + radius + 3
               for figure in self.items):
            return False
        if any(ball.rolling and ball.owner == hwnd and abs(ball.y - ledge) < 8
               and abs(ball.x - x) < ball.radius + radius + 3 for ball in self.moving):
            return False
        self.items.append(Snowman(hwnd, x, ledge, positions.get(hwnd), [self.held]))
        return True

    def _launch_drop(self, snow, x, baseline, owner):
        if (self._ball_count() >= MAX_BALLS or
                snow.ball_landing(x, baseline, x, baseline + 24000) is None):
            return False
        self.moving.append(_MovingBall(x, baseline, self.held, owner))
        return True

    def _ball_count(self):
        return (len(self.moving) + sum(len(figure.volumes) for figure in self.items)
                + sum(len(flight.figure.volumes) for flight in self.flying))

    def _park(self, snow, positions):
        if self.last_ledge is None:
            return
        x, prior_y, hwnd = self.last_ledge
        self.last_ledge = None
        window = positions.get(hwnd)
        ledge = (window[1] if window is not None else None) if hwnd else snow.ground_y_at(x, prior_y)
        support = snow.support_at(x, ledge, 2, hwnd=hwnd) if ledge is not None else None
        radius = self.loose_radius
        if (support is not None and len(self.items) < MAX_STACKS
                and self._ball_count() < MAX_BALLS):
            _, left, right, ledge = support
            location = self._free_position(x, ledge, left, right, radius, hwnd)
            if location is not None:
                if not hwnd:
                    ledge = snow.ground_y_at(location, ledge)
                self.items.append(Snowman(hwnd, location, ledge, window, [self.held]))
                self.held = 0.0
                return
        # Let an unsupported ball fall. If there is no ledge below, it leaves as loose snow.
        if not self._launch_drop(snow, x, prior_y - 2, hwnd):
            snow.release_ball(x, prior_y - radius - 2, radius, 0, 0, volume=self.held)
        self.held = 0.0

    def _position_is_clear(self, x, ledge, radius, hwnd, *, ignore=None):
        # Only nearby bodies can cover the requested position. Most landings
        # need no interval allocation or search for an alternative.
        distance = MAX_RADIUS + radius + 3
        left, right = x - distance, x + distance
        for figure in self.items:
            if figure.hwnd != hwnd or not left <= figure.x <= right or abs(figure.y - ledge) >= 8:
                continue
            reach = _radius(figure.volumes[0]) + radius + 3
            if figure.x - reach < x < figure.x + reach:
                return False
        for ball in self.moving:
            if (ball is ignore or not ball.rolling or ball.owner != hwnd
                    or not left <= ball.x <= right or abs(ball.y - ledge) >= 8):
                continue
            reach = ball.radius + radius + 3
            if ball.x - reach < x < ball.x + reach:
                return False
        return True

    def _free_position(self, x, ledge, left, right, radius, hwnd, *, ignore=None):
        minimum, maximum = left + radius, right - radius
        if minimum > maximum:
            return None
        desired = min(maximum, max(minimum, x))
        if self._position_is_clear(desired, ledge, radius, hwnd, ignore=ignore):
            return desired
        blocked = []
        for figure in self.items:
            if figure.hwnd == hwnd and abs(figure.y - ledge) < 8:
                reach = _radius(figure.volumes[0]) + radius + 3
                blocked.append((figure.x - reach, figure.x + reach))
        for ball in self.moving:
            if ball is not ignore and ball.rolling and ball.owner == hwnd and abs(ball.y - ledge) < 8:
                reach = ball.radius + radius + 3
                blocked.append((ball.x - reach, ball.x + reach))
        best, distance = None, float("inf")
        cursor = minimum
        for low, high in sorted(blocked):
            if high < cursor:
                continue
            if low >= cursor:
                candidate = min(low, maximum, max(cursor, desired))
                delta = abs(candidate - desired)
                if delta < distance:
                    best, distance = candidate, delta
            cursor = max(cursor, high)
            if cursor > maximum:
                return best
        candidate = max(cursor, desired)
        return candidate if abs(candidate - desired) < distance else best

    def collect_desktop_figures(self, snow, screen, left, right, *, blade_lift=0.0,
                                panel_gap=None, visible=None):
        """Collect props touched by the blade on the floor or raised taskbar."""
        def touches(x, y, radius, height):
            if x + radius < left or x - radius >= right:
                return False
            if height > blade_lift and snow.on_desktop_floor(screen, x, y):
                return True
            return (panel_gap is not None and snow.on_taskbar_platform(
                screen, x, y, blade_lift, panel_gap, height=height)
                and (visible is None or visible(0, x, y - radius)))

        hits = []
        for figure in tuple(self.items):
            if figure.hwnd != 0 or figure.window is not None:
                continue
            radius = _radius(figure.volumes[0])
            if touches(figure.x, figure.y, radius, figure.y - figure.crown):
                self.items.remove(figure)
                hits.append((figure.x, figure.y - radius, radius, sum(figure.volumes)))
        for ball in tuple(self.moving):
            if (ball.owner == 0 and ball.rolling
                    and touches(ball.x, ball.y, ball.radius, 2 * ball.radius)):
                self.moving.remove(ball)
                hits.append((ball.x, ball.y - ball.radius, ball.radius, ball.volume))
        return hits

    def scene(self):
        return tuple((figure.x, figure.y, *figure.radii) for figure in self.items)

    def scene_owners(self):
        return tuple(figure.hwnd for figure in self.items)

    def moving_scene(self):
        return (tuple((ball.x, ball.y, ball.radius) for ball in self.moving)
                + tuple((flight.figure.x, flight.figure.y, *flight.figure.radii) for flight in self.flying))

    def moving_scene_owners(self):
        return (tuple(FLYING_BALL_LAYER if ball.thrown else ball.owner if ball.owner in self._positions else 0
                      for ball in self.moving) + (FLYING_BALL_LAYER,) * len(self.flying))

    def drag_owner(self, snow, visible):
        if self.pointer is None:
            return 0
        x, y = self.pointer
        figure = self._stack_at(snow, x, y, visible)
        if figure is not None:
            return figure.hwnd
        support = snow.support_at(x, y, SUPPORT_REACH, visible=visible)
        return support[0] if support is not None else 0

    def drag_scene(self):
        if not self.enabled or not self.dragging:
            return ()
        x, y = self.pointer
        if self.held_figure is not None:
            base_x, baseline = self._figure_anchor(x, y)
            return ((base_x, baseline, *self.held_figure.radii),)
        if not self.held:
            return ()
        radius = self.loose_radius
        return ((x, y + radius - min(8.0, radius / 2), radius),)
