"""Native window geometry and per-monitor layered surfaces."""

import ctypes as c
from ctypes import wintypes as w

APP_NAME = "Қысқы дала"
from threading import Thread
from time import monotonic
from uuid import UUID
import winreg


def bind(dll, name, result_type, *argument_types):
    function = getattr(dll, name)
    function.restype = result_type
    function.argtypes = argument_types
    return function


LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x800
user32 = c.WinDLL("user32", use_last_error=True, winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
# These read-only queries return all the information their callers need.
_window_queries = c.WinDLL("user32", winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
gdi32 = c.WinDLL("gdi32", use_last_error=True, winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
shell32 = c.WinDLL("shell32", use_last_error=True, winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
kernel32 = c.WinDLL("kernel32", use_last_error=True, winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
dwmapi = c.WinDLL("dwmapi", winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
shcore = c.WinDLL("shcore", winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)

LRESULT = c.c_ssize_t
HCURSOR = w.HCURSOR
WNDPROC = c.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
MONITORENUMPROC = c.WINFUNCTYPE(w.BOOL, w.HANDLE, w.HDC, c.POINTER(w.RECT), w.LPARAM)


class WNDCLASSW(c.Structure):
    _fields_ = [
        ("style", w.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", c.c_int),
        ("cbWndExtra", c.c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON),
        ("hCursor", w.HCURSOR),
        ("hbrBackground", w.HBRUSH),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
    ]


class MSG(c.Structure):
    _fields_ = w.MSG._fields_ + [("lPrivate", w.DWORD)]


class BITMAPINFOHEADER(c.Structure):
    _fields_ = [
        ("biSize", w.DWORD),
        ("biWidth", w.LONG),
        ("biHeight", w.LONG),
        ("biPlanes", w.WORD),
        ("biBitCount", w.WORD),
        ("biCompression", w.DWORD),
        ("biSizeImage", w.DWORD),
        ("biXPelsPerMeter", w.LONG),
        ("biYPelsPerMeter", w.LONG),
        ("biClrUsed", w.DWORD),
        ("biClrImportant", w.DWORD),
    ]


class BITMAPINFO(c.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", w.DWORD * 1)]


class BLENDFUNCTION(c.Structure):
    _fields_ = [
        ("BlendOp", w.BYTE),
        ("BlendFlags", w.BYTE),
        ("SourceConstantAlpha", w.BYTE),
        ("AlphaFormat", w.BYTE),
    ]


class MONITORINFO(c.Structure):
    _fields_ = [
        ("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD),
    ]


class GUIThreadInfo(c.Structure):
    _fields_ = [
        ("size", w.DWORD), ("flags", w.DWORD), ("active", w.HWND), ("focus", w.HWND),
        ("capture", w.HWND), ("menu_owner", w.HWND), ("move_size", w.HWND),
        ("caret", w.HWND), ("caret_rect", w.RECT),
    ]


class APPBARDATA(c.Structure):
    _fields_ = [
        ("cbSize", w.DWORD), ("hWnd", w.HWND), ("uCallbackMessage", w.UINT),
        ("uEdge", w.UINT), ("rc", w.RECT), ("lParam", w.LPARAM),
    ]


class UPDATELAYEREDWINDOWINFO(c.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("hdcDst", w.HDC),
        ("pptDst", c.POINTER(w.POINT)),
        ("psize", c.POINTER(w.SIZE)),
        ("hdcSrc", w.HDC),
        ("pptSrc", c.POINTER(w.POINT)),
        ("crKey", w.DWORD),
        ("pblend", c.POINTER(BLENDFUNCTION)),
        ("dwFlags", w.DWORD),
        ("prcDirty", c.POINTER(w.RECT)),
    ]


GetModuleHandleW = bind(kernel32, "GetModuleHandleW", w.HMODULE, w.LPCWSTR)
DefWindowProcW = bind(user32, "DefWindowProcW", LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
RegisterClassW = bind(user32, "RegisterClassW", w.ATOM, c.POINTER(WNDCLASSW))
CreateWindowExW = bind(
    user32, "CreateWindowExW", w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
    c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p,
)
DestroyWindow = bind(user32, "DestroyWindow", w.BOOL, w.HWND)
SetWindowPos = bind(
    user32, "SetWindowPos", w.BOOL, w.HWND, w.HWND, c.c_int, c.c_int,
    c.c_int, c.c_int, w.UINT,
)
ShowWindow = bind(user32, "ShowWindow", w.BOOL, w.HWND, c.c_int)
UpdateLayeredWindow = bind(
    user32, "UpdateLayeredWindow", w.BOOL, w.HWND, w.HDC, c.POINTER(w.POINT),
    c.POINTER(w.SIZE), w.HDC, c.POINTER(w.POINT), w.DWORD, c.POINTER(BLENDFUNCTION), w.DWORD,
)
UpdateLayeredWindowIndirect = bind(
    user32, "UpdateLayeredWindowIndirect", w.BOOL, w.HWND, c.POINTER(UPDATELAYEREDWINDOWINFO),
)

CreateCompatibleDC = bind(gdi32, "CreateCompatibleDC", w.HDC, w.HDC)
CreateDIBSection = bind(
    gdi32, "CreateDIBSection", w.HBITMAP, w.HDC, c.POINTER(BITMAPINFO),
    w.UINT, c.POINTER(c.c_void_p), w.HANDLE, w.DWORD,
)
SelectObject = bind(gdi32, "SelectObject", w.HGDIOBJ, w.HDC, w.HGDIOBJ)
DeleteObject = bind(gdi32, "DeleteObject", w.BOOL, w.HGDIOBJ)
DeleteDC = bind(gdi32, "DeleteDC", w.BOOL, w.HDC)
PatBlt = bind(gdi32, "PatBlt", w.BOOL, w.HDC, c.c_int, c.c_int, c.c_int, c.c_int, w.DWORD)
GdiFlush = bind(gdi32, "GdiFlush", w.BOOL)
GetDeviceCaps = bind(gdi32, "GetDeviceCaps", c.c_int, w.HDC, c.c_int)
GetDC = bind(user32, "GetDC", w.HDC, w.HWND)
ReleaseDC = bind(user32, "ReleaseDC", c.c_int, w.HWND, w.HDC)

GetTopWindow = bind(user32, "GetTopWindow", w.HWND, w.HWND)
GetWindow = bind(_window_queries, "GetWindow", w.HWND, w.HWND, w.UINT)
_get_gui_thread_info = bind(_window_queries, "GetGUIThreadInfo", w.BOOL, w.DWORD,
                            c.POINTER(GUIThreadInfo))
GetWindowLongW = bind(user32, "GetWindowLongW", w.LONG, w.HWND, c.c_int)
GetWindowRect = bind(user32, "GetWindowRect", w.BOOL, w.HWND, c.POINTER(w.RECT))
GetWindowRgnBox = bind(user32, "GetWindowRgnBox", c.c_int, w.HWND, c.POINTER(w.RECT))
GetClassNameW = bind(user32, "GetClassNameW", c.c_int, w.HWND, w.LPWSTR, c.c_int)
FindWindowExW = bind(user32, "FindWindowExW", w.HWND, w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR)
IsWindowVisible = bind(_window_queries, "IsWindowVisible", w.BOOL, w.HWND)
IsIconic = bind(user32, "IsIconic", w.BOOL, w.HWND)
EnumDisplayMonitors = bind(
    user32, "EnumDisplayMonitors", w.BOOL, w.HDC, c.POINTER(w.RECT),
    MONITORENUMPROC, w.LPARAM,
)
GetMonitorInfoW = bind(user32, "GetMonitorInfoW", w.BOOL, w.HANDLE, c.POINTER(MONITORINFO))
GetDpiForWindow = getattr(user32, "GetDpiForWindow", None)
if GetDpiForWindow is not None:
    GetDpiForWindow = bind(user32, "GetDpiForWindow", w.UINT, w.HWND)
SHAppBarMessage = bind(shell32, "SHAppBarMessage", c.c_size_t, w.DWORD, c.POINTER(APPBARDATA))
DwmGetWindowAttribute = bind(
    dwmapi, "DwmGetWindowAttribute", w.HRESULT, w.HWND, w.DWORD, c.c_void_p, w.DWORD,
)

DPI_PER_MONITOR_AWARE = -3
DPI_PER_MONITOR_AWARE_V2 = -4
PROCESS_PER_MONITOR_DPI_AWARE = 2

GW_HWNDNEXT, GW_CHILD = 2, 5
GWL_STYLE = -16
GWL_EXSTYLE = -20
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
WS_MINIMIZE = 0x20000000
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

WM_MOUSEACTIVATE = 0x0021
WM_SETCURSOR = 0x0020
WM_NCHITTEST = 0x0084
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MA_NOACTIVATE = 3
HTTRANSPARENT = -1
HTCLIENT = 1
HWND_TOPMOST = -1
SW_HIDE = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

DIB_RGB_COLORS = 0
BLACKNESS = 0x00000042
ULW_ALPHA = 2
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
HGDI_ERROR = c.c_void_p(-1).value
ABM_GETAUTOHIDEBAREX = 0x0000000B
ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM = range(4)
AUTOHIDE_REVEAL_STRIP = 2
WINDOWGLASS_REFRESH_SECONDS = 5.0
VREFRESH = 116
GUI_INMOVESIZE = 0x0002

_OVERLAY_CLASS = "SnowEffect.LayeredOverlay"
_TASKBAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")
_XAML_BRIDGE_CLASS = "Windows.UI.Composition.DesktopWindowContentBridge"
_WINDHAWK_STYLER_KEY = r"SOFTWARE\Windhawk\Engine\Mods\windows-11-taskbar-styler"
_SHELL_WINDOW_CLASSES = {
    "Progman", "WorkerW", *_TASKBAR_CLASSES,
    "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland",
    "#32768",  # Win32 pop-up menu
    _OVERLAY_CLASS,
}


def window_move_active(info):
    """Check the foreground window's native move or resize loop without a hook."""
    return bool(_get_gui_thread_info(0, c.byref(info)) and (info.flags & GUI_INMOVESIZE))


def display_refresh_hz():
    """Return the primary desktop refresh rate, using 60 Hz when unavailable."""
    dc = GetDC(None)
    if not dc:
        return 60
    try:
        hz = GetDeviceCaps(dc, VREFRESH)
    finally:
        ReleaseDC(None, dc)
    return hz if hz > 1 else 60


def enable_dpi_awareness():
    """Set physical per-monitor coordinates before creating windows on this thread."""
    set_process_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if set_process_context is not None:
        set_process_context = bind(user32, "SetProcessDpiAwarenessContext", w.BOOL, w.HANDLE)
        if set_process_context(DPI_PER_MONITOR_AWARE_V2):
            return

    # An executable manifest may already have set the process default.
    set_thread_context = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if set_thread_context is not None:
        set_thread_context = bind(user32, "SetThreadDpiAwarenessContext", w.HANDLE, w.HANDLE)
        if set_thread_context(DPI_PER_MONITOR_AWARE_V2):
            return
        if set_thread_context(DPI_PER_MONITOR_AWARE):
            return

    # Fallback for the earliest Windows 10 releases.
    set_process_awareness = bind(shcore, "SetProcessDpiAwareness", w.HRESULT, c.c_int)
    if set_process_awareness(PROCESS_PER_MONITOR_DPI_AWARE) < 0:
        get_process_awareness = bind(
            shcore, "GetProcessDpiAwareness", w.HRESULT, w.HANDLE, c.POINTER(c.c_int),
        )
        awareness = c.c_int()
        if get_process_awareness(None, c.byref(awareness)) < 0 or awareness.value != PROCESS_PER_MONITOR_DPI_AWARE:
            raise OSError("Could not enable per-monitor DPI awareness")


def monitor_layout():
    """Return (monitor rectangle, work rectangle) pairs in physical desktop pixels."""
    layout = []

    def coordinates(rect):
        return rect.left, rect.top, rect.right, rect.bottom

    @MONITORENUMPROC
    def collect(monitor, _dc, bounds, _data):
        rect = bounds.contents
        if rect.left < rect.right and rect.top < rect.bottom:
            monitor_rect = coordinates(rect)
            info = MONITORINFO(cbSize=c.sizeof(MONITORINFO))
            work_rect = coordinates(info.rcWork) if GetMonitorInfoW(monitor, c.byref(info)) else monitor_rect
            layout.append((monitor_rect, work_rect))
        return True

    if not EnumDisplayMonitors(None, None, collect, 0):
        raise c.WinError(c.get_last_error())
    return sorted(dict(layout).items())


def list_monitors():
    """Return monitor rectangles (left, top, right, bottom) in physical pixels."""
    return [monitor for monitor, _ in monitor_layout()]


def list_work_areas():
    """Return taskbar-excluding monitor work rectangles in monitor order."""
    return [work_area for _, work_area in monitor_layout()]


def _overlap_area(first, second):
    left, top, right, bottom = first
    other_left, other_top, other_right, other_bottom = second
    return (max(0, min(right, other_right) - max(left, other_left))
            * max(0, min(bottom, other_bottom) - max(top, other_top)))


def _taskbar_edge(screen, rect):
    left, top, right, bottom = screen
    x1, y1, x2, y2 = rect
    if x2 - x1 >= y2 - y1:
        return ABE_TOP if abs(y1 - top) < abs(y2 - bottom) else ABE_BOTTOM
    return ABE_LEFT if abs(x1 - left) < abs(x2 - right) else ABE_RIGHT


def _reserve_bar(work, screen, edge, rect, strip=0):
    """Trim one monitor edge, including the hidden appbar's reveal strip."""
    left, top, right, bottom = work
    sl, st, sr, sb = screen
    x1, y1, x2, y2 = rect if rect is not None else (sl, st, sr, sb)
    intersects = rect is not None and _overlap_area(screen, rect)
    if edge == ABE_LEFT:
        left = min(right, max(left, sl + strip, x2 if intersects else sl))
    elif edge == ABE_TOP:
        top = min(bottom, max(top, st + strip, y2 if intersects else st))
    elif edge == ABE_RIGHT:
        right = max(left, min(right, sr - strip, x1 if intersects else sr))
    else:
        bottom = max(top, min(bottom, sb - strip, y1 if intersects else sb))
    return left, top, right, bottom


def _windowglass_radius():
    """Return WindowGlass's radius in DIPs if the installed Windhawk style is active."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _WINDHAWK_STYLER_KEY) as mod:
            try:
                if winreg.QueryValueEx(mod, "Disabled")[0]:
                    return None
            except FileNotFoundError:
                pass
            with winreg.OpenKey(mod, "Settings") as settings:
                if winreg.QueryValueEx(settings, "theme")[0] != "WindowGlass":
                    return None
                for i in range(winreg.QueryInfoKey(settings)[1]):
                    name, value, _ = winreg.EnumValue(settings, i)
                    if (name.startswith("styleConstants[") and isinstance(value, str)
                            and value.startswith("CornerRadius=")):
                        try:
                            return min(100, max(0, float(value.split("=", 1)[1])))
                        except ValueError:
                            break
                return 25
    except OSError:
        return None


def _window_rect(hwnd):
    bounds = w.RECT()
    if GetWindowRect(hwnd, c.byref(bounds)):
        return bounds.left, bounds.top, bounds.right, bounds.bottom
    return None


def _windowglass_spans(handles):
    """Read just taskbar frame/control geometry from the shell's XAML accessibility tree."""
    ole32 = c.WinDLL("ole32", winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
    oleaut32 = c.WinDLL("oleaut32", winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
    initialize = bind(ole32, "CoInitializeEx", w.HRESULT, c.c_void_p, w.DWORD)
    uninitialize = bind(ole32, "CoUninitialize", None)
    create = bind(ole32, "CoCreateInstance", w.HRESULT, c.c_void_p, c.c_void_p,
                  w.DWORD, c.c_void_p, c.POINTER(c.c_void_p))
    free_bstr = bind(oleaut32, "SysFreeString", None, c.c_void_p)
    set_dpi = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if set_dpi is not None:
        bind(user32, "SetThreadDpiAwarenessContext", w.HANDLE, w.HANDLE)(DPI_PER_MONITOR_AWARE_V2)

    def method(obj, slot, result, *args):
        table = c.cast(obj, c.POINTER(c.POINTER(c.c_void_p))).contents
        return c.WINFUNCTYPE(result, c.c_void_p, *args)(table[slot])

    def pointer(obj, slot, *args):
        output = c.c_void_p()
        types = (c.c_void_p,) * len(args) + (c.POINTER(c.c_void_p),)
        if method(obj, slot, w.HRESULT, *types)(obj, *args, c.byref(output)) >= 0:
            return output
        return c.c_void_p()

    def release(obj):
        if obj:
            method(obj, 2, w.ULONG)(obj)

    def measure(walker, element, frames, tray, depth=0):
        # UIAutomationClient.h vtable slots, including the three IUnknown methods.
        class_name = pointer(element, 30)  # IUIAutomationElement::get_CurrentClassName
        try:
            name = c.wstring_at(class_name) if class_name else ""
        finally:
            if class_name:
                free_bstr(class_name)
        if name == "Taskbar.TaskbarFrameAutomationPeer" or name.startswith("SystemTray."):
            rect = w.RECT()
            if method(element, 43, w.HRESULT, c.POINTER(w.RECT))(element, c.byref(rect)) >= 0:
                if rect.left < rect.right and rect.top < rect.bottom:
                    target = tray if name.startswith("SystemTray.") else frames
                    target.append(rect)
            return
        if depth < 3:
            child = pointer(walker, 4, element)  # GetFirstChildElement
            for _ in range(100):
                if not child:
                    break
                try:
                    measure(walker, child, frames, tray, depth + 1)
                    following = pointer(walker, 6, child)  # GetNextSiblingElement
                finally:
                    release(child)
                child = following
            else:
                release(child)

    if initialize(None, 0) < 0:  # COINIT_MULTITHREADED
        return {}
    automation = c.c_void_p()
    walker = None
    try:
        clsid = (c.c_ubyte * 16).from_buffer_copy(UUID("ff48dba4-60ef-4201-aa87-54103eef594e").bytes_le)
        iid = (c.c_ubyte * 16).from_buffer_copy(UUID("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee").bytes_le)
        if create(clsid, None, 1, iid, c.byref(automation)) < 0 or not automation:
            return {}
        walker = pointer(automation, 16)  # IUIAutomation::get_RawViewWalker
        if not walker:
            return {}
        spans = {}
        class_name = c.create_unicode_buffer(256)
        for hwnd in handles:
            rect = _window_rect(hwnd)
            if rect is None:
                continue
            left, top, right, bottom = rect
            if right - left <= bottom - top:
                continue
            child = GetWindow(hwnd, GW_CHILD)
            for _ in range(64):
                if not child:
                    break
                if (GetClassNameW(child, class_name, len(class_name))
                        and class_name.value == _XAML_BRIDGE_CLASS):
                    root = pointer(automation, 6, child)  # ElementFromHandle
                    if root:
                        frames, tray = [], []
                        try:
                            measure(walker, root, frames, tray)
                        finally:
                            release(root)
                        if frames and tray:
                            start = min(frame.left for frame in frames) - left
                            end = max(control.right for control in tray) - left
                            if 0 <= start < end <= right - left:
                                spans[hwnd] = start, end
                    break
                child = GetWindow(child, GW_HWNDNEXT)
        return spans
    finally:
        release(walker)
        release(automation)
        uninitialize()


class TaskbarTracker:
    """Use live taskbar positions because rcWork stays full-sized in auto-hide mode."""

    def __init__(self):
        self._registered = {}
        self._native = ()
        self.handles = ()
        self._glass_radius = None
        self._glass_spans = {}
        self._geometry_reader = None
        self._next_glass_refresh = 0.0

    def refresh(self, monitors):
        registered = {}
        for monitor in monitors:
            data = APPBARDATA(cbSize=c.sizeof(APPBARDATA), rc=w.RECT(*monitor))
            for edge in (ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM):
                data.uEdge = edge
                hwnd = SHAppBarMessage(ABM_GETAUTOHIDEBAREX, c.byref(data))
                if hwnd:
                    registered[hwnd] = monitor, edge

        native = {}
        for class_name in _TASKBAR_CLASSES:
            hwnd = FindWindowExW(None, None, class_name, None)
            seen = set()
            while hwnd and hwnd not in seen:
                seen.add(hwnd)
                native[hwnd] = None
                hwnd = FindWindowExW(None, hwnd, class_name, None)
        handles = tuple(native)
        radius = _windowglass_radius()
        if handles != self._native or radius != self._glass_radius:
            self._next_glass_refresh = 0.0
        self._registered = registered
        self._native = handles
        self.handles = tuple(dict.fromkeys((*registered, *native)))
        self._glass_radius = radius
        if radius is None or not handles:
            return
        now = monotonic()
        missing_width = any(self._glass_spans.get(hwnd) is None for hwnd in handles)
        if ((missing_width or now >= self._next_glass_refresh)
                and (self._geometry_reader is None or not self._geometry_reader.is_alive())):
            # Only the horizontal span needs UI Automation. Platforms still read the
            # taskbar window rectangle on every frame to follow its live reveal.
            self._next_glass_refresh = now + WINDOWGLASS_REFRESH_SECONDS
            self._geometry_reader = Thread(target=self._read_glass, args=(handles,), daemon=True)
            self._geometry_reader.start()

    def _read_glass(self, handles):
        try:
            spans = _windowglass_spans(handles)
        except (OSError, ValueError):
            return
        if handles == self._native:
            self._glass_spans = {hwnd: spans.get(hwnd, self._glass_spans.get(hwnd))
                                for hwnd in handles}

    def desktop_areas(self, base_layout):
        """Reserve only the activation strip for auto-hidden taskbars."""
        layout = dict(base_layout)
        for screen, edge in self._registered.values():
            if screen in layout:
                layout[screen] = _reserve_bar(layout[screen], screen, edge, None, AUTOHIDE_REVEAL_STRIP)
        return layout

    def platforms(self, base_layout):
        """Return live (left, top, right, bottom, radius) taskbar shapes by monitor."""
        layout = {screen: [] for screen in base_layout}
        if not layout:
            return {}
        glass_spans = self._glass_spans if self._glass_radius is not None else {}
        region = w.RECT()
        for hwnd in self.handles:
            if not IsWindowVisible(hwnd):
                continue
            rect = _window_rect(hwnd)
            if rect is None:
                continue
            left, top, right, bottom = rect
            registered = self._registered.get(hwnd)
            screen = registered[0] if registered else max(layout, key=lambda item: _overlap_area(item, rect))
            if screen not in layout or not _overlap_area(screen, rect):
                continue
            edge = registered[1] if registered else _taskbar_edge(screen, rect)
            radius = 0
            floating = self._glass_radius is not None and hwnd in self._native and edge in (ABE_TOP, ABE_BOTTOM)
            glass = glass_spans.get(hwnd) if floating else None
            if glass is not None:
                scale = (GetDpiForWindow(hwnd) or 96) / 96 if GetDpiForWindow else 1
                inset = int(2 * scale + 0.5)
                shape = (left + glass[0], top + inset,
                         min(right, left + glass[1] + int(8 * scale + 0.5)), bottom - inset)
                radius = min(int(self._glass_radius * scale + 0.5),
                             (shape[2] - shape[0]) / 2, (shape[3] - shape[1]) / 2)
            else:
                if floating and self._geometry_reader is not None and self._geometry_reader.is_alive():
                    # A new bridge is still being measured. Avoid flashing a full-width ledge.
                    continue
                region_type = GetWindowRgnBox(hwnd, c.byref(region))
                if region_type == 1:  # NULLREGION
                    continue
                shape = rect
                if region_type > 1:
                    shape = (left + region.left, top + region.top,
                             left + region.right, top + region.bottom)
            if _overlap_area(screen, shape):
                layout[screen].append((*shape, radius))
        return {screen: tuple(shapes) for screen, shapes in layout.items()}

    def work_areas(self, base_layout):
        layout = dict(base_layout)
        bounds = w.RECT()
        for hwnd in self.handles:
            rect = None
            if IsWindowVisible(hwnd) and GetWindowRect(hwnd, c.byref(bounds)):
                rect = (bounds.left, bounds.top, bounds.right, bounds.bottom)
            registered = self._registered.get(hwnd)
            if registered is not None:
                screen, edge = registered
                if screen in layout:
                    layout[screen] = _reserve_bar(layout[screen], screen, edge, rect, AUTOHIDE_REVEAL_STRIP)
            elif rect is not None and layout:
                screen = max(layout, key=lambda monitor: _overlap_area(monitor, rect))
                if _overlap_area(screen, rect):
                    edge = _taskbar_edge(screen, rect)
                    layout[screen] = _reserve_bar(layout[screen], screen, edge, rect)
        return layout


def list_windows(exclude_hwnds=()):
    """Return (HWND, left, top, right, bottom) for app windows, front to back."""
    excluded = set(exclude_hwnds)
    order = []
    seen = set()
    hwnd = GetTopWindow(None)
    # The Z order can change while it is read. Stop if it leads back to a seen HWND.
    while hwnd and hwnd not in seen:
        seen.add(hwnd)
        order.append(hwnd)
        hwnd = GetWindow(hwnd, GW_HWNDNEXT)

    windows = []
    class_name = c.create_unicode_buffer(256)
    bounds = w.RECT()
    cloaked = w.DWORD()
    for hwnd in order:
        if hwnd in excluded or not IsWindowVisible(hwnd):
            continue
        style = GetWindowLongW(hwnd, GWL_STYLE)
        if style & (WS_CHILD | WS_MINIMIZE) or (not style and IsIconic(hwnd)):
            continue
        ex_style = GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex_style & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) and not ex_style & WS_EX_APPWINDOW:
            continue
        if not GetClassNameW(hwnd, class_name, len(class_name)) or class_name.value in _SHELL_WINDOW_CLASSES:
            continue
        result = DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, c.byref(cloaked), c.sizeof(cloaked))
        if result >= 0 and cloaked.value:
            continue
        result = DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, c.byref(bounds), c.sizeof(bounds))
        if result < 0 and not GetWindowRect(hwnd, c.byref(bounds)):
            continue
        if bounds.left < bounds.right and bounds.top < bounds.bottom:
            windows.append((hwnd, bounds.left, bounds.top, bounds.right, bounds.bottom))
    return windows


_overlay_input = {}
_load_cursor = bind(user32, "LoadCursorW", HCURSOR, w.HINSTANCE, c.c_void_p)
_set_cursor = bind(user32, "SetCursor", HCURSOR, HCURSOR)
_hand_cursor = _load_cursor(None, c.c_void_p(32649))  # IDC_HAND


@WNDPROC
def _overlay_proc(hwnd, message, wparam, lparam):
    mouse = _overlay_input.get(hwnd)
    if message == WM_NCHITTEST:
        return HTCLIENT if mouse else HTTRANSPARENT
    if message == WM_MOUSEACTIVATE:
        return MA_NOACTIVATE
    if mouse:
        if message == WM_SETCURSOR and _hand_cursor:
            _set_cursor(_hand_cursor)
            return 1
        if message in (WM_LBUTTONDOWN, WM_LBUTTONUP):
            callback, left, top = mouse
            x = left + c.c_short(lparam & 0xFFFF).value
            y = top + c.c_short((lparam >> 16) & 0xFFFF).value
            callback(message == WM_LBUTTONDOWN, x, y)
            return 0
    return DefWindowProcW(hwnd, message, wparam, lparam)


_overlay_class_atom = None


def _register_overlay(instance):
    global _overlay_class_atom
    if _overlay_class_atom is None:
        window_class = WNDCLASSW(
            lpfnWndProc=_overlay_proc, hInstance=instance, lpszClassName=_OVERLAY_CLASS,
        )
        _overlay_class_atom = RegisterClassW(c.byref(window_class))
        if not _overlay_class_atom:
            _overlay_class_atom = None
            raise c.WinError(c.get_last_error())


class Overlay:
    """Own a layered HWND and a writable premultiplied BGRA32 DIB."""

    def __init__(self, rect, mouse=None):
        self.left, self.top, right, bottom = rect
        self.width = right - self.left
        self.height = bottom - self.top
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Overlay rectangle must have positive dimensions")

        self.hwnd = self.address = None
        self._dc = self._dib = self._old_bitmap = None
        self._position = w.POINT(self.left, self.top)
        self._committed_position = self.left, self.top
        self._size = w.SIZE(self.width, self.height)
        self._origin = w.POINT(0, 0)
        self._blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        instance = GetModuleHandleW(None)
        _register_overlay(instance)
        try:
            style = WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            if mouse is None:
                style |= WS_EX_TRANSPARENT
            self.hwnd = CreateWindowExW(
                style, _OVERLAY_CLASS, APP_NAME, WS_POPUP, self.left, self.top,
                self.width, self.height, None, None, instance, None,
            )
            if not self.hwnd:
                raise c.WinError(c.get_last_error())
            if mouse is not None:
                _overlay_input[self.hwnd] = (mouse, self.left, self.top)
            self._dc = CreateCompatibleDC(None)
            if not self._dc:
                raise c.WinError(c.get_last_error())
            bitmap_info = BITMAPINFO()
            bitmap_info.bmiHeader = BITMAPINFOHEADER(
                biSize=c.sizeof(BITMAPINFOHEADER), biWidth=self.width, biHeight=-self.height,
                biPlanes=1, biBitCount=32,
            )
            pixels = c.c_void_p()
            self._dib = CreateDIBSection(
                self._dc, c.byref(bitmap_info), DIB_RGB_COLORS, c.byref(pixels), None, 0,
            )
            if not self._dib or not pixels.value:
                raise c.WinError(c.get_last_error())
            old_bitmap = SelectObject(self._dc, self._dib)
            if not old_bitmap or old_bitmap == HGDI_ERROR:
                raise c.WinError(c.get_last_error())
            self._old_bitmap = old_bitmap
            self.address = pixels.value
            c.memset(self.address, 0, self.width * self.height * 4)
            self._dirty = w.RECT()
            self._update_info = UPDATELAYEREDWINDOWINFO(
                cbSize=c.sizeof(UPDATELAYEREDWINDOWINFO), pptDst=c.pointer(self._position),
                psize=c.pointer(self._size), hdcSrc=self._dc, pptSrc=c.pointer(self._origin),
                pblend=c.pointer(self._blend), dwFlags=ULW_ALPHA, prcDirty=c.pointer(self._dirty),
            )
        except BaseException:
            self.close()
            raise

    def clear_bands(self, bands):
        """Zero exact world-coordinate rectangles in the DIB, including alpha."""
        complete = all(
            PatBlt(self._dc, left - self.left, top - self.top,
                   right - left, bottom - top, BLACKNESS)
            for left, top, right, bottom in bands
        )
        # The rasterizer accesses DIB memory directly after this call.
        return bool(GdiFlush()) and complete

    def present(self, dirty=None):
        """Copy the DIB to the desktop compositor without taking input focus."""
        updated = False
        if dirty is not None and dirty != (0, 0, self.width, self.height):
            self._dirty.left, self._dirty.top, self._dirty.right, self._dirty.bottom = dirty
            updated = UpdateLayeredWindowIndirect(self.hwnd, c.byref(self._update_info))
        if not updated and not UpdateLayeredWindow(
            self.hwnd, None, c.byref(self._position), c.byref(self._size), self._dc,
            c.byref(self._origin), 0, c.byref(self._blend), ULW_ALPHA,
        ):
            raise c.WinError(c.get_last_error())
        self._committed_position = self._position.x, self._position.y

    def set_destination(self, x, y):
        """Set the screen position for the next present, keeping DIB coordinates local."""
        self._position.x, self._position.y = x, y

    def move_to_destination(self):
        """Commit a staged position when the compositor already has the right pixels."""
        destination = self._position.x, self._position.y
        if destination == self._committed_position:
            return
        if not UpdateLayeredWindow(self.hwnd, None, c.byref(self._position),
                                   None, None, None, 0, None, 0):
            raise c.WinError(c.get_last_error())
        self._committed_position = destination

    def show(self):
        """Show or raise the overlay without activating it."""
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        if not SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags):
            raise c.WinError(c.get_last_error())

    def hide(self):
        if self.hwnd:
            ShowWindow(self.hwnd, SW_HIDE)

    def close(self):
        if self.hwnd:
            _overlay_input.pop(self.hwnd, None)
            DestroyWindow(self.hwnd)
            self.hwnd = None
        if self._old_bitmap:
            SelectObject(self._dc, self._old_bitmap)
            self._old_bitmap = None
        if self._dib:
            DeleteObject(self._dib)
            self._dib = self.address = None
        if self._dc:
            DeleteDC(self._dc)
            self._dc = None
