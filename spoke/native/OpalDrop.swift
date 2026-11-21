import CoreGraphics
import Foundation

@available(macOS 14, *)

enum OpalDrop {
    struct VoiceFrame {
        let phase: CGFloat
        let shell: CGPath

        init(phase: CGFloat, level: CGFloat) {
            self.phase = phase
            self.shell = OpalDrop.outline(phase: phase, level: level)
        }
    }

    private static let space = CGColorSpace(name: CGColorSpace.sRGB)!
    private static let core = gradient([(0xA6C7ED, 1), (0xBA9FDB, 1), (0x798DAD, 1)])
    private static let rim = gradient([(0x101829, 1), (0x309A94, 1), (0x60ECF1, 1),
                                       (0x6884DE, 1), (0xF4A4DD, 1), (0xFEA958, 1), (0x101829, 1)])
    private static let cyan = fade(0x50E0EF, alpha: 0.86)
    private static let dark = fade(0x20293B, alpha: 0.61)
    private static let coral = fade(0xFA9A8B, alpha: 0.86)
    private static let reflection = fade(0xF7ECFF)
    private static let blush = fade(0xFA9A8B, alpha: 0.39)
    private static let meniscus = gradient([(0xFFFFFF, 0), (0x50E0EF, 0.25), (0xFFF8EB, 0.8),
                                             (0xFA9A8B, 0.15), (0xFFFFFF, 0)])
    private static let shadow = color(0x10131E, alpha: 0.27)
    private static let outlineColor = color(0xEFF9FF, alpha: 0.33)

    static func color(_ hex: UInt32, alpha: CGFloat = 1) -> CGColor {
        CGColor(red: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255,
                blue: CGFloat(hex & 255) / 255, alpha: alpha)
    }

    private static func gradient(_ stops: [(UInt32, CGFloat)]) -> CGGradient {
        CGGradient(colorsSpace: space, colors: stops.map { color($0.0, alpha: $0.1) } as CFArray, locations: nil)!
    }

    private static func fade(_ hex: UInt32, alpha: CGFloat = 1) -> CGGradient {
        gradient([(hex, alpha), (hex, 0)])
    }

    static func draw(_ context: CGContext, size: CGFloat, voice: VoiceFrame, loadingTime: CGFloat) {
        let radius = size * 0.36
        let turn = (voice.phase + loadingTime * 0.8) * 0.78
        let progress = min(1, loadingTime / 0.58)
        let blend = progress * progress * (3 - 2 * progress)
        let shell = voice.shell
        context.saveGState()
        defer { context.restoreGState() }
        context.scaleBy(x: radius, y: -radius)

        context.saveGState()
        context.setShadow(offset: CGSize(width: 0, height: -size * 0.033), blur: size * 0.05, color: shadow)
        context.setFillColor(outlineColor)
        context.addPath(shell)
        context.fillPath()
        context.restoreGState()

        context.saveGState()
        context.addPath(shell)
        context.clip()
        context.drawLinearGradient(core, start: CGPoint(x: -0.79, y: -0.71), end: CGPoint(x: 0.73, y: 0.82),
                                   options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
        context.setBlendMode(.sourceAtop)
        for (index, reflection) in [cyan, dark, coral, dark].enumerated() {
            let step = CGFloat(index)
            let angle = turn * (index.isMultiple(of: 2) ? 1.1 : -0.88) + step * 1.61
            context.saveGState()
            context.rotate(by: angle)
            context.scaleBy(x: 1, y: 0.48 + 0.12 * sin(turn + step))
            pool(context, reflection, at: CGPoint(x: 1.01, y: -0.1), radius: 0.69)
            context.restoreGState()
        }
        context.setAlpha(0.43 + 0.10 * blend)
        pool(context, reflection, at: CGPoint(x: -0.34 + 0.19 * sin(turn * 0.9), y: -0.26), radius: 0.88)
        context.setAlpha(1)
        pool(context, blush, at: CGPoint(x: 0.36 + 0.32 * sin(turn), y: 0.32), radius: 0.68)

        context.saveGState()
        clipStroke(context, shell, width: 0.133 + 0.027 * sin(turn * 2.1))
        CGContextDrawConicGradient(context, rim, CGPoint(x: -0.08, y: 0.07), turn)
        context.restoreGState()

        clipStroke(context, waterline(turn: turn), width: 0.022)
        context.setAlpha(0.46 + 0.23 * blend)
        context.drawLinearGradient(meniscus, start: CGPoint(x: -1.15, y: 0), end: CGPoint(x: 1.15, y: 0),
                                   options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
        context.restoreGState()

        context.setStrokeColor(outlineColor)
        context.setLineWidth(0.013)
        context.addPath(shell)
        context.strokePath()
    }

    private static func outline(phase: CGFloat, level: CGFloat) -> CGPath {
        let shell = CGMutablePath()
        for index in 0..<96 {
            let angle = CGFloat(index) / 96 * 2 * .pi
            let a = angle - 0.42 * sin(phase * 0.92)
            let fold = 0.059 * cos(2 * a + phase) + 0.071 * sin(3 * a - phase * 1.3)
                     + 0.042 * cos(5 * a + phase * 1.17) + 0.019 * sin(7 * a + phase * 0.86)
            let radius = (1 + fold * (0.88 + 0.75 * level)) * (1 + 0.066 * level)
            let point = CGPoint(x: radius * cos(angle), y: radius * sin(angle))
            if index == 0 { shell.move(to: point) } else { shell.addLine(to: point) }
        }
        shell.closeSubpath()
        return shell
    }

    private static func waterline(turn: CGFloat) -> CGPath {
        let path = CGMutablePath()
        for index in 0...32 {
            let x = CGFloat(index) / 32 * 2.35 - 1.175
            let y = 0.14 * sin(x * 3.28 + turn * 2.1) + 0.11 * sin(turn) + x * 0.21 * sin(turn * 1.24)
            let point = CGPoint(x: x, y: y)
            if index == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }
        return path
    }

    private static func pool(_ context: CGContext, _ gradient: CGGradient, at center: CGPoint, radius: CGFloat) {
        context.drawRadialGradient(gradient, startCenter: center, startRadius: 0, endCenter: center,
                                   endRadius: radius, options: [])
    }

    private static func clipStroke(_ context: CGContext, _ path: CGPath, width: CGFloat) {
        context.setLineWidth(width)
        context.addPath(path)
        context.replacePathWithStrokedPath()
        context.clip()
    }
}
