"""Плашка записи с индикатором голоса и обработки."""
from __future__ import annotations

import math
import time
from collections import deque

import Quartz
from AppKit import (NSBackingStoreBuffered, NSBezierPath, NSColor, NSMakeRect, NSPanel, NSScreen, NSStatusWindowLevel, NSView,
                    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel)

HOLD_WIDTH, LOCKED_WIDTH, WORK_WIDTH, HEIGHT = 70.0, 100.0, 96.0, 28.0
BUTTON, BUTTON_INSET = 16.0, 7.0
BAR_PITCH, BAR_WIDTH, BAR_MIN, BAR_MAX, SPOKES = 4.0, 1.6, 1.6, 13.0, 8
BAR_GAIN = (0.48, 0.62, 0.77, 0.92, 1.0, 0.92, 0.77, 0.62, 0.48)
BARS = len(BAR_GAIN)
BAR_LAG = (2, 1, 1, 0, 0, 0, 1, 1, 2)
LEVEL_SAMPLES = max(BAR_LAG) + 1
RECORD, WORK = "record", "work"


def _white(alpha: float) -> NSColor:
    return NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha)


class _PillView(NSView):
    mode = RECORD
    locked = False
    levels = None
    on_cancel = None
    on_finish = None

    def acceptsFirstMouse_(self, event):
        return True

    def _button_rects(self):
        width = self.bounds().size.width
        y = (HEIGHT - BUTTON) / 2
        return NSMakeRect(BUTTON_INSET, y, BUTTON, BUTTON), NSMakeRect(width - BUTTON_INSET - BUTTON, y, BUTTON, BUTTON)

    def mouseDown_(self, event):
        if not (self.mode == RECORD and self.locked):
            return
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        cancel, finish = self._button_rects()
        for rect, handler in ((cancel, self.on_cancel), (finish, self.on_finish)):
            if rect.origin.x - 2 <= point.x <= rect.origin.x + rect.size.width + 2 and handler:
                handler()
                return

    def drawRect_(self, rect):
        width = self.bounds().size.width
        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), HEIGHT / 2, HEIGHT / 2)
        NSColor.blackColor().setFill()
        pill.fill()
        if self.mode == RECORD and self.locked:
            cancel, finish = self._button_rects()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.29, 0.27, 0.25, 1.0).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(cancel).fill()
            _white(1.0).setStroke()
            cross = NSBezierPath.bezierPath()
            cx, cy, r = cancel.origin.x + BUTTON / 2, cancel.origin.y + BUTTON / 2, 2.6
            cross.moveToPoint_((cx - r, cy - r))
            cross.lineToPoint_((cx + r, cy + r))
            cross.moveToPoint_((cx - r, cy + r))
            cross.lineToPoint_((cx + r, cy - r))
            cross.setLineWidth_(1.35)
            cross.setLineCapStyle_(1)
            cross.stroke()
            _white(1.0).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(finish).fill()
            NSColor.blackColor().setStroke()
            tick = NSBezierPath.bezierPath()
            fx, fy = finish.origin.x + BUTTON / 2, finish.origin.y + BUTTON / 2
            tick.moveToPoint_((fx - 3.1, fy - 0.1))
            tick.lineToPoint_((fx - 0.9, fy - 2.4))
            tick.lineToPoint_((fx + 3.3, fy + 2.7))
            tick.setLineWidth_(1.45)
            tick.setLineCapStyle_(1)
            tick.stroke()
        working = self.mode == WORK
        left = 17.0 if working else (width - (BARS - 1) * BAR_PITCH - BAR_WIDTH) / 2
        now = time.monotonic()
        for i in range(BARS):
            if working:
                height = BAR_MIN
                _white(0.6).setFill()
            else:
                level = self.levels[-1 - BAR_LAG[i]] if self.levels else 0.0
                height = BAR_MIN + level * BAR_GAIN[i] * (BAR_MAX - BAR_MIN)
                _white(1.0).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(left + i * BAR_PITCH, (HEIGHT - height) / 2, BAR_WIDTH, height), BAR_WIDTH / 2, BAR_WIDTH / 2).fill()
        if working:
            cx, cy, phase = width - 21.3, HEIGHT / 2, int(now * 11) % SPOKES
            for k in range(SPOKES):
                angle = math.pi / 2 - k * 2 * math.pi / SPOKES
                age = (phase - k) % SPOKES
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.63, 0.61, 0.76, max(0.12, 0.68 - age * 0.09)).setStroke()
                spoke = NSBezierPath.bezierPath()
                spoke.moveToPoint_((cx + 4.4 * math.cos(angle), cy + 4.4 * math.sin(angle)))
                spoke.lineToPoint_((cx + 8.2 * math.cos(angle), cy + 8.2 * math.sin(angle)))
                spoke.setLineWidth_(2.05)
                spoke.setLineCapStyle_(1)
                spoke.stroke()


class Overlay:
    def __init__(self, bottom_margin: float = 14.0, on_cancel=None, on_finish=None):
        self.bottom_margin = bottom_margin
        self._levels = deque([0.0] * LEVEL_SAMPLES, maxlen=LEVEL_SAMPLES)
        self._visible = False
        self._clickable = False
        self._width = HOLD_WIDTH
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, HOLD_WIDTH, HEIGHT), NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(False)
        self.panel.setLevel_(NSStatusWindowLevel)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary | NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.view = _PillView.alloc().initWithFrame_(NSMakeRect(0, 0, HOLD_WIDTH, HEIGHT))
        self.view.levels = list(self._levels)
        self.view.on_cancel, self.view.on_finish = on_cancel, on_finish
        self.panel.setContentView_(self.view)

    def set_bottom_margin(self, margin: float) -> None:
        if self.bottom_margin == margin:
            return
        self.bottom_margin = margin
        if self._visible:
            self._place(self._width)

    @staticmethod
    def _dock_visible() -> bool:
        for window in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []:
            if window.get("kCGWindowOwnerName") == "Dock" and window.get("kCGWindowLayer") == 20:
                return True
        return False

    def _place(self, width: float) -> None:
        screen = NSScreen.mainScreen() or NSScreen.screens()[0]
        base = screen.visibleFrame() if self._dock_visible() else screen.frame()
        self.panel.setFrame_display_(NSMakeRect(base.origin.x + (base.size.width - width) / 2, base.origin.y + self.bottom_margin, width, HEIGHT), True)
        self.view.setFrame_(NSMakeRect(0, 0, width, HEIGHT))
        self._width = width

    def update(self, mode: str | None, level: float = 0.0, locked: bool = False) -> None:
        if mode is None:
            if self._visible:
                self.panel.orderOut_(None)
                self._visible = False
                self._levels.extend([0.0] * LEVEL_SAMPLES)
            return
        clickable = mode == RECORD and locked
        width = LOCKED_WIDTH if clickable else WORK_WIDTH if mode == WORK else HOLD_WIDTH
        if not self._visible or width != self._width:
            self._place(width)
        if not self._visible:
            self.panel.orderFrontRegardless()
            self._visible = True
        if clickable != self._clickable:
            self.panel.setIgnoresMouseEvents_(not clickable)
            self._clickable = clickable
        amplitude = min(1.0, math.sqrt(max(0.0, level - 0.004)) * 4.5)
        previous = self._levels[-1]
        response = 0.80 if amplitude > previous else 0.52
        self._levels.append(previous + (amplitude - previous) * response)
        self.view.mode, self.view.locked, self.view.levels = mode, locked, list(self._levels)
        self.view.setNeedsDisplay_(True)
