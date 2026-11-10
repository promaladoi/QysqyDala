"""Roll variable-sized snowballs and stack them with explicit mouse drags."""

from dataclasses import dataclass, field
from functools import lru_cache
from math import ceil, hypot, sqrt

from simulation import MAX_FLAKES

MAX_RADIUS = 36.0
MAX_VOLUME = MAX_RADIUS ** 2
MAX_STACKS = 24
STACK_RADII = (1.0, 0.73, 0.53, 0.40, 0.30)
STACK_HEIGHTS = tuple(1.8 * sum(STACK_RADII[:index]) + 2 * ratio
                      for index, ratio in enumerate(STACK_RADII))
MAX_LAYERS = len(STACK_RADII)
MAX_BALLS = MAX_STACKS * MAX_LAYERS
SUPPORT_REACH = 52
PICKUP_VOLUME = 12.0
GRAVITY = 760.0
SHATTER_SPEED = 745.0
MAX_IMPACT_FRAGMENTS = 8
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
    y: int
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


class Snowmen:
    def __init__(self):
        self.enabled = False
        self.clear()

    def clear(self):
        self.items = []
        self.moving = []
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
    def loose_radius(self):
        return _radius(self.held)

    def step(self, snow, windows, dt=1 / 60, *, visible=None):
        if not self.enabled:
            self.cancel_drag()
        dt = min(0.1, max(0.0, dt))
        if dt:
            self._drag_dt = min(1 / 30, max(1 / 120, dt))
        if not (self.items or self.moving or self.held_figure is not None) and (
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
                self._detach(figure, vx, vy)
        self.items = supported
        if dt and self.moving:
            pieces = min(4, ceil(dt * 60))
            for _ in range(pieces):
                self._advance(snow, dt / pieces, visible)
        if self.held_figure is not None and not self.dragging:
            self._restore_figure(snow, self._positions)
        if self.held and not self.dragging:
            self._park(snow, self._positions)

    def _detach(self, figure, vx, vy, *, rolling=False):
        bottom = figure.y
        base = None
        for index, (volume, displayed) in enumerate(zip(figure.volumes, figure.radii)):
            on_ledge = rolling and index == 0
            ball = _MovingBall(figure.x, bottom - displayed + _radius(volume), volume,
                               figure.hwnd, vx if index == 0 else vx + (index - 1) * 22,
                               0.0 if on_ledge else vy - index * 9, on_ledge)
            if base is None:
                base = ball
            self.moving.append(ball)
            bottom -= displayed * 1.8
        return base

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
        balls = tuple(self.moving)
        motion = None
        if any(ball.thrown for ball in balls):
            # Take one snapshot so a moving target's path is independent of update order.
            motion = {ball: (ball.x, ball.y, ball.vx, ball.vy, ball.rolling) for ball in balls}
        broken = set() if motion is not None else ()
        for ball in balls:
            if ball in broken:
                continue
            ball.age += dt
            if ball.rolling:
                self._roll(snow, ball, dt)
            elif self._fall(snow, ball, dt, motion, broken, visible):
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

    def _roll(self, snow, ball, dt):
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
            if isinstance(target, Snowman):
                self.items.remove(target)
                target = self._detach(target, 0.0, -24.0, rolling=True)
            relative = ball.vx - target.vx
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
        landing = snow.ball_landing(ball.x, ball.y, x, y) if y >= ball.y else None
        if ball.thrown:
            limit = (landing.y - ball.y) / (y - ball.y) if landing is not None else 1.0
            hit = self._throw_contact(snow, ball, x, y, dt, limit, motion, visible)
            if hit is not None:
                return self._hit_thrown_ball(snow, ball, x, y, dt, hit, broken)
        if landing is not None:
            owner, impact_x, impact_y = landing
            # Use the velocity at first contact, before the end of the frame.
            speed_squared = ball.vy ** 2 + 2 * GRAVITY * (impact_y - ball.y)
            if speed_squared >= SHATTER_SPEED ** 2:
                if self._shatter(snow, ball, impact_x, impact_y, sqrt(speed_squared)):
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

    def _throw_contact(self, snow, ball, x, y, dt, limit, motion, visible):
        dx, dy = x - ball.x, y - ball.y
        center_y = ball.y - ball.radius
        nearest = None
        margin = ball.radius + MAX_RADIUS
        left, right = min(ball.x, x) - margin, max(ball.x, x) + margin
        top, bottom = min(center_y, y - ball.radius) - margin, max(center_y, y - ball.radius) + margin

        def consider(target, part, owner, target_x, target_y, radius, drift_x=0.0, drift_y=0.0):
            nonlocal nearest, limit
            if min(target_y, target_y + drift_y) > bottom or max(target_y, target_y + drift_y) < top:
                return
            time = _swept_circle(dx - drift_x, dy - drift_y, target_x - ball.x,
                                 target_y - center_y, ball.radius + radius)
            if time is None or time > limit:
                return
            if nearest is not None and time == limit and snow.owner_rank(owner) >= snow.owner_rank(nearest[5]):
                return
            target_x += drift_x * time
            target_y += drift_y * time
            if visible is not None:
                center_x = ball.x + dx * time
                contact_y = center_y + dy * time
                distance = hypot(target_x - center_x, target_y - contact_y)
                edge = ball.radius / distance if distance else 0
                contact_x = center_x + (target_x - center_x) * edge
                contact_y += (target_y - contact_y) * edge
                if not visible(owner, contact_x, contact_y):
                    return
            limit = time
            nearest = target, part, time, target_x, target_y, owner

        for figure in self.items:
            if not left <= figure.x <= right or figure.y < top or figure.crown > bottom:
                continue
            baseline = figure.y
            for part, radius in enumerate(figure.radii):
                consider(figure, part, figure.hwnd, figure.x, baseline - radius, radius)
                baseline -= radius * 1.8
        for target in self.moving:
            if target is ball:
                continue
            state = motion.get(target)
            if state is None:
                state = target.x, target.y, target.vx, target.vy, target.rolling
            target_x, target_y, vx, vy, rolling = state
            drift_x = vx * dt
            if min(target_x, target_x + drift_x) > right or max(target_x, target_x + drift_x) < left:
                continue
            drift_y = 0.0 if rolling else (vy + 0.5 * GRAVITY * dt) * dt
            owner = target.owner if target.owner in self._positions else 0
            consider(target, 0, owner, target_x, target_y - target.radius, target.radius, drift_x, drift_y)
        return nearest

    def _hit_thrown_ball(self, snow, ball, x, y, dt, hit, broken):
        target, part, time, target_x, target_y, _ = hit
        ball.x += (x - ball.x) * time
        ball.y += (y - ball.y) * time
        ball.vy = min(880.0, ball.vy + GRAVITY * dt * time)
        nx, ny = target_x - ball.x, target_y - (ball.y - ball.radius)
        distance = hypot(nx, ny)
        if not distance:
            return False
        nx, ny = nx / distance, ny / distance
        resting = isinstance(target, Snowman)
        target_vx = 0.0 if resting else target.vx
        target_vy = 0.0 if resting else target.vy
        speed = (ball.vx - target_vx) * nx + (ball.vy - target_vy) * ny
        if speed <= 0:
            return False
        if resting:
            self.items.remove(target)
            offset = len(self.moving) + part
            self._detach(target, 0.0, -24.0, rolling=True)
            target = self.moving[offset]
        impulse = 1.22 * speed / (ball.volume + target.volume)
        ball.vx -= impulse * target.volume * nx
        ball.vy -= impulse * target.volume * ny
        target.vx += impulse * ball.volume * nx
        target.vy += impulse * ball.volume * ny
        if target.rolling:
            if target.vy < -SETTLE_SPEED:
                target.rolling = False
            else:
                target.vy = 0.0
        available = MAX_FLAKES - len(snow.particles)
        if speed >= THROW_SHATTER_SPEED and available >= 4:
            # Leave two fragment slots for the target even when the weather is full.
            self._shatter(snow, ball, ball.x, ball.y, speed, budget=available - 2)
            self._shatter(snow, target, target_x, target_y + target.radius, speed)
            self.moving.remove(target)
            broken.add(target)
            return True
        ball.x -= nx * 0.05
        ball.y -= ny * 0.05
        return False

    @staticmethod
    def _shatter(snow, ball, x, y, speed, *, budget=MAX_IMPACT_FRAGMENTS):
        count = min(MAX_FLAKES - len(snow.particles), MAX_IMPACT_FRAGMENTS, budget,
                    max(3, ceil(ball.radius / 5)))
        if count < 2:
            return False
        portion = ball.volume / count
        radius = min(4.8, max(1.05, 0.33 * sqrt(portion)))
        for index in range(count):
            spread = 2 * index / (count - 1) - 1
            volume = portion if index < count - 1 else ball.volume - portion * (count - 1)
            snow.release_ball(
                x + spread * ball.radius * 0.40, y - radius - max(1, ball.radius * 0.25),
                radius, ball.vx * 0.4 + spread * (75 + speed * 0.20),
                -(55 + speed * 0.10) * (1 - 0.45 * abs(spread)), volume=volume,
            )
        return True

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
        support = snow.support_at(x, y, SUPPORT_REACH, hwnd=hwnd, visible=visible)
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
        if self.held_figure is None and now is not None:
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
        if self.held_figure is not None:
            return
        if samples is not None and now is not None and now > samples[-1][0]:
            if self._flick_rest is not None and samples[-1][0] < self._flick_rest < now:
                samples.append((self._flick_rest, *previous))
            samples.append((now, x, y))
            if len(samples) > 9:
                del samples[:-9]
        self._flick_rest = None
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
            self._detach(nearest, velocity, -24.0, rolling=True)
        elif nearest.vx * direction < abs(velocity):
            nearest.vx = velocity

    def end_drag(self, snow, windows, x, y, *, allow_drop=True, visible=None, now=None):
        if not self.dragging:
            return
        positions = {window[0]: window[1:] for window in windows}
        if self.held_figure is not None:
            if allow_drop and self.enabled:
                base_x, baseline = self._figure_anchor(x, y)
                self._drop_figure(snow, positions, base_x, baseline, visible)
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

    def _drop_figure(self, snow, positions, x, y, visible):
        radius = _radius(self.held_figure.volumes[0])

        def can_hold(owner, base_x, baseline):
            support = snow.support_at(base_x, baseline, SUPPORT_REACH, hwnd=owner)
            if support is None:
                return False
            _, left, right, ledge = support
            return (left + radius <= base_x <= right - radius
                    and (visible is None or visible(owner, base_x, ledge - 1)))

        support = snow.support_at(x, y, SUPPORT_REACH, visible=can_hold)
        if support is None:
            return
        owner, left, right, ledge = support
        if not left + radius <= x <= right - radius or not self._position_is_clear(x, ledge, radius, owner):
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
        return len(self.moving) + sum(len(figure.volumes) for figure in self.items)

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

    def scene(self):
        return tuple((figure.x, figure.y, *figure.radii) for figure in self.items)

    def scene_owners(self):
        return tuple(figure.hwnd for figure in self.items)

    def moving_scene(self):
        return tuple((ball.x, ball.y, ball.radius) for ball in self.moving)

    def moving_scene_owners(self):
        return tuple(FLYING_BALL_LAYER if ball.thrown else ball.owner if ball.owner in self._positions else 0
                     for ball in self.moving)

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
