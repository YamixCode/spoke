import AppKit
import QuartzCore

@available(macOS 14, *)
@objc(SpokeSpeechMotionView)
public final class SpokeSpeechMotionView: NSView {
    private var clock: CADisplayLink?
    private var hasMotionState = false
    private var lastFrame: CFTimeInterval = 0
    private var phase: CGFloat = 0
    private var target: CGFloat = 0
    private var envelope: CGFloat = 0
    private var lastVoiceFrame = OpalDrop.VoiceFrame(phase: 0, level: 0)
    private var loadingTime: CGFloat = 0
    private var processing = false
    private var locked = false
    private var reduceMotion = false

    @objc public func updateVoice(_ level: Double, processing: Bool, locked: Bool) {
        target = processing ? 0 : min(1, sqrt(max(0, level - 0.004)) * 4.5)
        if self.processing != processing {
            self.processing = processing
            if processing {
                loadingTime = 0
            }
            needsDisplay = true
        }
        if self.locked != locked {
            self.locked = locked
            needsDisplay = true
        }
    }

    public override func hitTest(_ point: NSPoint) -> NSView? { nil }

    @objc public func startMotion() {
        guard clock == nil else { return }
        reduceMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        if !hasMotionState {
            envelope = target * 0.45
            lastVoiceFrame = OpalDrop.VoiceFrame(phase: 0, level: envelope)
            loadingTime = 0
            hasMotionState = true
        }
        lastFrame = 0
        let link = displayLink(target: self, selector: #selector(displayTick(_:)))
        let refresh = Float(max(30, window?.screen?.maximumFramesPerSecond ?? 60))
        link.preferredFrameRateRange = CAFrameRateRange(minimum: min(60, refresh), maximum: refresh, preferred: refresh)
        link.add(to: .main, forMode: .common)
        clock = link
        needsDisplay = true
    }

    @objc public func stopMotion() {
        clock?.invalidate()
        clock = nil
        hasMotionState = false
        target = 0
        envelope = 0
        phase = 0
    }

    public override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window == nil {
            clock?.invalidate()
            clock = nil
        }
    }

    @objc private func displayTick(_ link: CADisplayLink) {
        let now = link.targetTimestamp
        let elapsed = lastFrame == 0 ? link.duration : min(0.05, max(0, now - lastFrame))
        lastFrame = now
        let interval = target > envelope ? 0.045 : 0.15
        let next = envelope + (target - envelope) * (1 - exp(-elapsed / interval))
        if !reduceMotion {
            if processing {
                loadingTime += elapsed
            } else {
                phase += elapsed * (0.8 + 1.4 * next)
            }
        }
        if !reduceMotion || abs(next - envelope) > 0.001 {
            envelope = next
            needsDisplay = true
        }
    }

    public override func draw(_ dirtyRect: NSRect) {
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        context.saveGState()
        defer { context.restoreGState() }
        context.translateBy(x: bounds.midX, y: bounds.midY)
        context.setLineCap(.round)
        context.setLineJoin(.round)
        if !processing {
            lastVoiceFrame = OpalDrop.VoiceFrame(phase: phase, level: envelope)
        }
        OpalDrop.draw(context, size: bounds.height, voice: lastVoiceFrame,
                       loadingTime: processing ? loadingTime : 0)
        if locked { drawControls(context) }
    }

    private func drawControls(_ context: CGContext) {
        let offset = bounds.width / 2 - 18
        for finish in [false, true] {
            let x = finish ? offset : -offset
            let circle = CGRect(x: x - 8, y: -8, width: 16, height: 16)
            context.setFillColor(OpalDrop.color(finish ? 0xF6F1ED : 0x383337, alpha: 0.96))
            context.fillEllipse(in: circle)
            context.setStrokeColor(OpalDrop.color(0xA69CA3, alpha: 0.72))
            context.setLineWidth(0.65)
            context.strokeEllipse(in: circle)
            context.setStrokeColor(OpalDrop.color(finish ? 0x362D35 : 0xFFFFFF))
            context.setLineWidth(1.35)
            if finish {
                context.move(to: CGPoint(x: x - 3, y: 0))
                context.addLine(to: CGPoint(x: x - 1, y: -2))
                context.addLine(to: CGPoint(x: x + 3, y: 2.5))
            } else {
                context.move(to: CGPoint(x: x - 2.25, y: -2.25))
                context.addLine(to: CGPoint(x: x + 2.25, y: 2.25))
                context.move(to: CGPoint(x: x - 2.25, y: 2.25))
                context.addLine(to: CGPoint(x: x + 2.25, y: -2.25))
            }
            context.strokePath()
        }
    }
}
