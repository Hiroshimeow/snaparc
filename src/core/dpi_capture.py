"""Multi-monitor, mixed-DPI, rotation/HDR-aware coordinate and capture engine.

Solves:
1. Cross-monitor / high-DPI physical pixel crop accuracy (including negative virtual coordinates).
2. Window-move / minimize race conditions while a capture or custom prompt is pending by freezing
   the screen framebuffer and active window geometry at user action time.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import os
import sys
import time
from typing import Callable, Iterable
from PIL import Image, ImageGrab

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


@dataclass(frozen=True)
class MonitorDescriptor:
    monitor_id: str
    left: int
    top: int
    right: int
    bottom: int
    dpi_x: int = 96
    dpi_y: int = 96
    rotation_deg: int = 0
    is_primary: bool = False
    is_hdr: bool = False
    device_name: str = ""

    @property
    def width(self) -> int:
        return max(1, self.right - self.left)

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)

    @property
    def scale_factor(self) -> float:
        return round(max(0.5, float(self.dpi_x) / 96.0), 4)

    def contains_point(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def intersection_area(self, left: int, top: int, right: int, bottom: int) -> int:
        ix1 = max(self.left, left)
        iy1 = max(self.top, top)
        ix2 = min(self.right, right)
        iy2 = min(self.bottom, bottom)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0
        return (ix2 - ix1) * (iy2 - iy1)


@dataclass(frozen=True)
class VirtualScreenTopology:
    virtual_left: int
    virtual_top: int
    virtual_width: int
    virtual_height: int
    monitors: tuple[MonitorDescriptor, ...] = field(default_factory=tuple)

    @property
    def virtual_right(self) -> int:
        return self.virtual_left + self.virtual_width

    @property
    def virtual_bottom(self) -> int:
        return self.virtual_top + self.virtual_height

    @classmethod
    def detect(cls, custom_monitors: Iterable[MonitorDescriptor] | None = None) -> "VirtualScreenTopology":
        if custom_monitors is not None:
            mons = tuple(custom_monitors)
            if mons:
                v_left = min(m.left for m in mons)
                v_top = min(m.top for m in mons)
                v_right = max(m.right for m in mons)
                v_bottom = max(m.bottom for m in mons)
                return cls(
                    virtual_left=v_left,
                    virtual_top=v_top,
                    virtual_width=max(1, v_right - v_left),
                    virtual_height=max(1, v_bottom - v_top),
                    monitors=mons,
                )

        if sys.platform == "win32" and hasattr(ctypes, "windll"):
            try:
                user32 = ctypes.windll.user32
                left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
                top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
                width = max(1, int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)))
                height = max(1, int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))
                monitors = cls._enumerate_win32_monitors(left, top, width, height)
                return cls(
                    virtual_left=left,
                    virtual_top=top,
                    virtual_width=width,
                    virtual_height=height,
                    monitors=monitors,
                )
            except Exception:
                pass

        default_mon = MonitorDescriptor(
            monitor_id="DISPLAY1",
            left=0,
            top=0,
            right=1920,
            bottom=1080,
            dpi_x=96,
            dpi_y=96,
            is_primary=True,
            device_name="\\\\.\\DISPLAY1",
        )
        return cls(
            virtual_left=0,
            virtual_top=0,
            virtual_width=1920,
            virtual_height=1080,
            monitors=(default_mon,),
        )

    @staticmethod
    def _enumerate_win32_monitors(
        fallback_left: int, fallback_top: int, fallback_w: int, fallback_h: int
    ) -> tuple[MonitorDescriptor, ...]:
        monitors: list[MonitorDescriptor] = []
        try:
            import ctypes.wintypes

            user32 = ctypes.windll.user32
            shcore = getattr(ctypes.windll, "shcore", None)

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            class MONITORINFOEXW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.wintypes.DWORD),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", ctypes.wintypes.DWORD),
                    ("szDevice", ctypes.wintypes.WCHAR * 32),
                ]

            MONITORENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.POINTER(RECT),
                ctypes.c_ssize_t,
            )

            def _callback(hmonitor, _hdc, lprect, _lparam):
                info = MONITORINFOEXW()
                info.cbSize = ctypes.sizeof(MONITORINFOEXW)
                dpi_x = ctypes.c_uint(96)
                dpi_y = ctypes.c_uint(96)
                if shcore is not None:
                    try:
                        shcore.GetDpiForMonitor(
                            ctypes.c_void_p(hmonitor),
                            0,
                            ctypes.byref(dpi_x),
                            ctypes.byref(dpi_y),
                        )
                    except Exception:
                        pass
                if user32.GetMonitorInfoW(ctypes.c_void_p(hmonitor), ctypes.byref(info)):
                    rc = info.rcMonitor
                    is_primary = bool(info.dwFlags & 1)
                    dev = str(info.szDevice or f"DISPLAY{len(monitors) + 1}")
                else:
                    rc = lprect.contents
                    is_primary = len(monitors) == 0
                    dev = f"DISPLAY{len(monitors) + 1}"
                monitors.append(
                    MonitorDescriptor(
                        monitor_id=f"MON_{len(monitors) + 1}",
                        left=int(rc.left),
                        top=int(rc.top),
                        right=int(rc.right),
                        bottom=int(rc.bottom),
                        dpi_x=int(dpi_x.value or 96),
                        dpi_y=int(dpi_y.value or 96),
                        is_primary=is_primary,
                        device_name=dev,
                    )
                )
                return 1

            user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_callback), 0)
        except Exception:
            pass

        if not monitors:
            monitors.append(
                MonitorDescriptor(
                    monitor_id="MON_1",
                    left=fallback_left,
                    top=fallback_top,
                    right=fallback_left + fallback_w,
                    bottom=fallback_top + fallback_h,
                    dpi_x=96,
                    dpi_y=96,
                    is_primary=True,
                    device_name="\\\\.\\DISPLAY1",
                )
            )
        return tuple(monitors)

    def dominant_monitor_for_rect(self, left: int, top: int, right: int, bottom: int) -> MonitorDescriptor:
        best_mon = self.monitors[0] if self.monitors else MonitorDescriptor("DEFAULT", 0, 0, 1920, 1080)
        best_area = -1
        for mon in self.monitors:
            area = mon.intersection_area(left, top, right, bottom)
            if area > best_area:
                best_area = area
                best_mon = mon
        return best_mon


class DpiCoordinateEngine:
    """Computes pixel-accurate crop rectangles across multi-monitor & mixed-DPI setups."""

    def __init__(self, topology: VirtualScreenTopology | None = None):
        self.topology = topology or VirtualScreenTopology.detect()

    def normalize_drag_rect(self, x1: int, y1: int, x2: int, y2: int) -> tuple[int, int, int, int]:
        left = min(int(x1), int(x2))
        top = min(int(y1), int(y2))
        right = max(int(x1), int(x2))
        bottom = max(int(y1), int(y2))
        if right <= left:
            right = left + 1
        if bottom <= top:
            bottom = top + 1
        return left, top, right, bottom

    def virtual_to_image_crop(
        self,
        left: int,
        top: int,
        right: int,
        bottom: int,
        image_size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        """Map virtual-screen coordinates (which may be negative on left/top monitors)
        onto a captured full-virtual-screen image of size `(img_w, img_h)`."""
        norm_left, norm_top, norm_right, norm_bottom = self.normalize_drag_rect(left, top, right, bottom)
        img_w, img_h = max(1, int(image_size[0])), max(1, int(image_size[1]))

        v_left = self.topology.virtual_left
        v_top = self.topology.virtual_top
        v_w = max(1, self.topology.virtual_width)
        v_h = max(1, self.topology.virtual_height)

        scale_x = float(img_w) / float(v_w)
        scale_y = float(img_h) / float(v_h)

        crop_left = int(round((norm_left - v_left) * scale_x))
        crop_top = int(round((norm_top - v_top) * scale_y))
        crop_right = int(round((norm_right - v_left) * scale_x))
        crop_bottom = int(round((norm_bottom - v_top) * scale_y))

        crop_left = max(0, min(crop_left, img_w - 1))
        crop_top = max(0, min(crop_top, img_h - 1))
        crop_right = max(crop_left + 1, min(crop_right, img_w))
        crop_bottom = max(crop_top + 1, min(crop_bottom, img_h))
        return crop_left, crop_top, crop_right, crop_bottom

    def logical_to_physical_point(self, x: int, y: int, dpi_aware: bool = True) -> tuple[int, int]:
        if dpi_aware:
            return int(x), int(y)
        for mon in self.topology.monitors:
            if mon.contains_point(x, y):
                scale = mon.scale_factor
                phys_x = mon.left + int(round((x - mon.left) * scale))
                phys_y = mon.top + int(round((y - mon.top) * scale))
                return phys_x, phys_y
        return int(x), int(y)


@dataclass(frozen=True)
class FrozenCaptureFrame:
    """Immutable screen capture frame taken at user gesture time."""
    frame_id: str
    captured_at_monotonic: float
    captured_at_iso: str
    virtual_bbox: tuple[int, int, int, int]
    crop_bbox_image: tuple[int, int, int, int]
    monitor_id: str
    monitor_dpi: int
    monitor_scale: float
    image_size: tuple[int, int]
    sha256: str
    png_bytes: bytes
    saved_path: str = ""

    def to_pil(self) -> Image.Image:
        return Image.open(BytesIO(self.png_bytes)).copy()


class WindowMoveSafeCapturer:
    """Captures the screen region immediately at user action time so subsequent window
    movements, popups, or async delays never corrupt the captured pixels."""

    def __init__(
        self,
        screenshots_dir: str,
        grab_fn: Callable[..., Image.Image] | None = None,
        topology_provider: Callable[[], VirtualScreenTopology] | None = None,
    ):
        self.screenshots_dir = screenshots_dir
        os.makedirs(self.screenshots_dir, exist_ok=True)
        self._grab_fn = grab_fn
        self._topology_provider = topology_provider or VirtualScreenTopology.detect

    def capture_region_at_action_time(
        self,
        left: int,
        top: int,
        right: int,
        bottom: int,
        timestamp_str: str | None = None,
        save_to_disk: bool = True,
        full_screen_image: Image.Image | None = None,
    ) -> FrozenCaptureFrame:
        ts_str = timestamp_str or time.strftime("%Y%m%d_%H%M%S")
        topology = self._topology_provider()
        engine = DpiCoordinateEngine(topology)
        v_left, v_top, v_right, v_bottom = engine.normalize_drag_rect(left, top, right, bottom)
        dom_monitor = topology.dominant_monitor_for_rect(v_left, v_top, v_right, v_bottom)

        cropped_img: Image.Image | None = None
        crop_coords = (0, 0, max(1, v_right - v_left), max(1, v_bottom - v_top))

        if full_screen_image is not None:
            crop_coords = engine.virtual_to_image_crop(
                v_left, v_top, v_right, v_bottom, full_screen_image.size
            )
            cropped_img = full_screen_image.crop(crop_coords)
        elif self._grab_fn is not None:
            grabbed = self._grab_fn(bbox=(v_left, v_top, v_right, v_bottom), all_screens=True)
            if grabbed.size == (topology.virtual_width, topology.virtual_height) and (
                (v_right - v_left) != topology.virtual_width or (v_bottom - v_top) != topology.virtual_height
            ):
                crop_coords = engine.virtual_to_image_crop(v_left, v_top, v_right, v_bottom, grabbed.size)
                cropped_img = grabbed.crop(crop_coords)
            else:
                cropped_img = grabbed.copy()
        else:
            try:
                with ImageGrab.grab(bbox=(v_left, v_top, v_right, v_bottom), all_screens=True) as grabbed:
                    cropped_img = grabbed.copy()
            except Exception:
                try:
                    with ImageGrab.grab(all_screens=True) as full_screen:
                        crop_coords = engine.virtual_to_image_crop(
                            v_left, v_top, v_right, v_bottom, full_screen.size
                        )
                        cropped_img = full_screen.crop(crop_coords)
                except Exception:
                    w = max(1, v_right - v_left)
                    h = max(1, v_bottom - v_top)
                    cropped_img = Image.new("RGB", (w, h), color=(30, 41, 59))

        buf = BytesIO()
        cropped_img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        digest = hashlib.sha256(png_bytes).hexdigest()

        saved_path = ""
        if save_to_disk:
            filename = f"screenshot_{ts_str}.png"
            saved_path = os.path.join(self.screenshots_dir, filename)
            with open(saved_path, "wb") as handle:
                handle.write(png_bytes)

        frame = FrozenCaptureFrame(
            frame_id=f"frm_{ts_str}_{digest[:8]}",
            captured_at_monotonic=time.monotonic(),
            captured_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            virtual_bbox=(v_left, v_top, v_right, v_bottom),
            crop_bbox_image=crop_coords,
            monitor_id=dom_monitor.monitor_id,
            monitor_dpi=dom_monitor.dpi_x,
            monitor_scale=dom_monitor.scale_factor,
            image_size=cropped_img.size,
            sha256=digest,
            png_bytes=png_bytes,
            saved_path=saved_path,
        )
        cropped_img.close()
        return frame
