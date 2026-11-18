"""Плашка записи внизу экрана, как у Wispr Flow: компактная тёмная «таблетка» с волной от голоса; при постоянной записи — кнопки ✕ и ✓."""
from __future__ import annotations

import math
import time
from collections import deque

import Quartz
from AppKit import (NSBackingStoreBuffered, NSBezierPath, NSColor, NSMakeRect, NSPanel, NSScreen, NSStatusWindowLevel, NSView,
                    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel)

HOLD_WIDTH, LOCKED_WIDTH, WORK_WIDTH, HEIGHT, BARS, BUTTON = 72.0, 102.0, 96.0, 30.0, 10, 17.0
BAR_PITCH, BAR_WIDTH, BAR_MIN, BAR_MAX, SPOKES = 3.8, 2.0, 2.0, 15.0, 8
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
        return NSMakeRect(7, y, BUTTON, BUTTON), NSMakeRect(width - 7 - BUTTON, y, BUTTON, BUTTON)

    def mouseDown_(self, event):
        if not (self.mode == RECORD and self.locked):
            return
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        cancel, finish = self._button_rects()
        for rect, handler in ((cancel, self.on_cancel), (finish, self.on_finish)):
            if rect.origin.x <= point.x <= rect.origin.x + rect.size.width and handler:
                handler()
                return

    def drawRect_(self, rect):
        width = self.bounds().size.width
        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(0.5, 0.5, width - 1, HEIGHT - 1), (HEIGHT - 1) / 2, (HEIGHT - 1) / 2)
        NSColor.blackColor().setFill()
        pill.fill()
        _white(0.14).setStroke()
        pill.setLineWidth_(1.0)
        pill.stroke()
        if self.mode == RECORD and self.locked:
            cancel, finish = self._button_rects()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.29, 0.27, 0.25, 1.0).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(cancel).fill()
            _white(1.0).setStroke()
            cross = NSBezierPath.bezierPath()
            cx, cy, r = cancel.origin.x + BUTTON / 2, cancel.origin.y + BUTTON / 2, 3.0
            cross.moveToPoint_((cx - r, cy - r))
            cross.lineToPoint_((cx + r, cy + r))
            cross.moveToPoint_((cx - r, cy + r))
            cross.lineToPoint_((cx + r, cy - r))
            cross.setLineWidth_(1.5)
            cross.stroke()
            _white(1.0).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(finish).fill()
            NSColor.blackColor().setStroke()
            tick = NSBezierPath.bezierPath()
            fx, fy = finish.origin.x + BUTTON / 2, finish.origin.y + BUTTON / 2
            tick.moveToPoint_((fx - 3.4, fy - 0.2))
            tick.lineToPoint_((fx - 1.0, fy - 2.8))
            tick.lineToPoint_((fx + 3.6, fy + 3.0))
            tick.setLineWidth_(1.7)
            tick.stroke()
        working = self.mode == WORK
        left = 17.0 if working else (width - (BARS - 1) * BAR_PITCH - BAR_WIDTH) / 2
        now = time.monotonic()
        for i in range(BARS):
            if working:
                height = BAR_MIN
                _white(0.6).setFill()
            else:
                level = self.levels[i] if self.levels else 0.0
                height = BAR_MIN + level * (BAR_MAX - BAR_MIN)
                _white(1.0).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(left + i * BAR_PITCH, (HEIGHT - height) / 2, BAR_WIDTH, height), BAR_WIDTH / 2, BAR_WIDTH / 2).fill()
        if working:
            cx, cy, phase = width - 21.3, HEIGHT / 2, int(now * 11) % SPOKES
            for k in range(SPOKES):
                angle = math.pi / 2 - k * 2 * math.pi / SPOKES
                age = (phase - k) % SPOKES
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.53, 0.48, 0.76, max(0.16, 1.0 - age * 0.15)).setStroke()
                spoke = NSBezierPath.bezierPath()
                spoke.moveToPoint_((cx + 4.4 * math.cos(angle), cy + 4.4 * math.sin(angle)))
                spoke.lineToPoint_((cx + 8.2 * math.cos(angle), cy + 8.2 * math.sin(angle)))
                spoke.setLineWidth_(1.7)
                spoke.setLineCapStyle_(1)
                spoke.stroke()


class Overlay:
    def __init__(self, bottom_margin: float = 14.0, on_cancel=None, on_finish=None):
        self.bottom_margin = bottom_margin
        self._levels = deque([0.0] * BARS, maxlen=BARS)
        self._visible = False
        self._clickable = False
        self._width = HOLD_WIDTH
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, HOLD_WIDTH, HEIGHT), NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(True)
        self.panel.setLevel_(NSStatusWindowLevel)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary | NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.view = _PillView.alloc().initWithFrame_(NSMakeRect(0, 0, HOLD_WIDTH, HEIGHT))
        self.view.levels = list(self._levels)
        self.view.on_cancel, self.view.on_finish = on_cancel, on_finish
        self.panel.setContentView_(self.view)

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
                self._levels.extend([0.0] * BARS)
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
        self._levels.append(min(1.0, math.sqrt(level) * 3.2))
        self.view.mode, self.view.locked, self.view.levels = mode, locked, list(self._levels)
        self.view.setNeedsDisplay_(True)
