"""Windows desktop snowfall. Run with python snow_effect.py."""

import sys

if sys.platform != "win32":
    raise SystemExit("Windows 10 немесе Windows 11 қажет.")

import ctypes as ct
from ctypes import wintypes as wt
from math import ceil, floor
from pathlib import Path
from time import perf_counter
import winreg

from render import PaintEngine, SnowScreen, figure_bounds, inside_platform
from shovel import SnowShovel
from simulation import SnowSimulation
from snowman import MAX_RADIUS, Snowmen
from snowplow import Snowplow
from win32 import (
    APP_NAME, CreateWindowExW, DefWindowProcW, DestroyWindow, GetModuleHandleW, GUIThreadInfo, MSG,
    RegisterClassW, TaskbarTracker, WNDCLASSW, WNDPROC, bind, display_refresh_hz,
    enable_dpi_awareness, kernel32,
    list_windows, monitor_layout, shell32, user32, window_move_active,
)

CONTROL_CLASS = "SnowEffect.Tray"
INSTANCE_NAME = r"Local\SnowEffect-188BAF54-6574-4B66-9B05-84AA9700162D"
SETTINGS_KEY = r"Software\SnowEffect"
SNOWMAN_MODE_VALUE = "SnowmanModeEnabled"
WM_TRAY = 0x8001
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_QUIT = 0x0012
WM_DISPLAYCHANGE = 0x007E
WM_DPICHANGED = 0x02E0
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
NIN_SELECT = 0x0400
NIN_KEYSELECT = 0x0401
TOGGLE = 10
EXIT = 20
TOGGLE_SNOWMEN = 21
TOGGLE_SHOVEL = 22
VK_LBUTTON, VK_RBUTTON = 1, 2
SM_SWAPBUTTON = 23
INTENSITIES = {
    11: ("Баяу", 0.35),
    12: ("Орташа", 0.65),
    13: ("Қалың", 1.0),
}
MF_STRING = 0x0000
MF_CHECKED = 0x0008
MF_POPUP = 0x0010
MF_SEPARATOR = 0x0800
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETFOCUS, NIM_SETVERSION = range(5)
NOTIFYICON_VERSION_4 = 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_SHOWTIP = 1, 2, 4, 0x80
TPM_RIGHTBUTTON = 0x0002
QS_ALLINPUT = 0x04FF
MWMO_INPUTAVAILABLE = 0x0004
INFINITE = 0xFFFFFFFF
WAIT_FAILED = 0xFFFFFFFF
PM_REMOVE = 0x0001
TIMER_ACCESS = 0x00100002  # SYNCHRONIZE | TIMER_MODIFY_STATE
HIGH_RESOLUTION_TIMER = 0x0002
BELOW_NORMAL_PRIORITY_CLASS = 0x4000
DRAG_TILE_SIZE = 2 * ceil(MAX_RADIUS) + 8
# Fits the widest classic arms and the tallest five-ball stack, including its hat.
STACK_DRAG_TILE = 112, 224
WINDOW_MOTION_GRACE = 0.14
WINDOW_MOTION_BUDGET = 0.002


class NotifyIconData(ct.Structure):
    _fields_ = [
        ("size", wt.DWORD),
        ("hwnd", wt.HWND),
        ("id", wt.UINT),
        ("flags", wt.UINT),
        ("callback", wt.UINT),
        ("icon", wt.HICON),
        ("tip", wt.WCHAR * 128),
        ("state", wt.DWORD),
        ("state_mask", wt.DWORD),
        ("info", wt.WCHAR * 256),
        ("version", wt.UINT),
        ("info_title", wt.WCHAR * 64),
        ("info_flags", wt.DWORD),
        ("guid", wt.DWORD * 4),
        ("balloon_icon", wt.HICON),
    ]


notify_icon = bind(shell32, "Shell_NotifyIconW", wt.BOOL, wt.DWORD,
                   ct.POINTER(NotifyIconData))
load_image = bind(user32, "LoadImageW", wt.HANDLE, wt.HINSTANCE, wt.LPCWSTR,
                  wt.UINT, ct.c_int, ct.c_int, wt.UINT)
destroy_icon = bind(user32, "DestroyIcon", wt.BOOL, wt.HICON)
register_message = bind(user32, "RegisterWindowMessageW", wt.UINT, wt.LPCWSTR)
create_menu = bind(user32, "CreatePopupMenu", wt.HMENU)
append_menu = bind(user32, "AppendMenuW", wt.BOOL, wt.HMENU, wt.UINT,
                   ct.c_size_t, wt.LPCWSTR)
destroy_menu = bind(user32, "DestroyMenu", wt.BOOL, wt.HMENU)
track_menu = bind(user32, "TrackPopupMenu", wt.BOOL, wt.HMENU, wt.UINT,
                  ct.c_int, ct.c_int, ct.c_int, wt.HWND, ct.POINTER(wt.RECT))
get_cursor_pos = bind(user32, "GetCursorPos", wt.BOOL, ct.POINTER(wt.POINT))
get_key_state = bind(user32, "GetAsyncKeyState", wt.SHORT, ct.c_int)
get_system_metrics = bind(user32, "GetSystemMetrics", ct.c_int, ct.c_int)
set_foreground = bind(user32, "SetForegroundWindow", wt.BOOL, wt.HWND)
post_message = bind(user32, "PostMessageW", wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
post_quit = bind(user32, "PostQuitMessage", None, ct.c_int)
peek_message = bind(user32, "PeekMessageW", wt.BOOL, ct.POINTER(MSG), wt.HWND, wt.UINT,
                    wt.UINT, wt.UINT)
wait_for_input = bind(user32, "MsgWaitForMultipleObjectsEx", wt.DWORD, wt.DWORD,
                      ct.POINTER(wt.HANDLE), wt.DWORD, wt.DWORD, wt.DWORD)
create_timer = bind(kernel32, "CreateWaitableTimerExW", wt.HANDLE, ct.c_void_p,
                    wt.LPCWSTR, wt.DWORD, wt.DWORD)
arm_timer = bind(kernel32, "SetWaitableTimer", wt.BOOL, wt.HANDLE,
                 ct.POINTER(ct.c_longlong), wt.LONG, ct.c_void_p, ct.c_void_p, wt.BOOL)
cancel_timer = bind(kernel32, "CancelWaitableTimer", wt.BOOL, wt.HANDLE)
translate_message = bind(user32, "TranslateMessage", wt.BOOL, ct.POINTER(MSG))
dispatch_message = bind(user32, "DispatchMessageW", ct.c_ssize_t, ct.POINTER(MSG))
unregister_class = bind(user32, "UnregisterClassW", wt.BOOL, wt.LPCWSTR, wt.HINSTANCE)
message_box = bind(user32, "MessageBoxW", ct.c_int, wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT)
create_mutex = bind(kernel32, "CreateMutexW", wt.HANDLE, ct.c_void_p, wt.BOOL, wt.LPCWSTR)
close_handle = bind(kernel32, "CloseHandle", wt.BOOL, wt.HANDLE)
get_current_process = bind(kernel32, "GetCurrentProcess", wt.HANDLE)
set_priority_class = bind(kernel32, "SetPriorityClass", wt.BOOL, wt.HANDLE, wt.DWORD)


def require(result):
    if not result:
        raise ct.WinError(ct.get_last_error())
    return result


def load_snowman_mode():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS_KEY) as key:
            value, value_type = winreg.QueryValueEx(key, SNOWMAN_MODE_VALUE)
    except OSError:
        return False
    return value_type == winreg.REG_DWORD and value == 1


def save_snowman_mode(enabled):
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, SETTINGS_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, SNOWMAN_MODE_VALUE, 0, winreg.REG_DWORD, int(enabled))
    except OSError:
        # The current mode still works if Windows settings storage is unavailable.
        pass


class FrameClock:
    """Wait for a frame deadline or a window message without busy-waiting."""

    def __init__(self):
        timer = create_timer(None, None, HIGH_RESOLUTION_TIMER, TIMER_ACCESS)
        self.high_resolution = bool(timer)
        self.timer = timer or require(create_timer(None, None, 0, TIMER_ACCESS))
        self.handles = (wt.HANDLE * 1)(self.timer)
        self.configure_refresh(60)

    def configure_refresh(self, hz):
        self.normal_period = max(1, round(hz / 30)) / hz
        self.slow_period = max(1, round(hz / 20)) / hz
        self.drag_period = (max(1, round(hz / 60), ceil(hz / 66)) / hz
                            if self.high_resolution else self.normal_period)

    @property
    def snow_period(self):
        return self.slow_period if self.overloaded else self.normal_period

    def paint_interval(self, dragging):
        interval = self.snow_period
        if dragging and self.drag_period < interval:
            return ceil(interval / self.drag_period - 1e-6) * self.drag_period
        return interval

    def reset(self):
        self.overloaded = False
        self.cost = 0.0
        self.last_overrun = None
        self.recheck_at = perf_counter() + 1.0
        self.kick()

    def kick(self):
        self.deadline = perf_counter()
        self.arm(0)

    def arm(self, delay):
        due = ct.c_longlong(-max(1, round(delay * 10_000_000)))
        require(arm_timer(self.timer, ct.byref(due), 0, None, None, False))

    def advance(self, started, finished, *, hidden=False, dragging=False,
                window_moving=False, snow_frame=True):
        if snow_frame and not hidden:
            duration = finished - started
            self.cost += 0.12 * (duration - self.cost)
            if duration > self.normal_period * 0.93:
                if self.last_overrun is not None and finished - self.last_overrun < 0.8:
                    self.overloaded = True
                    self.recheck_at = finished + 2.5
                self.last_overrun = finished
            if finished >= self.recheck_at:
                if self.cost > self.normal_period * 0.72:
                    self.overloaded = True
                elif self.cost < self.normal_period * 0.35:
                    self.overloaded = False
                self.recheck_at = finished + 3.0
        if dragging:
            period = self.drag_period
        elif hidden:
            period = 1 / 8
        elif (window_moving and not self.overloaded
              and max(self.cost, finished - started) < WINDOW_MOTION_BUDGET):
            period = self.drag_period
        else:
            period = self.snow_period
        self.deadline += period
        if self.deadline <= finished:
            # Rebase after an overrun. Waiting a whole additional frame would
            # turn a slightly late update into a visible double-frame pause.
            self.deadline = finished + 0.002
        self.arm(self.deadline - finished)

    def wait(self, enabled):
        count = int(enabled)
        result = wait_for_input(count, self.handles if enabled else None, INFINITE,
                                QS_ALLINPUT, MWMO_INPUTAVAILABLE)
        if result == WAIT_FAILED:
            raise ct.WinError(ct.get_last_error())
        return enabled and result == 0

    def pause(self):
        cancel_timer(self.timer)

    def close(self):
        close_handle(self.timer)


class DragPreview:
    """A cursor-sized layer, independent of full-monitor snowfall updates."""

    def __init__(self, engine, *, whole=False):
        self.whole = whole
        self.width, self.height = STACK_DRAG_TILE if whole else (DRAG_TILE_SIZE, DRAG_TILE_SIZE)
        self.screen = SnowScreen(engine, (0, 0, self.width, self.height))
        self.key = None

    def render(self, ball, work_area, windows, platforms, raise_layer, *, shovel=None):
        if shovel is None:
            x, bottom, *radii = ball
            center_y = bottom - radii[0]
        else:
            x, bottom, direction = shovel
            center_y = bottom - 18
        if self.whole:
            left = floor(x) - self.width // 2
            top = floor(bottom) - self.height + 4
        else:
            left, top = round(x) - self.width // 2, round(center_y) - self.height // 2
        right, lower = left + self.width, top + self.height

        def local(rect):
            return rect[0] - left, rect[1] - top, rect[2] - left, rect[3] - top

        def overlaps(rect):
            return rect[0] < right and rect[1] < lower and rect[2] > left and rect[3] > top

        local_windows = tuple((window[0], *local(window[1:]))
                              for window in windows if overlaps(window[1:]))
        local_platforms = tuple((*local(shape), shape[4]) for shape in platforms if overlaps(shape))
        key = ball, shovel, work_area, local_windows, local_platforms
        if key != self.key:
            self.screen.overlay.set_destination(left, top)
            figures = ((x - left, bottom - top, *radii),) if shovel is None else ()
            blade = (x - left, bottom - top, direction) if shovel is not None else None
            self.screen.render((), (), figures, local_windows, local(work_area),
                               local_platforms, shovel=blade)
            self.key = key
        if self.screen.visible and raise_layer:
            self.screen.overlay.show()

    def hide(self):
        if self.screen.visible:
            self.screen.hide()
        self.key = None

    def close(self):
        self.screen.close()


class SnowEffect:
    def __init__(self):
        self.instance = require(GetModuleHandleW(None))
        self.hwnd = None
        self.class_registered = False
        self.icon = None
        self.tray = None
        self.engine = None
        self.clock = None
        self.screens = []
        self.snow_controls = {}
        self.drag_previews = {}
        self.monitors = []
        self.base_layout = {}
        self.work_areas = {}
        self.platforms = {}
        self.windows = []
        self.gui_thread = GUIThreadInfo(size=ct.sizeof(GUIThreadInfo))
        self.window_moving = False
        self.window_motion_until = 0.0
        self.taskbars = TaskbarTracker()
        self.simulation = SnowSimulation()
        self.snowmen = Snowmen()
        self.snowmen.enabled = load_snowman_mode()
        self.shovel = SnowShovel()
        self.plow = Snowplow()
        self.pointer = wt.POINT()
        self.drag_button = VK_LBUTTON
        self.enabled = False
        self.intensity = 12
        self.last_frame = 0.0
        self.next_snow_frame = 0.0
        self.snow_frame = True
        self.next_monitor_check = 0.0
        self.displays_dirty = True
        self.menu_open = False
        self.error = None
        self.taskbar_created = require(register_message("TaskbarCreated"))
        self.wndproc = WNDPROC(self.on_message)

    @property
    def mouse_tool(self):
        return self.shovel if self.shovel.enabled else self.snowmen

    def run(self):
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self.wndproc
        window_class.hInstance = self.instance
        window_class.lpszClassName = CONTROL_CLASS
        require(RegisterClassW(ct.byref(window_class)))
        self.class_registered = True
        self.hwnd = require(CreateWindowExW(
            0, CONTROL_CLASS, APP_NAME, 0x80000000, 0, 0, 0, 0,
            None, None, self.instance, None,
        ))
        icon_path = Path(__file__).with_name("snowflake.ico")
        # IMAGE_ICON and LR_LOADFROMFILE|LR_DEFAULTSIZE
        self.icon = require(load_image(None, str(icon_path), 1, 0, 0, 0x0050))
        self.tray = NotifyIconData(size=ct.sizeof(NotifyIconData), hwnd=self.hwnd, id=1,
                                   flags=NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP,
                                   callback=WM_TRAY, icon=self.icon)
        self.add_tray()
        self.engine = PaintEngine()
        self.clock = FrameClock()
        self.toggle()
        message = MSG()
        while True:
            for _ in range(64):
                if not peek_message(ct.byref(message), None, 0, 0, PM_REMOVE):
                    break
                if message.message == WM_QUIT:
                    if self.error is not None:
                        raise self.error
                    return
                translate_message(ct.byref(message))
                dispatch_message(ct.byref(message))
            if self.clock.wait(self.enabled):
                now = perf_counter()
                self.frame(now)
                hidden = not any(screen.visible for screen in self.screens)
                if hidden:
                    hidden = not any(screen.visible for screen in self.snow_controls.values())
                finished = perf_counter()
                dragging = self.mouse_tool.dragging
                if dragging and self.snow_frame and finished - now >= self.clock.drag_period:
                    self.next_snow_frame = max(self.next_snow_frame,
                                               finished + self.clock.drag_period / 2)
                self.clock.advance(now, finished, hidden=hidden, dragging=dragging,
                                   window_moving=self.window_moving,
                                   snow_frame=self.snow_frame)

    def update_tip(self):
        state = INTENSITIES[self.intensity][0] if self.enabled else "Кідіртілді"
        if self.enabled and self.shovel.enabled:
            state = "Қар күрегі — қарды тінтуірмен ысырыңыз"
        elif self.enabled and self.snowmen.enabled:
            state = "Аққала жасау — қарды тінтуірмен сүйреңіз"
        self.tray.tip = f"{APP_NAME} — {state}"

    def add_tray(self):
        self.update_tip()
        if not (notify_icon(NIM_ADD, ct.byref(self.tray))
                or notify_icon(NIM_MODIFY, ct.byref(self.tray))):
            raise RuntimeError("Windows хабарландыру аймағына белгіше қосылмады.")
        self.tray.version = NOTIFYICON_VERSION_4
        notify_icon(NIM_SETVERSION, ct.byref(self.tray))

    def toggle(self):
        self.enabled = not self.enabled
        if self.enabled:
            self.last_frame = perf_counter()
            self.next_monitor_check = 0.0
            self.next_snow_frame = 0.0
            self.window_moving = False
            self.window_motion_until = 0.0
            self.clock.reset()
        else:
            self.clock.pause()
            self.simulation.clear()
            self.snowmen.clear()
            self.shovel.cancel_drag()
            self.plow.clear()
            self.hide_scene()
        self.update_tip()
        notify_icon(NIM_MODIFY, ct.byref(self.tray))

    def frame(self, now):
        self.snow_frame = False
        if self.mouse_tool.dragging and not self.displays_dirty and now < self.next_snow_frame - 0.002:
            self.update_drag()
            self.plow.capture_held(self.snowmen, visible=self.surface_visible)
            if self.mouse_tool.dragging:
                self.paint_drag_preview()
                return
        self.snow_frame = True
        native_move = window_move_active(self.gui_thread)
        # Shell appbar queries can wait on Explorer. Defer periodic refreshes during a native drag.
        if self.displays_dirty or (not native_move and now >= self.next_monitor_check):
            layout = dict(monitor_layout())
            monitors = list(layout)
            self.taskbars.refresh(monitors)
            if self.displays_dirty or monitors != self.monitors:
                self.clock.configure_refresh(display_refresh_hz())
                self.close_controls()
                for screen in self.screens:
                    screen.close()
                self.screens.clear()
                for rect in monitors:
                    self.screens.append(SnowScreen(self.engine, rect))
            if monitors != self.monitors:
                self.monitors = monitors
                self.simulation.configure(monitors, list(layout.values()))
                self.snowmen.clear()
                self.shovel.cancel_drag()
                self.plow.clear()
            self.base_layout = layout
            self.work_areas = self.taskbars.desktop_areas(layout)
            self.displays_dirty = False
            self.next_monitor_check = now + 1.0
        interval = self.clock.paint_interval(self.mouse_tool.dragging)
        self.next_snow_frame = now + interval
        dt = min(now - self.last_frame, 0.10)
        self.last_frame = now
        self.platforms = self.taskbars.platforms(self.base_layout)
        previous_windows = self.windows
        self.windows = list_windows((self.hwnd, *self.taskbars.handles))
        if self.windows != previous_windows:
            previous_bounds = {window[0]: window for window in previous_windows}
            if any(window != previous_bounds.get(window[0], window) for window in self.windows):
                self.window_motion_until = now + WINDOW_MOTION_GRACE
        self.window_moving = native_move or now < self.window_motion_until
        covered = self.desktop_covered()
        if (covered and not self.snowmen.dragging and not self.snowmen.visible_flight
                and not self.simulation.impact_active):
            self.update_drag()
            self.plow.step(self.simulation, self.snowmen, dt, paused=True)
            self.hide_scene()
            return
        self.simulation.update_windows(self.windows, elapsed=dt)
        self.simulation.update_grounds(
            [self.work_areas[rect] for rect in self.monitors],
            [self.platforms.get(rect, ()) for rect in self.monitors],
        )
        self.snowmen.step(self.simulation, self.windows, dt=dt, visible=self.surface_visible)
        self.update_drag()
        self.plow.step(self.simulation, self.snowmen, dt, paused=covered or self.shovel.dragging,
                       visible=self.surface_visible)
        # Commit the held tool before the slower monitor-sized weather draw.
        self.paint_drag_preview()
        self.simulation.step(dt, INTENSITIES[self.intensity][1])
        drifts = list(self.simulation.drifts())
        falling = tuple(self.simulation.falling_drifts())
        interactive = self.mouse_tool.enabled and not self.window_moving
        art_drifts = falling if interactive else drifts
        if falling and not interactive:
            art_drifts = (*drifts, *falling)
        stationary = self.snowmen.scene()
        owners = self.snowmen.scene_owners()
        moving = self.snowmen.moving_scene()
        moving_owners = self.snowmen.moving_scene_owners()
        flakes = self.simulation.particles
        impact_flakes = tuple(self.simulation.impact_flakes())
        if self.window_moving:
            # Draw the entire scene on one click-through layer during a window drag.
            for control in self.snow_controls.values():
                if control.visible:
                    control.hide()
        if self.snowmen.enabled and not self.window_moving:
            art_figures, art_owners = moving, moving_owners
            # Keep the costume visible without adding it to the mouse hit area.
            costumes = tuple((owner, figure) for owner, figure in zip(owners, stationary)
                             if len(figure) >= 5)
        else:
            art_figures, art_owners = stationary + moving, owners + moving_owners
            costumes = ()
        raised_layer = False
        for screen in self.screens:
            work_area = self.work_areas[screen.rect]
            platforms = self.platforms.get(screen.rect, ())
            art_visible = screen.visible
            plow = self.plow.scene(screen.rect)
            plow_pitch = self.plow.pitch(screen.rect) if plow is not None else 0.0
            pushed = self.plow.pushed_scene(screen.rect)
            figures = art_figures + pushed if pushed else art_figures
            figure_owners = art_owners + (0,) * len(pushed) if pushed else art_owners
            if interactive:
                control = self.snow_controls.get(screen.rect)
                if control is None:
                    control = SnowScreen(self.engine, screen.rect, self.on_snow_pointer)
                    self.snow_controls[screen.rect] = control
                was_visible = control.visible
                control.render((), drifts, stationary if self.snowmen.enabled else (),
                               self.windows, work_area, platforms, snowmen_owners=owners)
                screen.render(flakes, art_drifts, figures, self.windows,
                              work_area, platforms, snowmen_owners=figure_owners, costumes=costumes,
                              plow=plow, plow_pitch=plow_pitch, impact_flakes=impact_flakes)
                if control.visible and not was_visible and screen.visible:
                    screen.overlay.show()
                    raised_layer = True
            else:
                screen.render(flakes, art_drifts, figures, self.windows,
                              work_area, platforms, snowmen_owners=figure_owners,
                              cache_costumes=self.window_moving, plow=plow, plow_pitch=plow_pitch,
                              impact_flakes=impact_flakes)
            raised_layer |= screen.visible and not art_visible
        if raised_layer:
            self.paint_drag_preview(True)

    def desktop_covered(self):
        for left, top, right, bottom in self.work_areas.values():
            for _, x1, y1, x2, y2 in self.windows:
                if x1 < right and x2 > left and y1 < bottom and y2 > top:
                    if not (x1 <= left and y1 <= top and x2 >= right and y2 >= bottom):
                        return False
                    break
            else:
                return False
        return bool(self.work_areas)

    def hide_scene(self):
        for screen in (*self.screens, *self.snow_controls.values()):
            if screen.visible:
                screen.hide()
        for preview in self.drag_previews.values():
            preview.hide()

    def update_drag(self):
        tool = self.mouse_tool
        if not tool.dragging:
            return
        if not get_cursor_pos(ct.byref(self.pointer)):
            tool.cancel_drag()
            return
        x, y = self.pointer.x, self.pointer.y
        # Poll release only for a drag that began on a visible snow pixel.
        if get_key_state(self.drag_button) < 0:
            if tool is self.snowmen:
                tool.move_drag(self.simulation, x, y, visible=self.surface_visible, now=perf_counter())
            else:
                tool.move_drag(self.simulation, x, y, visible=self.surface_visible)
        else:
            self.finish_drag(x, y)

    def paint_drag_preview(self, raise_layer=False):
        scene = self.snowmen.drag_scene()
        shovel = self.shovel.preview(self.simulation, self.surface_visible)
        if not scene and shovel is None:
            for preview in self.drag_previews.values():
                preview.hide()
            return
        ball = scene[0] if scene else None
        whole = ball is not None and len(ball) > 3
        windows = self.windows
        if shovel is not None:
            x, y, _ = shovel
            bounds = x - 27, y - 38, x + 27, y + 2
            owner = self.shovel.owner
        elif whole:
            bounds = figure_bounds(ball)
            # A carried figure stays visible above apps until it is put down.
            windows = ()
            owner = 0
        else:
            x, y, radius = ball
            bounds = x - radius, y - 2 * radius, x + radius, y
            owner = self.snowmen.drag_owner(self.simulation, self.surface_visible)
            if not owner:
                windows = ()
        if owner:
            for index, window in enumerate(windows):
                if window[0] == owner:
                    windows = windows[:index + 1]
                    break
        for (_, stack_preview), preview in self.drag_previews.items():
            if stack_preview != whole:
                preview.hide()
        for screen in self.screens:
            rect = screen.rect
            work = self.work_areas[rect]
            left, top, right, bottom = work
            key = rect, whole
            preview = self.drag_previews.get(key)
            if bounds[2] < left or bounds[0] >= right or bounds[3] < top or bounds[1] >= bottom:
                if preview:
                    preview.hide()
                continue
            if preview is None:
                preview = DragPreview(self.engine, whole=whole)
                self.drag_previews[key] = preview
            preview.render(ball, work, windows, self.platforms.get(rect, ()), raise_layer,
                           shovel=shovel)

    def on_snow_pointer(self, down, x, y):
        tool = self.mouse_tool
        if not self.enabled or not tool.enabled:
            return
        try:
            if down:
                if tool is self.snowmen:
                    started = tool.begin_drag(self.simulation, x, y, visible=self.surface_visible, now=perf_counter())
                else:
                    started = tool.begin_drag(self.simulation, x, y, visible=self.surface_visible)
                if started:
                    self.drag_button = VK_RBUTTON if get_system_metrics(SM_SWAPBUTTON) else VK_LBUTTON
                    if self.snowmen.whole_dragging:
                        self.next_snow_frame = 0.0
                    self.clock.kick()
            elif tool.dragging:
                self.finish_drag(x, y)
        except Exception as error:
            self.error = error
            post_quit(1)

    def in_work_area(self, x, y):
        return (any(left <= x < right and top <= y < bottom
                    for left, top, right, bottom in self.work_areas.values())
                and not any(inside_platform(x, y, platform)
                            for shapes in self.platforms.values() for platform in shapes))

    def surface_visible(self, owner, x, y):
        if not self.in_work_area(x, y):
            return False
        for hwnd, left, top, right, bottom in self.windows:
            if left <= x < right and top <= y < bottom:
                return False
            if hwnd == owner:
                return True
        return owner == 0

    def finish_drag(self, x, y):
        if self.shovel.enabled:
            self.shovel.move_drag(self.simulation, x, y, visible=self.surface_visible)
            self.shovel.cancel_drag()
        else:
            self.snowmen.end_drag(self.simulation, self.windows, x, y,
                                  allow_drop=self.in_work_area(x, y), visible=self.surface_visible,
                                  now=perf_counter())
        self.paint_drag_preview()

    def select_mouse_tool(self, tool):
        enabled = not tool.enabled
        was_snowman_enabled = self.snowmen.enabled
        self.mouse_tool.cancel_drag()
        self.snowmen.enabled = enabled and tool is self.snowmen
        self.shovel.enabled = enabled and tool is self.shovel
        if self.snowmen.enabled != was_snowman_enabled:
            save_snowman_mode(self.snowmen.enabled)
        if enabled:
            self.paint_drag_preview()
        else:
            self.close_controls()
        self.update_tip()
        notify_icon(NIM_MODIFY, ct.byref(self.tray))

    def close_controls(self):
        for screen in self.snow_controls.values():
            screen.close()
        self.snow_controls.clear()
        for preview in self.drag_previews.values():
            preview.close()
        self.drag_previews.clear()

    def popup_menu(self, keyboard_anchor=None):
        if self.menu_open:
            return
        self.mouse_tool.cancel_drag()
        self.paint_drag_preview()
        position = wt.POINT()
        if keyboard_anchor is None:
            require(get_cursor_pos(ct.byref(position)))
        else:
            position.x = ct.c_short(keyboard_anchor & 0xFFFF).value
            position.y = ct.c_short((keyboard_anchor >> 16) & 0xFFFF).value
        menu = require(create_menu())
        intensity_menu = None
        self.menu_open = True
        try:
            require(append_menu(menu, MF_STRING, TOGGLE, "Кідірту" if self.enabled else "Қарды қосу"))
            intensity_menu = require(create_menu())
            for command, (label, _) in INTENSITIES.items():
                flags = MF_CHECKED if command == self.intensity else MF_STRING
                require(append_menu(intensity_menu, flags, command, label))
            require(append_menu(menu, MF_POPUP, intensity_menu, "Қар қарқындылығы"))
            intensity_menu = None  # DestroyMenu also destroys attached submenus.
            snowman_flags = MF_CHECKED if self.snowmen.enabled else MF_STRING
            require(append_menu(menu, snowman_flags, TOGGLE_SNOWMEN, "Аққала жасау режимі"))
            shovel_flags = MF_CHECKED if self.shovel.enabled else MF_STRING
            require(append_menu(menu, shovel_flags, TOGGLE_SHOVEL, "Қар күрегі"))
            require(append_menu(menu, MF_SEPARATOR, 0, None))
            require(append_menu(menu, MF_STRING, EXIT, "Шығу"))
            set_foreground(self.hwnd)
            track_menu(menu, TPM_RIGHTBUTTON, position.x, position.y, 0, self.hwnd, None)
            post_message(self.hwnd, 0, 0, 0)
            notify_icon(NIM_SETFOCUS, ct.byref(self.tray))
        finally:
            if intensity_menu:
                destroy_menu(intensity_menu)
            destroy_menu(menu)
            self.menu_open = False

    def on_message(self, hwnd, message, wparam, lparam):
        try:
            if message == WM_TRAY:
                event = lparam & 0xFFFF
                if event in (WM_CONTEXTMENU, NIN_SELECT, NIN_KEYSELECT, WM_LBUTTONUP, WM_RBUTTONUP):
                    self.popup_menu(wparam if event == NIN_KEYSELECT else None)
                return 0
            if message == WM_COMMAND:
                command = wparam & 0xFFFF
                if command == TOGGLE:
                    self.toggle()
                elif command in INTENSITIES:
                    self.intensity = command
                    self.update_tip()
                    notify_icon(NIM_MODIFY, ct.byref(self.tray))
                elif command == TOGGLE_SNOWMEN:
                    self.select_mouse_tool(self.snowmen)
                elif command == TOGGLE_SHOVEL:
                    self.select_mouse_tool(self.shovel)
                elif command == EXIT:
                    post_quit(0)
                return 0
            if message in (WM_DISPLAYCHANGE, WM_DPICHANGED):
                self.displays_dirty = True
                return 0
            if message == self.taskbar_created and self.tray is not None:
                self.add_tray()
                return 0
            if message == WM_CLOSE:
                post_quit(0)
                return 0
        except Exception as error:
            self.error = error
            post_quit(1)
            return 0
        return DefWindowProcW(hwnd, message, wparam, lparam)

    def close(self):
        if self.clock is not None:
            self.clock.close()
        self.close_controls()
        for screen in self.screens:
            screen.close()
        if self.engine is not None:
            self.engine.close()
        if self.tray is not None:
            notify_icon(NIM_DELETE, ct.byref(self.tray))
        if self.icon:
            destroy_icon(self.icon)
        if self.hwnd:
            DestroyWindow(self.hwnd)
        if self.class_registered:
            unregister_class(CONTROL_CLASS, self.instance)


def main():
    instance_lock = None
    app = None
    try:
        ct.set_last_error(0)
        instance_lock = require(create_mutex(None, False, INSTANCE_NAME))
        if ct.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return 0
        enable_dpi_awareness()
        set_priority_class(get_current_process(), BELOW_NORMAL_PRIORITY_CLASS)
        app = SnowEffect()
        app.run()
        return 0
    except Exception as error:
        if app is not None:
            app.close()
            app = None
        message_box(None, f"«{APP_NAME}» іске қосылмады немесе тоқтады.\n\n{error}",
                    APP_NAME, 0x10)
        return 1
    finally:
        if app is not None:
            app.close()
        if instance_lock:
            close_handle(instance_lock)


if __name__ == "__main__":
    raise SystemExit(main())
