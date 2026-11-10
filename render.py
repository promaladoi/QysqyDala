"""Render antialiased snow into premultiplied BGRA overlay bitmaps."""

import ctypes as ct
from array import array
from collections import OrderedDict
from ctypes import wintypes as wt
from itertools import chain
from math import ceil, cos, floor, sin

from simulation import DriftPolygon
from snowman import FLYING_BALL_LAYER
from snowplow import PLOW_SCALE, SCOOP_EDGE, VEHICLE_HEIGHT, VEHICLE_LENGTH
from win32 import LOAD_LIBRARY_SEARCH_SYSTEM32, Overlay, bind

gdiplus = ct.WinDLL("gdiplus", winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
Handle = ct.c_void_p
HandleOut = ct.POINTER(Handle)


class StartupInput(ct.Structure):
    _fields_ = [
        ("version", wt.UINT),
        ("debug_callback", Handle),
        ("suppress_thread", wt.BOOL),
        ("suppress_codecs", wt.BOOL),
    ]


class PointF(ct.Structure):
    _fields_ = [("x", ct.c_float), ("y", ct.c_float)]


class RectI(ct.Structure):
    _fields_ = [("x", ct.c_int), ("y", ct.c_int), ("width", ct.c_int), ("height", ct.c_int)]


startup = bind(gdiplus, "GdiplusStartup", ct.c_int, ct.POINTER(ct.c_size_t),
               ct.POINTER(StartupInput), Handle)
shutdown = bind(gdiplus, "GdiplusShutdown", None, ct.c_size_t)
create_bitmap = bind(gdiplus, "GdipCreateBitmapFromScan0", ct.c_int, ct.c_int,
                     ct.c_int, ct.c_int, ct.c_int, Handle, HandleOut)
dispose_bitmap = bind(gdiplus, "GdipDisposeImage", ct.c_int, Handle)
create_graphics = bind(gdiplus, "GdipGetImageGraphicsContext", ct.c_int, Handle, HandleOut)
delete_graphics = bind(gdiplus, "GdipDeleteGraphics", ct.c_int, Handle)
set_smoothing = bind(gdiplus, "GdipSetSmoothingMode", ct.c_int, Handle, ct.c_int)
translate_graphics = bind(gdiplus, "GdipTranslateWorldTransform", ct.c_int, Handle,
                          ct.c_float, ct.c_float, ct.c_int)
scale_graphics = bind(gdiplus, "GdipScaleWorldTransform", ct.c_int, Handle,
                      ct.c_float, ct.c_float, ct.c_int)
create_region = bind(gdiplus, "GdipCreateRegionRectI", ct.c_int, ct.POINTER(RectI), HandleOut)
clone_region = bind(gdiplus, "GdipCloneRegion", ct.c_int, Handle, HandleOut)
delete_region = bind(gdiplus, "GdipDeleteRegion", ct.c_int, Handle)
exclude_rect = bind(gdiplus, "GdipCombineRegionRectI", ct.c_int, Handle, ct.POINTER(RectI), ct.c_int)
exclude_path = bind(gdiplus, "GdipCombineRegionPath", ct.c_int, Handle, Handle, ct.c_int)
get_region_bounds = bind(gdiplus, "GdipGetRegionBoundsI", ct.c_int, Handle, Handle, ct.POINTER(RectI))
set_clip_region = bind(gdiplus, "GdipSetClipRegion", ct.c_int, Handle, Handle, ct.c_int)
create_path = bind(gdiplus, "GdipCreatePath", ct.c_int, ct.c_int, HandleOut)
add_path_arc = bind(gdiplus, "GdipAddPathArc", ct.c_int, Handle, ct.c_float,
                    ct.c_float, ct.c_float, ct.c_float, ct.c_float, ct.c_float)
close_path_figure = bind(gdiplus, "GdipClosePathFigure", ct.c_int, Handle)
delete_path = bind(gdiplus, "GdipDeletePath", ct.c_int, Handle)
flush_graphics = bind(gdiplus, "GdipFlush", ct.c_int, Handle, ct.c_int)
create_brush = bind(gdiplus, "GdipCreateSolidFill", ct.c_int, wt.DWORD, HandleOut)
delete_brush = bind(gdiplus, "GdipDeleteBrush", ct.c_int, Handle)
fill_ellipse = bind(gdiplus, "GdipFillEllipse", ct.c_int, Handle, Handle,
                    ct.c_float, ct.c_float, ct.c_float, ct.c_float)
fill_polygon = bind(gdiplus, "GdipFillPolygon", ct.c_int, Handle, Handle,
                     ct.POINTER(PointF), ct.c_int, ct.c_int)
draw_bitmap = bind(gdiplus, "GdipDrawImageRectRectI", ct.c_int, Handle, Handle,
                   ct.c_int, ct.c_int, ct.c_int, ct.c_int,
                   ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_int,
                   Handle, Handle, Handle)
draw_bitmap_points = bind(gdiplus, "GdipDrawImagePointsRect", ct.c_int, Handle, Handle,
                          ct.POINTER(PointF), ct.c_int, ct.c_float, ct.c_float,
                          ct.c_float, ct.c_float, ct.c_int, Handle, Handle, Handle)

PIXEL_FORMAT_32BPP_PARGB = 0x000E200B
UNIT_PIXEL = 2
SMOOTHING_ANTIALIAS = 4
FLUSH_SYNC = 1
FILL_ALTERNATE = 0
COMBINE_REPLACE = 0
COMBINE_INTERSECT = 1
COMBINE_UNION = 2
COMBINE_EXCLUDE = 4
DRIFT_COLOR = 0xF0F3F9FF
FLAKE_RGB = 0xF2F8FF
SNOW_SHADOW = 0xFFD2E5F6
SNOW_LIGHT = 0xFFF5FBFF
BALL_EDGE = 0xFF4789BB
COAL_COLOR = 0xFF263A52
CARROT_COLOR = 0xFFF28A33
COSTUME_CACHE_LIMIT = 48
MAX_COSTUME_TILE_SIDE = 192
CLASSIC_COAL = 0xFF171B22
HAT_LIGHT = 0xFF374451
SCARF_RED = 0xFFD33743
SCARF_SHADE = 0xFF922333
SCARF_LIGHT = 0xFFF1686C
TWIG_DARK = 0xFF76492F
TWIG_LIGHT = 0xFFB67C4E
CLASSIC_HAT = (
    (CLASSIC_COAL, ((-.63, -.78), (-.71, -1.54), (-.62, -1.66), (.62, -1.66),
                    (.71, -1.54), (.63, -.78))),
    (HAT_LIGHT, ((-.60, -1.54), (-.54, -1.60), (.53, -1.60), (.60, -1.54),
                 (.52, -1.49), (-.53, -1.49))),
    (SCARF_RED, ((-.65, -1.05), (.65, -1.05), (.63, -.79), (-.63, -.79))),
    (SCARF_LIGHT, ((-.62, -1.02), (.62, -1.02), (.62, -.97), (-.62, -.97))),
    (CLASSIC_COAL, ((-1.03, -.81), (-.92, -.90), (.92, -.90), (1.03, -.81),
                    (.95, -.70), (-.95, -.70))),
    (HAT_LIGHT, ((-.89, -.85), (.88, -.85), (.92, -.80), (-.93, -.80))),
)
CLASSIC_SCARF = (
    (SCARF_SHADE, ((.34, .65), (.62, .61), (.94, 1.37), (.79, 1.54),
                   (.69, 1.46), (.60, 1.53), (.50, 1.44), (.28, .85))),
    (SCARF_RED, ((.43, .71), (.58, .71), (.84, 1.36), (.76, 1.46), (.68, 1.38),
                 (.62, 1.46), (.56, 1.38))),
    (SCARF_RED, ((-.75, .62), (-.58, .54), (.55, .54), (.73, .62), (.76, .76),
                 (.63, .90), (-.60, .88), (-.75, .77))),
    (SCARF_SHADE, ((-.67, .77), (.67, .77), (.61, .86), (-.60, .86))),
    (SCARF_LIGHT, ((-.61, .60), (.56, .60), (.65, .66), (-.62, .66))),
)
CLASSIC_TWIG = (
    (TWIG_DARK, ((.91, -.14), (1.31, -.31), (1.73, -.61), (1.80, -.56),
                 (1.39, -.22), (.94, -.06))),
    (TWIG_DARK, ((1.45, -.37), (1.50, -.68), (1.58, -.75), (1.56, -.46))),
    (TWIG_DARK, ((1.56, -.43), (1.83, -.39), (1.91, -.45), (1.62, -.51))),
    (TWIG_LIGHT, ((1.10, -.17), (1.40, -.29), (1.65, -.48), (1.41, -.25))),
)
CLASSIC_CARROT = (
    (CARROT_COLOR, ((-.12, -.04), (.89, .18), (-.10, .27), (-.17, .13))),
    (0xFFCA6124, ((-.12, .18), (.89, .18), (-.10, .27))),
)
SHOVEL_HANDLE = 0xFFC89661
SHOVEL_BLADE = 0xFF6BA9CE
SHOVEL_EDGE = 0xFFDAF0FC
SHOVEL_SHAPES = (
    (COAL_COLOR, ((-23, -31), (-19, -34), (1, -11), (-3, -8))),
    (SHOVEL_HANDLE, ((-21, -31), (-19, -32), (-1, -11), (-3, -10))),
    (COAL_COLOR, ((-25, -31), (-20, -37), (-16, -34), (-21, -28))),
    (SHOVEL_BLADE, ((-23, -31), (-20, -35), (-18, -34), (-21, -30))),
    (COAL_COLOR, ((-9, -13), (3, -12), (11, -8), (13, -5), (11, -1),
                  (7, 0), (-7, 0), (-10, -3), (-11, -9))),
    (SHOVEL_BLADE, ((-8, -11), (3, -10), (10, -7), (11, -5), (9, -2),
                    (-6, -2), (-8, -4), (-9, -9))),
    (SHOVEL_EDGE, ((-6, -4), (10, -4), (8, -2), (-5, -2))),
)

PLOW_FILL_STEPS = 16
PLOW_INK = 0xFF162536
PLOW_WHITE = 0xFFF1F7FC
PLOW_CYAN = 0xFF44DBEA
PLOW_SHELL = (
    # Chassis and the open, rear-mounted hopper.
    (PLOW_INK, ((8, 42), (91, 43), (94, 51), (90, 58), (9, 58), (6, 53))),
    (0xFF1C7486, ((0, 27), (6, 20), (41, 20), (45, 25), (39, 43), (12, 47), (3, 37))),
    (0xFF31B7C9, ((1, 30), (41, 30), (38, 41), (23, 41), (13, 45), (5, 37))),
    (0xFF75E1E9, ((5, 30), (7, 30), (16, 43), (14, 44))),
    (PLOW_WHITE, ((0, 29), (3, 23), (8, 19), (39, 19), (44, 22), (43, 29))),
    (0xFF597D91, ((4, 25), (8, 21), (38, 21), (41, 23), (39, 26), (6, 26))),
    (0xFFA8EAF0, ((0, 29), (43, 29), (42, 31), (1, 31))),
    # White fenders and the swept, dark-glass cab.
    (0xFF8196AA, ((9, 46), (17, 39), (30, 38), (36, 36), (42, 13), (46, 8), (52, 6),
                  (70, 6), (76, 9), (83, 17), (89, 29), (92, 38), (92, 49),
                  (85, 54), (80, 54), (76, 48), (72, 45), (66, 45), (60, 49), (57, 54),
                  (36, 54), (32, 48), (27, 45), (21, 45), (16, 48), (13, 54), (8, 52))),
    (PLOW_WHITE, ((10, 45), (17, 39), (31, 39), (37, 36), (42, 14), (46, 8), (52, 6),
                  (70, 6), (76, 9), (83, 17), (89, 29), (92, 38), (92, 46), (88, 48),
                  (82, 45), (74, 40), (65, 41), (58, 46), (55, 50), (38, 50),
                  (32, 44), (26, 42), (19, 43), (13, 48), (10, 51))),
    (0xFFD8E6F1, ((14, 46), (20, 42), (28, 41), (36, 44), (52, 47), (60, 42),
                  (69, 40), (80, 43), (86, 47), (87, 50), (81, 50), (75, 45),
                  (66, 44), (59, 49), (56, 52), (37, 52), (31, 46), (25, 44), (20, 45))),
    (PLOW_INK, ((44, 20), (48, 12), (52, 8), (69, 8), (75, 10), (81, 16), (87, 27),
                (90, 36), (77, 36), (52, 31), (46, 28), (44, 25))),
    (0xFF31566E, ((46, 21), (50, 13), (54, 10), (62, 10), (62, 31), (53, 29), (48, 26))),
    (0xFF294E67, ((63, 10), (70, 10), (75, 12), (80, 17), (85, 27), (88, 34), (77, 34), (64, 31))),
    (0xFF54849E, ((74, 11), (78, 14), (66, 31), (63, 30), (63, 27))),
    (0xFF416D89, ((81, 19), (83, 22), (75, 34), (71, 33))),
    (0xFF17384D, ((46, 22), (54, 23), (60, 27), (62, 31), (52, 29), (48, 26))),
    (0xFF078C9E, ((37, 40), (52, 40), (54, 41), (52, 43), (38, 43), (36, 42))),
    (PLOW_CYAN, ((38, 41), (52, 41), (51, 42), (38, 42))),
    # Headlight, rear marker and the amber roof beacon.
    (0xFF165361, ((80, 35), (89, 35), (92, 37), (92, 41), (82, 40))),
    (PLOW_CYAN, ((82, 36), (89, 36), (91, 38), (91, 39), (83, 39))),
    (0xFFE7FFFF, ((84, 37), (89, 37), (90, 38), (84, 38))),
    (0xFFFF975A, ((4, 37), (7, 39), (9, 42), (6, 42))),
    (PLOW_INK, ((56, 5), (67, 5), (68, 7), (55, 7))),
    (0xFFE08315, ((56, 4), (57, 1), (60, 0), (65, 0), (67, 2), (67, 5), (56, 5))),
    (0xFFFFBF43, ((57, 4), (58, 2), (60, 1), (64, 1), (66, 2), (66, 4))),
    (0xFFFFE698, ((59, 2), (64, 2), (65, 3), (59, 3))),
)
PLOW_SCOOP = (
    # Articulated bracket and the curved inner face of the front blade.
    (PLOW_INK, ((87, 44), (92, 44), (103, 54), (102, 59), (96, 58), (86, 49))),
    (0xFF677F96, ((89, 45), (92, 45), (101, 53), (98, 55), (89, 48))),
    (0xFF268EA1, ((91, 55), (92, 48), (95, 43), (100, 39), (106, 38), *SCOOP_EDGE,
                  (101, 64), (95, 62))),
    (0xFF65CFE2, ((93, 55), (94, 49), (97, 43), (102, 40), (106, 39), (122, 39),
                  (119, 44), (117, 49), (118, 54), (121, 59), (125, 63),
                  (102, 63), (97, 61))),
    (0xFF399FB6, ((99, 45), (103, 42), (108, 41), (121, 41), (119, 45), (117, 49), (97, 49))),
    (0xFF79DDEC, ((97, 53), (118, 52), (119, 55), (122, 59), (98, 59), (96, 56))),
    (0xFFC1F5FA, ((92, 55), (93, 49), (96, 44), (101, 40), (106, 38), (121, 38),
                  (123, 39), (122, 40), (106, 40), (101, 42), (98, 45), (96, 49),
                  (95, 54), (97, 59), (101, 63), (98, 63), (94, 60))),
    (0xFFB5EEF4, ((98, 59), (122, 59), (123, 60), (99, 61))),
    (0xFF45AFBE, ((100, 62), (124, 61), (126, 63), (125, 64), (102, 64))),
)


def check(status, operation):
    if status:
        raise RuntimeError(f"{operation} failed (GDI+ status {status})")


def inside_platform(x, y, platform):
    left, top, right, bottom, radius = platform
    if not left <= x < right or not top <= y < bottom:
        return False
    radius = min(radius, (right - left) / 2, (bottom - top) / 2)
    dx = max(left + radius - x, x - (right - radius), 0)
    dy = max(top + radius - y, y - (bottom - radius), 0)
    return dx * dx + dy * dy <= radius * radius


def union_bounds(first, second):
    return (min(first[0], second[0]), min(first[1], second[1]),
            max(first[2], second[2]), max(first[3], second[3]))


def _classic_layout(x, ground_y, radii):
    body, head = radii[-2:]
    body_y = ground_y - 1.8 * sum(radii[:-2]) - body
    head_y = body_y - 0.8 * body - head
    arm_width = max(1.91 * body, 1.03 * head)
    bounds = (x - arm_width, min(head_y - 1.66 * head, body_y - 0.75 * body),
              x + arm_width, max(head_y + 1.54 * head, body_y + 0.70 * body))
    return body_y, head_y, bounds


def figure_bounds(figure):
    """Unclipped footprint of a passive snowman, before antialias padding."""
    x, ground_y, *radii = figure
    radii = tuple(radius for radius in radii if radius > 0)
    width = max(radii, default=0)
    y = top = ground_y
    for radius in radii:
        y -= radius
        top = min(top, y - radius)
        y -= 0.80 * radius
    body = (x - width, top, x + width, ground_y)
    if len(radii) < 3:
        return body
    _, _, classic = _classic_layout(x, ground_y, radii)
    return union_bounds(body, classic)


def box_area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def subtract_box(box, blocker):
    """Return disjoint strips that cover the part of a box outside a blocker."""
    left, top, right, bottom = box
    x1, y1, x2, y2 = blocker
    x1, y1 = max(left, x1), max(top, y1)
    x2, y2 = min(right, x2), min(bottom, y2)
    if x1 >= x2 or y1 >= y2:
        return (box,)
    return tuple(
        strip for strip in ((left, top, right, y1), (left, y1, x1, y2),
                            (x2, y1, right, y2), (left, y2, right, bottom))
        if strip[0] < strip[2] and strip[1] < strip[3]
    )


def merge_bands(rectangles, max_bands=4):
    """Keep distant ledges and snowballs in separate, bounded uploads."""
    bands = []
    for rect in rectangles:
        for index in range(len(bands) - 1, -1, -1):
            other = bands[index]
            if (min(rect[2], other[2]) + 12 >= max(rect[0], other[0])
                    and min(rect[3], other[3]) + 12 >= max(rect[1], other[1])):
                joined = union_bounds(rect, other)
                # Touching strips can surround a large app-window hole. Joining
                # them would upload that whole blank area again.
                if box_area(joined) <= box_area(rect) + box_area(other) + 4096:
                    rect = joined
                    bands.pop(index)
        bands.append(rect)
        if len(bands) > max_bands:
            # Bound the number of compositor calls by joining the cheapest pair.
            area = [box_area(box) for box in bands]
            best = float("inf")
            for i, first in enumerate(bands):
                for j in range(i + 1, len(bands)):
                    joined = union_bounds(first, bands[j])
                    pixels = box_area(joined) - area[i] - area[j]
                    if pixels < best:
                        best, merge_left, merge_right, merged = pixels, i, j, joined
            bands[merge_left] = merged
            bands.pop(merge_right)
    return bands


def _raster_tile(width, height, paint):
    bitmap, graphics = Handle(), Handle()
    pixels = (ct.c_ubyte * (width * height * 4))()
    try:
        check(create_bitmap(width, height, width * 4, PIXEL_FORMAT_32BPP_PARGB, pixels,
                            ct.byref(bitmap)), "GdipCreateBitmapFromScan0")
        try:
            check(create_graphics(bitmap, ct.byref(graphics)), "GdipGetImageGraphicsContext")
            check(set_smoothing(graphics, SMOOTHING_ANTIALIAS), "GdipSetSmoothingMode")
            paint(graphics)
            check(flush_graphics(graphics, FLUSH_SYNC), "GdipFlush")
        finally:
            if graphics:
                delete_graphics(graphics)
    except BaseException:
        if bitmap:
            dispose_bitmap(bitmap)
        raise
    # GDI+ borrows the backing pixels for the life of the bitmap.
    return bitmap, pixels


class PaintEngine:
    def __init__(self):
        self.token = ct.c_size_t()
        self.brushes = {}
        self.flake_brushes = None
        self.costume_tiles = OrderedDict()
        self.plow_tiles = {}
        settings = StartupInput(1, None, False, True)
        check(startup(ct.byref(self.token), ct.byref(settings), None), "GdiplusStartup")

    def brush(self, color):
        if color not in self.brushes:
            brush = Handle()
            check(create_brush(color, ct.byref(brush)), "GdipCreateSolidFill")
            self.brushes[color] = brush
        return self.brushes[color]

    def flake_palette(self):
        if self.flake_brushes is None:
            self.flake_brushes = tuple(
                self.brush((min(255, step * 16) << 24) | FLAKE_RGB) for step in range(17)
            )
        return self.flake_brushes

    def close(self):
        for bitmap, _ in chain(self.costume_tiles.values(), self.plow_tiles.values()):
            dispose_bitmap(bitmap)
        self.costume_tiles.clear()
        self.plow_tiles.clear()
        for brush in self.brushes.values():
            delete_brush(brush)
        self.brushes.clear()
        shutdown(self.token)


class SnowScreen:
    def __init__(self, engine, rect, mouse=None):
        self.engine = engine
        self.rect = rect
        self.bitmap = Handle()
        self.graphics = Handle()
        self.overlay = Overlay(rect, mouse)
        self.interactive = mouse is not None
        self._stationary_scene = None
        self.visible = False
        self._clip_key = None
        self._clips = {}
        self._active_clip = None
        self._flake_occluders = ()
        self._dense_flake_bands = ()
        self._pending_dense_bounds = None
        self._painted_bands = []
        self._previous_costume_poses = set()
        self._current_costume_poses = set()
        try:
            width, height = self.overlay.width, self.overlay.height
            check(create_bitmap(width, height, width * 4, PIXEL_FORMAT_32BPP_PARGB,
                                self.overlay.address, ct.byref(self.bitmap)),
                  "GdipCreateBitmapFromScan0")
            check(create_graphics(self.bitmap, ct.byref(self.graphics)),
                  "GdipGetImageGraphicsContext")
            check(set_smoothing(self.graphics, SMOOTHING_ANTIALIAS), "GdipSetSmoothingMode")
            check(translate_graphics(self.graphics, -self.overlay.left, -self.overlay.top, 0),
                  "GdipTranslateWorldTransform")
        except BaseException:
            self.close()
            raise

    def _update_clips(self, windows, work_area, platforms, owners):
        left, top, right, bottom = self.rect
        if work_area is not None:
            wl, wt, wr, wb = work_area
            left, top, right, bottom = max(left, wl), max(top, wt), min(right, wr), min(bottom, wb)
        work = left, top, right, bottom
        blockers = []
        for hwnd, x1, y1, x2, y2 in windows:
            if x1 < right and y1 < bottom and x2 > left and y2 > top:
                blockers.append((hwnd if owners else 0, (
                    max(x1, left), max(y1, top), min(x2, right), min(y2, bottom),
                )))
            elif hwnd in owners:
                # A window beginning at a monitor seam can support snow on the monitor above.
                blockers.append((hwnd, None))
        order = tuple(blockers) if owners else tuple(sorted(set(blockers)))
        key = work, order, tuple(platforms), frozenset(owners)
        if self._clip_key == key:
            if self._pending_dense_bounds is not None:
                self._settle_dense_bands()
            return
        self._clear_clips()
        self._clip_key = key
        self._flake_occluders = ()
        self._dense_flake_bands = ()
        self._pending_dense_bounds = None
        blocked = {box for _, box in order if box is not None}
        if right <= left or bottom <= top or (not owners and work in blocked):
            return

        region = Handle()
        rect = RectI(left, top, right - left, bottom - top)
        check(create_region(ct.byref(rect), ct.byref(region)), "GdipCreateRegionRectI")
        self._clips[0] = region, None
        try:
            for platform in platforms:
                self._exclude_platform(region, platform)
            if FLYING_BALL_LAYER in owners:
                # A thrown ball is visible over app windows, within the desktop and taskbar clip.
                flight_clip = Handle()
                check(clone_region(region, ct.byref(flight_clip)), "GdipCloneRegion")
                self._clips[FLYING_BALL_LAYER] = flight_clip, None
            # Walk Z-order once. Each ledge gets the clip after excluding its own
            # window and every visible window above it, but no background windows.
            for hwnd, box in order:
                if box in blocked:
                    blocked.remove(box)
                    x1, y1, x2, y2 = box
                    rect = RectI(x1, y1, x2 - x1, y2 - y1)
                    check(exclude_rect(region, ct.byref(rect), COMBINE_EXCLUDE), "GdipCombineRegionRectI")
                if hwnd in owners:
                    snapshot = Handle()
                    check(clone_region(region, ct.byref(snapshot)), "GdipCloneRegion")
                    self._clips[hwnd] = snapshot, None
            for hwnd, (mask, _) in self._clips.items():
                self._clips[hwnd] = mask, self._region_bounds(mask)
        except BaseException:
            self._clear_clips()
            self._clip_key = None
            raise
        # Cull particles wholly behind large windows before invoking the native rasterizer.
        # The clip still handles every other blocker and antialiased edge pixel.
        cutoff = 0.05 * (right - left) * (bottom - top)
        largest = sorted({box for _, box in order if box is not None}, key=box_area, reverse=True)[:4]
        self._flake_occluders = tuple((x1 + 1, y1 + 1, x2 - 1, y2 - 1)
                                      for x1, y1, x2, y2 in largest if box_area((x1, y1, x2, y2)) > cutoff)
        bounds = self._clips[0][1]
        if bounds is not None and self._flake_occluders:
            strips = subtract_box(bounds, self._flake_occluders[0])
            if strips and sum(box_area(strip) for strip in strips) <= 0.65 * box_area(bounds):
                self._dense_flake_bands = strips
            if len(self._flake_occluders) > 1:
                # Defer the multi-window layout until the clip stops moving.
                self._pending_dense_bounds = bounds

    def _settle_dense_bands(self):
        bounds = self._pending_dense_bounds
        self._pending_dense_bounds = None
        strips = subtract_box(bounds, self._flake_occluders[0])
        best_area = sum(box_area(band) for band in self._dense_flake_bands) or 0.65 * box_area(bounds)
        for blocker in self._flake_occluders[1:]:
            remainder = tuple(piece for strip in strips for piece in subtract_box(strip, blocker))
            if remainder == strips:
                continue
            strips = remainder
            bands = merge_bands(strips, max_bands=6) if len(strips) > 4 else strips
            area = sum(box_area(band) for band in bands)
            # A few additional updates are worthwhile only with a substantial
            # byte saving. Existing owned artwork adds its own bounds separately.
            score = area + 65_536 * max(0, len(bands) - 4)
            if bands and score < best_area:
                best_area = score
                self._dense_flake_bands = tuple(bands)

    def _region_bounds(self, region):
        bounds = RectI()
        check(get_region_bounds(region, self.graphics, ct.byref(bounds)), "GdipGetRegionBoundsI")
        if bounds.width > 0 and bounds.height > 0:
            return bounds.x, bounds.y, bounds.x + bounds.width, bounds.y + bounds.height
        return None

    def _clear_clips(self):
        for region, _ in self._clips.values():
            delete_region(region)
        self._clips.clear()
        self._active_clip = None

    def _apply_clip(self, hwnd):
        region, bounds = self._clips.get(hwnd, (None, None))
        if bounds is not None and self._active_clip != hwnd:
            check(set_clip_region(self.graphics, region, COMBINE_REPLACE), "GdipSetClipRegion")
            self._active_clip = hwnd
        return bounds

    def _exclude_platform(self, region, platform):
        left, top, right, bottom, radius = platform
        diameter = min(2 * radius, right - left, bottom - top)
        if diameter <= 0:
            rect = RectI(left, top, right - left, bottom - top)
            check(exclude_rect(region, ct.byref(rect), COMBINE_EXCLUDE), "GdipCombineRegionRectI")
            return
        path = Handle()
        check(create_path(FILL_ALTERNATE, ct.byref(path)), "GdipCreatePath")
        try:
            for x, y, angle in ((left, top, 180), (right - diameter, top, 270),
                                (right - diameter, bottom - diameter, 0),
                                (left, bottom - diameter, 90)):
                check(add_path_arc(path, x, y, diameter, diameter, angle, 90), "GdipAddPathArc")
            check(close_path_figure(path), "GdipClosePathFigure")
            check(exclude_path(region, path, COMBINE_EXCLUDE), "GdipCombineRegionPath")
        finally:
            delete_path(path)

    def _polygon(self, brush, polygon, *, graphics=None):
        coordinates = array("f", chain.from_iterable(polygon))
        points = (PointF * len(polygon)).from_buffer(coordinates)
        check(fill_polygon(self.graphics if graphics is None else graphics,
                           brush, points, len(points), FILL_ALTERNATE),
              "GdipFillPolygon")

    def _circle(self, brush, x, y, radius, *, graphics=None):
        size = radius * 2
        check(fill_ellipse(self.graphics if graphics is None else graphics,
                           brush, x - radius, y - radius, size, size),
              "GdipFillEllipse")

    def _snowman(self, x, ground_y, radii):
        shadow = self.engine.brush(SNOW_SHADOW)
        light = self.engine.brush(SNOW_LIGHT)
        coal = self.engine.brush(COAL_COLOR)
        count = len(radii)
        y = ground_y
        for stage, radius in enumerate(radii):
            y -= radius
            if count == 1:
                self._circle(self.engine.brush(BALL_EDGE), x, y, radius)
                self._circle(shadow, x, y, max(1, radius - 1.3))
            else:
                self._circle(shadow, x, y, radius)
            self._circle(light, x - radius * 0.10, y - radius * 0.13, radius * 0.83)
            if count == 2 and stage == 1 and radius >= 4:
                self._circle(coal, x, y - radius * 0.24, radius * 0.11)
                self._circle(coal, x, y + radius * 0.30, radius * 0.11)
            y -= radius * 0.80

    def _costume_shapes(self, graphics, shapes, x, y, radius, facing=1):
        for color, points in shapes:
            polygon = tuple((x + facing * radius * dx, y + radius * dy) for dx, dy in points)
            self._polygon(self.engine.brush(color), polygon, graphics=graphics)

    def _paint_classic(self, x, ground_y, radii, bounds, painted, *, cache_details=False):
        body, head = radii[-2:]
        body_y, head_y, box = _classic_layout(x, ground_y, radii)
        left, top, right, bottom = bounds
        if box[2] < left or box[0] >= right or box[3] < top or box[1] >= bottom:
            return
        self._painted_box(painted, bounds, box)
        if cache_details:
            tx, ty = floor(box[0]) - 1, floor(box[1]) - 1
            width, height = ceil(box[2]) + 1 - tx, ceil(box[3]) + 1 - ty
            if 0 < width <= MAX_COSTUME_TILE_SIDE and 0 < height <= MAX_COSTUME_TILE_SIDE:
                if self._paint_cached_classic(x, ground_y, radii, body_y, head_y, tx, ty, width, height):
                    return
        self._draw_classic(self.graphics, x, body_y, head_y, body, head)

    def _paint_cached_classic(self, x, y, radii, body_y, head_y, tx, ty, width, height):
        key = x, y, radii
        self._current_costume_poses.add(key)
        cache = self.engine.costume_tiles
        tile = cache.get(key)
        if tile is None:
            # Moving poses use the original path. Cache only after a pose settles.
            if key not in self._previous_costume_poses:
                return False
            body, head = radii[-2:]

            def paint(graphics):
                check(translate_graphics(graphics, -tx, -ty, 0), "GdipTranslateWorldTransform")
                self._draw_classic(graphics, x, body_y, head_y, body, head)

            tile = _raster_tile(width, height, paint)
            cache[key] = tile
            bitmap, _ = tile
            if len(cache) > COSTUME_CACHE_LIMIT:
                _, (old_bitmap, _old_pixels) = cache.popitem(last=False)
                dispose_bitmap(old_bitmap)
        else:
            bitmap, _ = tile
            cache.move_to_end(key)
        # Use the active owner clip and a 1:1 blit of the original premultiplied pixels.
        check(draw_bitmap(self.graphics, bitmap, tx, ty, width, height,
                          0, 0, width, height, UNIT_PIXEL, None, None, None),
              "GdipDrawImageRectRectI")
        return True

    def _draw_classic(self, graphics, x, body_y, head_y, body, head):
        self._costume_shapes(graphics, CLASSIC_TWIG, x, body_y, body, -1)
        self._costume_shapes(graphics, CLASSIC_TWIG, x, body_y, body)
        coal = self.engine.brush(CLASSIC_COAL)
        for dy in (-.17, .19, .55):
            self._circle(coal, x, body_y + dy * body, max(.55, .11 * body), graphics=graphics)
        self._costume_shapes(graphics, CLASSIC_SCARF, x, head_y, head)
        self._costume_shapes(graphics, CLASSIC_HAT, x, head_y, head)
        self._circle(coal, x - .32 * head, head_y - .26 * head, max(.55, .13 * head), graphics=graphics)
        self._circle(coal, x + .29 * head, head_y - .26 * head, max(.55, .13 * head), graphics=graphics)
        for dx, dy in ((-.38, .26), (-.25, .36), (-.10, .39), (.06, .35)):
            self._circle(coal, x + dx * head, head_y + dy * head, max(.30, .064 * head), graphics=graphics)
        self._costume_shapes(graphics, CLASSIC_CARROT, x, head_y, head)

    def _paint_shovel(self, shovel, bounds, painted):
        x, y, direction = shovel
        sign = -1 if direction < 0 else 1
        left, top, right, bottom = bounds
        x1, x2 = (x - 25, x + 13) if sign > 0 else (x - 13, x + 25)
        y1 = y - 37
        if x2 < left or x1 >= right or y < top or y1 >= bottom:
            return
        self._painted_box(painted, bounds, (x1, y1, x2, y))
        for color, points in SHOVEL_SHAPES:
            self._polygon(self.engine.brush(color), tuple((x + sign * dx, y + dy) for dx, dy in points))

    def _draw_plow(self, graphics, level, part):
        # A transparent pixel around the sprite preserves its antialiased silhouette.
        check(translate_graphics(graphics, 1, 1, 0), "GdipTranslateWorldTransform")
        check(scale_graphics(graphics, PLOW_SCALE, PLOW_SCALE, 0), "GdipScaleWorldTransform")
        if part != "blade":
            self._draw_plow_body(graphics, level)
        if part != "body":
            shapes = PLOW_SCOOP[2:] if part == "blade" else PLOW_SCOOP
            self._costume_shapes(graphics, shapes, 0, 0, 1)
            for x in (104, 111, 118):
                self._circle(self.engine.brush(0xFF2D899C), x, 62.5, 0.7, graphics=graphics)

    def _draw_plow_body(self, graphics, level):
        self._costume_shapes(graphics, PLOW_SHELL, 0, 0, 1)
        if level:
            fill = level / PLOW_FILL_STEPS
            crest = 26 - 9 * fill
            snow = ((6, 26), (9, crest + 2 * fill), (14, crest), (19, crest + fill),
                    (24, crest - 1.5 * fill), (29, crest), (34, crest + fill),
                    (39, crest + 3 * fill), (40, 26))
            self._polygon(self.engine.brush(SNOW_SHADOW), snow, graphics=graphics)
            highlight = ((9, crest + 2 * fill), (14, crest), (19, crest + fill),
                         (24, crest - 1.5 * fill), (29, crest), (34, crest + fill),
                         (38, crest + 3 * fill), (29, crest + 2 * fill),
                         (17, crest + 3 * fill), (9, crest + 4 * fill))
            self._polygon(self.engine.brush(SNOW_LIGHT), highlight, graphics=graphics)
        for wheel in (24, 69):
            for color, radius in ((0xFF101722, 12), (0xFF283442, 9.7), (0xFF1095A9, 7.2),
                                  (PLOW_CYAN, 6.5), (0xFF243749, 5.1), (0xFF344C60, 2.0)):
                self._circle(self.engine.brush(color), wheel, 52, radius, graphics=graphics)
            tread = self.engine.brush(0xFF3B4655)
            for dx, dy in ((-10, 0), (-7, -7), (0, -10), (7, -7), (10, 0)):
                self._circle(tread, wheel + dx, 52 + dy, 0.9, graphics=graphics)
    def _plow_tile(self, level, part, width, height):
        tiles = self.engine.plow_tiles
        key = level, part
        tile = tiles.get(key)
        if tile is None:
            tile = tiles[key] = _raster_tile(width, height, lambda graphics: self._draw_plow(graphics, level, part))
        return tile[0]

    def _paint_plow(self, plow, pitch, bounds, painted):
        blade_x, baseline, fill, chips = plow
        left, top, right, bottom = bounds
        chip_brush = self.engine.brush(SNOW_LIGHT)
        for cx, cy, radius in chips:
            x1, y1, x2, y2 = cx - radius, cy - radius, cx + radius, cy + radius
            if x1 < right and y1 < bottom and x2 >= left and y2 >= top:
                self._painted_box(painted, bounds, (x1, y1, x2, y2))
                self._circle(chip_brush, cx, cy, radius)
        x, y = round(blade_x), round(baseline)
        x1, y1 = x - VEHICLE_LENGTH, y - VEHICLE_HEIGHT
        level = ceil(PLOW_FILL_STEPS * min(1.0, max(0.0, fill)))
        width, height = VEHICLE_LENGTH + 2, VEHICLE_HEIGHT + 2
        if pitch:
            angle_x, angle_y = cos(pitch), sin(pitch)

            def turn(px, py):
                dx, dy = px - x, py - y
                return x + dx * angle_x - dy * angle_y, y + dx * angle_y + dy * angle_x

            corners = tuple(turn(px, py) for px, py in ((x1 - 1, y1 - 1), (x + 1, y1 - 1),
                                                        (x1 - 1, y + 1), (x + 1, y + 1)))
            xs, ys = zip(*corners)
            box = union_bounds((x1, y1, x, y), (min(xs), min(ys), max(xs), max(ys)))
            if box[2] < left or box[3] < top or box[0] >= right or box[1] >= bottom:
                return
            self._painted_box(painted, bounds, box)
            # The cab pitches with the wheels while the articulated scoop keeps its contact pose.
            arm_x, arm_y = turn(x1 + 91 * PLOW_SCALE, y1 + 47 * PLOW_SCALE)
            blade_arm_x, blade_arm_y = x1 + 102 * PLOW_SCALE, y1 + 55 * PLOW_SCALE
            self._polygon(self.engine.brush(PLOW_INK), (
                (arm_x - 3, arm_y - 4), (blade_arm_x, blade_arm_y - 4),
                (blade_arm_x, blade_arm_y + 4), (arm_x - 3, arm_y + 4),
            ))
            body = self._plow_tile(level, "body", width, height)
            points = (PointF * 3)(*(PointF(*point) for point in corners[:3]))
            check(draw_bitmap_points(self.graphics, body, points, 3, 0, 0, width, height,
                                     UNIT_PIXEL, None, None, None), "GdipDrawImagePointsRect")
            bitmap = self._plow_tile(0, "blade", width, height)
        else:
            if x < left or y < top or x1 >= right or y1 >= bottom:
                return
            self._painted_box(painted, bounds, (x1, y1, x, y))
            bitmap = self._plow_tile(level, "flat", width, height)
        check(draw_bitmap(self.graphics, bitmap, x1 - 1, y1 - 1, width, height,
                          0, 0, width, height, UNIT_PIXEL, None, None, None), "GdipDrawImageRectRectI")

    def _clear_previous(self):
        overlay = self.overlay
        bands = self._painted_bands
        area = overlay.width * overlay.height
        if (len(bands) > 1 and area >= 500_000
                and any(bottom - top > 96 and right - left < overlay.width // 2
                        for left, top, right, bottom in bands)
                and sum(box_area(box) for box in bands) <= area * 0.8
                and overlay.clear_bands(bands)):
            return
        stride = overlay.width * 4
        full_rows = []
        for left, top, right, bottom in bands:
            width = (right - left) * 4
            start = overlay.address + (top - overlay.top) * stride
            # One contiguous clear beats a long row loop. Extra pixels are transparent.
            if width >= stride // 2 or bottom - top > 96:
                full_rows.append((top, bottom))
            else:
                start += (left - overlay.left) * 4
                for y in range(bottom - top):
                    ct.memset(start + y * stride, 0, width)
        cleared_to = overlay.top
        for top, bottom in sorted(full_rows):
            start = max(top, cleared_to)
            if start < bottom:
                ct.memset(overlay.address + (start - overlay.top) * stride, 0, (bottom - start) * stride)
                cleared_to = bottom

    def _paint_shapes(self, drifts, snowmen, costumes, bounds, painted, *, cache_details=False):
        left, top, right, bottom = bounds
        drift_brush = self.engine.brush(DRIFT_COLOR)
        for polygon in drifts:
            x1 = getattr(polygon, "left", polygon[0][0])
            x2 = getattr(polygon, "right", polygon[-1][0])
            y2 = getattr(polygon, "bottom", polygon[0][1])
            y1 = getattr(polygon, "top", None)
            if y1 is None:
                y1 = min(point[1] for point in polygon)
            if x2 < left or x1 >= right or y2 < top or y1 >= bottom:
                continue
            self._painted_box(painted, bounds, (x1, y1, x2, y2))
            self._polygon(drift_brush, polygon)
        for x, y, *radii in snowmen:
            radii = tuple(radius for radius in radii if radius > 0)
            if not radii:
                continue
            radius = max(radii)
            y1 = y - 2 * sum(radii)
            if x + radius >= left and x - radius < right and y >= top and y1 < bottom:
                self._painted_box(painted, bounds, (x - radius, y1, x + radius, y))
                self._snowman(x, y, radii)
            # The interactive layer stays inside the original ball silhouettes.
            if not self.interactive and len(radii) >= 3:
                self._paint_classic(x, y, radii, bounds, painted, cache_details=cache_details)
        for x, y, *radii in costumes:
            radii = tuple(radius for radius in radii if radius > 0)
            if len(radii) >= 3:
                self._paint_classic(x, y, radii, bounds, painted, cache_details=True)

    def _paint_flakes(self, flakes, bounds, painted, *, airborne=False):
        left, top, right, bottom = bounds
        graphics = self.graphics
        brushes = self.engine.flake_palette()
        blockers = () if airborne else self._flake_occluders
        local_boxes = []
        for flake in flakes:
            x, y, radius = flake.x, flake.y, flake.radius
            x1, y1, x2, y2 = x - radius, y - radius, x + radius, y + radius
            if x2 < left or x1 >= right or y2 < top or y1 >= bottom:
                continue
            for bx1, by1, bx2, by2 in blockers:
                if bx1 <= x1 and bx2 >= x2 and by1 <= y1 and by2 >= y2:
                    break
            else:
                if airborne or len(local_boxes) <= 48:
                    self._painted_box(local_boxes, bounds, (x1, y1, x2, y2))
                brush = brushes[min(16, (flake.alpha + 8) // 16)]
                size = radius * 2
                status = fill_ellipse(graphics, brush, x1, y1, size, size)
                if status:
                    check(status, "GdipFillEllipse")
        # Other monitors or large occluders may leave this screen with sparse snowfall.
        # For dense snow, use the cached visible bands instead of tracking each flake.
        if len(local_boxes) <= 48:
            painted.extend(local_boxes)
            return 4
        if airborne:
            painted.append((min(box[0] for box in local_boxes), min(box[1] for box in local_boxes),
                            max(box[2] for box in local_boxes), max(box[3] for box in local_boxes)))
            return 4
        bands = self._dense_flake_bands or (bounds,)
        painted.extend(bands)
        return max(4, len(bands))

    @staticmethod
    def _painted_box(painted, bounds, box):
        # Include antialiased fringe pixels, but never go outside this group's clip.
        left, top, right, bottom = bounds
        x1, y1, x2, y2 = box
        box = (max(left, floor(x1) - 1), max(top, floor(y1) - 1),
               min(right, ceil(x2) + 1), min(bottom, ceil(y2) + 1))
        if box[0] < box[2] and box[1] < box[3]:
            painted.append(box)

    @staticmethod
    def _bank_change(before, after):
        length = min(len(before), len(after))
        prefix = 0
        while prefix < length and before[prefix] == after[prefix]:
            prefix += 1
        suffix = 0
        while suffix < length - prefix and before[-1 - suffix] == after[-1 - suffix]:
            suffix += 1
        if prefix and suffix:
            # Unchanged neighbors bound the altered chain of polygon edges.
            points = before[prefix - 1:len(before) - suffix + 1]
            points.extend(after[prefix - 1:len(after) - suffix + 1])
            return (min(x for x, _ in points), min(y for _, y in points),
                    max(x for x, _ in points), max(y for _, y in points))
        return (min(before.left, after.left), min(before.top, after.top),
                max(before.right, after.right), max(before.bottom, after.bottom))

    def _patch_banks(self, old_layers, drift_layers, figure_layers):
        # Static figures and clips let us retain pixels outside the changed bank edges.
        changes = []
        if old_layers.keys() != drift_layers.keys():
            return False
        for hwnd, drifts in drift_layers.items():
            previous = old_layers[hwnd]
            if len(previous) != len(drifts):
                return False
            _, clip_bounds = self._clips.get(hwnd, (None, None))
            if clip_bounds is None:
                continue
            for old, new in zip(previous, drifts):
                if not isinstance(old, DriftPolygon) or not isinstance(new, DriftPolygon):
                    return False
                if old is new or old == new:
                    continue
                self._painted_box(changes, clip_bounds, self._bank_change(old, new))
        if not changes:
            self.overlay.move_to_destination()
            return True
        dirty = merge_bands(changes)
        if sum(box_area(box) for box in dirty) >= .7 * sum(box_area(box) for box in self._painted_bands):
            return False
        overlay = self.overlay
        region = Handle()
        try:
            x1, y1, x2, y2 = dirty[0]
            rect = RectI(x1, y1, x2 - x1, y2 - y1)
            if create_region(ct.byref(rect), ct.byref(region)):
                return False
            for x1, y1, x2, y2 in dirty[1:]:
                rect = RectI(x1, y1, x2 - x1, y2 - y1)
                if exclude_rect(region, ct.byref(rect), COMBINE_UNION):
                    return False
            if not overlay.clear_bands(dirty):
                return False

            def overlaps(left, top, right, bottom):
                return any(x1 < right + 1 and x2 > left - 1 and y1 < bottom + 1 and y2 > top - 1
                           for x1, y1, x2, y2 in dirty)

            for hwnd in (0, *reversed(tuple(self._clips)[1:])):
                bounds = self._clips[hwnd][1]
                if bounds is None:
                    continue
                polygons = [polygon for polygon in drift_layers.get(hwnd, ())
                            if overlaps(polygon.left, polygon.top, polygon.right, polygon.bottom)]
                figures = [figure for figure in figure_layers.get(hwnd, ())
                           if overlaps(*figure_bounds(figure))]
                if not (polygons or figures):
                    continue
                self._apply_clip(hwnd)
                check(set_clip_region(self.graphics, region, COMBINE_INTERSECT), "GdipSetClipRegion")
                self._active_clip = None
                self._paint_shapes(polygons, figures, (), bounds, [])
            check(flush_graphics(self.graphics, FLUSH_SYNC), "GdipFlush")
            for x1, y1, x2, y2 in dirty:
                overlay.present((x1 - overlay.left, y1 - overlay.top,
                                 x2 - overlay.left, y2 - overlay.top))
            self._painted_bands = merge_bands((*self._painted_bands, *dirty))
            return True
        finally:
            if region:
                delete_region(region)

    def render(self, flakes, drifts, snowmen=(), windows=(), work_area=None, platforms=(),
               *, snowmen_owners=(), shovel=None, costumes=(), cache_costumes=False, plow=None,
               plow_pitch=0.0, impact_flakes=()):
        if self.interactive:
            plow = None
            impact_flakes = ()
            if not (drifts or snowmen or shovel is not None):
                if self.visible:
                    self.hide()
                return
        drift_layers = {}
        for polygon in drifts:
            drift_layers.setdefault(getattr(polygon, "hwnd", 0), []).append(polygon)
        figure_layers = {}
        owner_iter = iter(snowmen_owners)
        for figure in snowmen:
            figure_layers.setdefault(next(owner_iter, 0), []).append(figure)
        costume_layers = {}
        if not self.interactive:
            for owner, figure in costumes:
                costume_layers.setdefault(owner, []).append(figure)
        owners = (drift_layers.keys() | figure_layers.keys() | costume_layers.keys()) - {0}
        if impact_flakes:
            owners.add(FLYING_BALL_LAYER)
        self._update_clips(windows, work_area, platforms, owners)
        if not self._clips:
            if self.visible:
                self.hide()
            return
        if not flakes and not impact_flakes:
            scene = self._clip_key, drift_layers, figure_layers, costume_layers, shovel, plow, plow_pitch
            if self.visible and scene == self._stationary_scene:
                self.overlay.move_to_destination()
                return
            before = self._stationary_scene
            self._stationary_scene = scene
            if (self.interactive and self.visible and before is not None
                    and before[0] == self._clip_key and before[2] == figure_layers
                    and before[4] is None and shovel is None
                    and self._patch_banks(before[1], drift_layers, figure_layers)):
                return
        else:
            self._stationary_scene = None
        overlay = self.overlay
        self._clear_previous()
        self._previous_costume_poses, self._current_costume_poses = (
            self._current_costume_poses, self._previous_costume_poses,
        )
        self._current_costume_poses.clear()
        painted = []
        # Ground, then each ledge from back to front. Airborne flakes use the full mask.
        for hwnd in (0, *reversed(tuple(self._clips)[1:])):
            polygons = drift_layers.get(hwnd, ())
            figures = figure_layers.get(hwnd, ())
            details = costume_layers.get(hwnd, ())
            if polygons or figures or details:
                bounds = self._apply_clip(hwnd)
                if bounds is not None:
                    self._paint_shapes(polygons, figures, details, bounds, painted,
                                       cache_details=cache_costumes)
        band_limit = 4
        if flakes:
            bounds = self._apply_clip(0)
            if bounds is not None:
                band_limit = self._paint_flakes(flakes, bounds, painted)
        if impact_flakes:
            bounds = self._apply_clip(FLYING_BALL_LAYER)
            if bounds is not None:
                self._paint_flakes(impact_flakes, bounds, painted, airborne=True)
        if shovel is not None:
            bounds = self._apply_clip(0)
            if bounds is not None:
                self._paint_shovel(shovel, bounds, painted)
        if plow is not None:
            bounds = self._apply_clip(0)
            if bounds is not None:
                self._paint_plow(plow, plow_pitch, bounds, painted)
        bands = merge_bands(painted, max_bands=band_limit)
        dirty = merge_bands((*self._painted_bands, *bands),
                            max_bands=max(band_limit, len(self._painted_bands)))
        if dirty:
            check(flush_graphics(self.graphics, FLUSH_SYNC), "GdipFlush")
            for x1, y1, x2, y2 in dirty:
                overlay.present((x1 - overlay.left, y1 - overlay.top,
                                 x2 - overlay.left, y2 - overlay.top))
        self._painted_bands = bands
        if not bands:
            if self.visible:
                self.hide()
            return
        if not self.visible:
            overlay.show()
            self.visible = True

    def hide(self):
        self.overlay.hide()
        self.visible = False

    def close(self):
        self._clear_clips()
        if self.graphics:
            delete_graphics(self.graphics)
        if self.bitmap:
            dispose_bitmap(self.bitmap)
        self.overlay.close()
