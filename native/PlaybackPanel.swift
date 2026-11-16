import AppKit

final class JumpSlider: NSSlider {
    private(set) var tracking = false
    override func mouseDown(with event: NSEvent) {
        guard isEnabled, let window else { return }
        tracking = true
        var current = event
        while true {
            let point = convert(current.locationInWindow, from: nil)
            let fraction = max(0, min(1, (point.x - 9) / max(1, bounds.width - 18)))
            doubleValue = minValue + fraction * (maxValue - minValue)
            if current.type == .leftMouseUp { break }
            guard let next = window.nextEvent(matching: [.leftMouseDragged, .leftMouseUp]) else { break }
            current = next
        }
        tracking = false
        sendAction(action, to: target)
    }
}

final class PlaybackPanel: NSPanel {
    var onCommand: ((String, Double?) -> Void)?
    private let titleLabel = NSTextField(labelWithString: "No cast in progress")
    private let destination = NSTextField(labelWithString: "Apple TV")
    private let positionLabel = NSTextField(labelWithString: "0:00")
    private let durationLabel = NSTextField(labelWithString: "0:00")
    private let statusLabel = NSTextField(wrappingLabelWithString: "Start a video from the Chrome extension.")
    private let slider = JumpSlider(value: 0, minValue: 0, maxValue: 1, target: nil, action: nil)
    private let pauseButton = NSButton(title: "Pause", target: nil, action: nil)
    private var buttons: [NSButton] = []
    private var position = 0.0

    init() {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 380, height: 210), styleMask: [.titled, .closable, .utilityWindow], backing: .buffered, defer: false)
        title = "Apple TV controls"
        isReleasedWhenClosed = false
        level = .floating
        center()
        guard let view = contentView else { return }
        titleLabel.frame = NSRect(x: 18, y: 173, width: 344, height: 20)
        titleLabel.font = .systemFont(ofSize: 14, weight: .semibold)
        titleLabel.lineBreakMode = .byTruncatingTail
        destination.frame = NSRect(x: 18, y: 150, width: 344, height: 17)
        destination.font = .systemFont(ofSize: 11)
        destination.textColor = .secondaryLabelColor
        slider.frame = NSRect(x: 15, y: 113, width: 350, height: 26)
        slider.target = self
        slider.action = #selector(jump)
        slider.isContinuous = false
        slider.setAccessibilityLabel("Playback position")
        slider.toolTip = "Click or drag to seek on Apple TV"
        positionLabel.frame = NSRect(x: 18, y: 94, width: 150, height: 16)
        durationLabel.frame = NSRect(x: 212, y: 94, width: 150, height: 16)
        durationLabel.alignment = .right
        for label in [positionLabel, durationLabel] {
            label.font = .monospacedDigitSystemFont(ofSize: 11, weight: .regular)
            label.textColor = .secondaryLabelColor
        }
        let back = NSButton(title: "−10 s", target: self, action: #selector(backward))
        pauseButton.target = self
        pauseButton.action = #selector(toggle)
        let forward = NSButton(title: "+10 s", target: self, action: #selector(forward))
        let stop = NSButton(title: "Stop", target: self, action: #selector(stopPlayback))
        buttons = [back, pauseButton, forward, stop]
        for (index, button) in buttons.enumerated() {
            button.frame = NSRect(x: 17 + index * 88, y: 47, width: 82, height: 30)
            button.bezelStyle = .rounded
            view.addSubview(button)
        }
        statusLabel.frame = NSRect(x: 18, y: 8, width: 344, height: 30)
        statusLabel.font = .systemFont(ofSize: 11)
        statusLabel.textColor = .secondaryLabelColor
        for control in [titleLabel, destination, slider, positionLabel, durationLabel, statusLabel] as [NSView] { view.addSubview(control) }
        update([:], busy: false)
    }

    func update(_ state: [String: Any], busy: Bool) {
        let active = state["casting"] as? Bool == true
        let duration = state["duration"] as? Double ?? 0
        position = state["position"] as? Double ?? 0
        titleLabel.stringValue = state["title"] as? String ?? "No cast in progress"
        destination.stringValue = state["selectedName"] as? String ?? "Apple TV"
        if !slider.tracking {
            slider.maxValue = max(duration, 1)
            slider.doubleValue = min(position, slider.maxValue)
        }
        slider.isEnabled = active && duration > 0 && !busy
        positionLabel.stringValue = Self.time(position)
        durationLabel.stringValue = Self.time(duration)
        pauseButton.title = state["paused"] as? Bool == true ? "Play" : "Pause"
        for button in buttons { button.isEnabled = active && !busy }
        statusLabel.stringValue = state["error"] as? String ?? (busy ? "Seeking…" : active ? "Click the timeline to seek to that position." : "Start a video from the Chrome extension.")
        statusLabel.textColor = state["error"] as? String == nil ? .secondaryLabelColor : .systemRed
    }

    private static func time(_ value: Double) -> String {
        guard value.isFinite else { return "0:00" }
        let seconds = max(0, Int(value))
        if seconds >= 3600 { return String(format: "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60) }
        return String(format: "%d:%02d", seconds / 60, seconds % 60)
    }
    @objc private func jump() { onCommand?("seek", slider.doubleValue) }
    @objc private func backward() { onCommand?("seek", max(0, position - 10)) }
    @objc private func forward() { onCommand?("seek", position + 10) }
    @objc private func toggle() { onCommand?("toggle", nil) }
    @objc private func stopPlayback() { onCommand?("stop", nil) }
}
