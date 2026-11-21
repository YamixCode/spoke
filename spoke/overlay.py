"""Плашка записи с отдельным оформлением голоса и обработки."""
from __future__ import annotations

import ctypes
import time

import objc
import Quartz
from AppKit import (NSBackingStoreBuffered, NSColor, NSMakeRect, NSPanel, NSScreen, NSStatusWindowLevel, NSView,
                    NSWindowCollectionBehaviorManaged, NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
                    NSWorkspace, NSWorkspaceActiveSpaceDidChangeNotification)
from Foundation import NSOperationQueue

from spoke.native_bridge import load_library

DEFAULT_SIZE, LOCKED_EXTRA_WIDTH = 60.0, 64.0
BUTTON_CENTER, BUTTON_TARGET = 18, 11
SPACE_SETTLE_INTERVAL = 0.075
SPACE_CHECK_INTERVAL = 0.15
DESKTOP_SPACE, FULLSCREEN_SPACE = 0, 4
RECORD, WORK = "record", "work"


class _PillView(NSView):
    on_cancel = None
    on_finish = None

    def acceptsFirstMouse_(self, event):
        return True

    def mouseDown_(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        if abs(point.y - self.bounds().size.height / 2) > BUTTON_TARGET:
            return
        width = self.bounds().size.width
        for center, handler in ((BUTTON_CENTER, self.on_cancel), (width - BUTTON_CENTER, self.on_finish)):
            if handler and abs(point.x - center) <= BUTTON_TARGET:
                handler()
                return


class Overlay:
    def __init__(self, bottom_margin: float = 14.0, on_cancel=None, on_finish=None):
        self.bottom_margin = bottom_margin
        self._active_space = load_library().spoke_active_space
        self._active_space.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_int32)]
        self._active_space.restype = ctypes.c_uint64
        self._size = DEFAULT_SIZE
        self._reveal_after = 0.0
        self._space_observer = None
        self._space = None
        self._next_space_check = 0.0
        self._base = None
        self.panel = None
        frame = NSMakeRect(0, 0, self._size, self._size)
        self.view = _PillView.alloc().initWithFrame_(frame)
        self.motion = objc.lookUpClass("SpokeSpeechMotionView").alloc().initWithFrame_(frame)
        self.motion.setWantsLayer_(True)
        self.view.addSubview_(self.motion)
        self.view.on_cancel, self.view.on_finish = on_cancel, on_finish

    def set_size(self, size: int) -> None:
        self._size = float(size)

    def set_bottom_margin(self, margin: float) -> None:
        self.bottom_margin = margin

    def _space_changed(self, _notification) -> None:
        if self._space_observer is not None:
            self._check_space(time.monotonic(), notified=True)

    def _close_panel(self) -> None:
        if self.panel is not None:
            self.panel.orderOut_(None)
            self.panel.setContentView_(None)
            self.panel.close()
            self.panel = None

    def _open_panel(self, frame) -> None:
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        self.panel.setReleasedWhenClosed_(False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(False)
        self.panel.setLevel_(NSStatusWindowLevel)
        self.panel.setHidesOnDeactivate_(False)
        # A managed panel belongs to this Space. The outgoing panel must not be
        # composited onto the destination desktop during the system transition.
        self.panel.setCollectionBehavior_(NSWindowCollectionBehaviorManaged | NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.panel.setContentView_(self.view)

    @staticmethod
    def _desktop_dock_visible(screen: NSScreen) -> bool:
        display = Quartz.CGDisplayBounds(screen.deviceDescription()["NSScreenNumber"])
        dock_layer = Quartz.CGWindowLevelForKey(Quartz.kCGDockWindowLevelKey)
        normal_layer = Quartz.CGWindowLevelForKey(Quartz.kCGNormalWindowLevelKey)
        options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        dock_visible = False
        found_foreground = False
        for window in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
            layer = window.get("kCGWindowLayer")
            is_dock = layer == dock_layer and window.get("kCGWindowOwnerName") == "Dock"
            if (not is_dock and (layer != normal_layer or found_foreground)) or not window.get("kCGWindowAlpha"):
                continue
            bounds = window["kCGWindowBounds"]
            frame = Quartz.CGRectMake(bounds["X"], bounds["Y"], bounds["Width"], bounds["Height"])
            if not Quartz.CGRectIntersectsRect(display, frame):
                continue
            if is_dock:
                dock_visible = True
            else:
                found_foreground = True
                # A fullscreen window may already be on screen while the outgoing Dock is still listed.
                if Quartz.CGRectEqualToRect(display, frame):
                    return False
            if found_foreground and dock_visible:
                break
        return dock_visible

    def _check_space(self, now: float, *, notified: bool = False) -> None:
        screen = NSScreen.mainScreen() or NSScreen.screens()[0]
        display = screen.deviceDescription()["NSScreenNumber"]
        kind = ctypes.c_int32(-1)
        space_id = self._active_space(display, ctypes.byref(kind))
        target_space = (display, space_id)
        if (self._space is not None and self._space != target_space) or (notified and not space_id):
            self._close_panel()
            self._reveal_after = now + SPACE_SETTLE_INTERVAL
        self._space = target_space
        self._next_space_check = now + SPACE_CHECK_INTERVAL

        frame = screen.frame()
        visible = screen.visibleFrame()
        if kind.value == DESKTOP_SPACE:
            self._base = visible
        elif kind.value == FULLSCREEN_SPACE:
            self._base = frame
        else:
            # Fallback for macOS versions where the active Space is unavailable.
            has_dock_inset = visible.origin.y > frame.origin.y or visible.size.width < frame.size.width
            self._base = visible if has_dock_inset and self._desktop_dock_visible(screen) else frame

    def update(self, mode: str | None, level: float = 0.0, locked: bool = False) -> None:
        if mode is None:
            if self._space_observer is not None:
                NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self._space_observer)
                self._space_observer = None
                self._reveal_after = 0.0
                self._space = None
                self._next_space_check = 0.0
                self.motion.stopMotion()
                self._close_panel()
            return

        processing = mode == WORK
        clickable = mode == RECORD and locked
        self.motion.updateVoice_processing_locked_(level, processing, clickable)
        if self._space_observer is None:
            center = NSWorkspace.sharedWorkspace().notificationCenter()
            self._space_observer = center.addObserverForName_object_queue_usingBlock_(
                NSWorkspaceActiveSpaceDidChangeNotification, None, NSOperationQueue.mainQueue(), self._space_changed)
        now = time.monotonic()
        if now >= self._next_space_check or (self.panel is None and now >= self._reveal_after):
            self._check_space(now)
        if now < self._reveal_after:
            return

        opening = self.panel is None
        base = self._base
        height = self._size
        width = height + LOCKED_EXTRA_WIDTH if clickable else height
        frame = NSMakeRect(base.origin.x + (base.size.width - width) / 2, base.origin.y + self.bottom_margin, width, height)
        if opening:
            self._open_panel(frame)
        elif frame != self.panel.frame():
            self.panel.setFrame_display_(frame, True)
        content = NSMakeRect(0, 0, width, height)
        if content != self.motion.frame():
            self.motion.setFrame_(content)
        if self.panel.ignoresMouseEvents() == clickable:
            self.panel.setIgnoresMouseEvents_(not clickable)
        if opening:
            self.panel.orderFrontRegardless()
            self.motion.startMotion()
