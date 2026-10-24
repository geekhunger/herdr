import AppKit
import AVKit
import AVFoundation

final class PreviewApp: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var panel: NSPanel!
    var localMonitor: Any?
    var globalMonitor: Any?
    var previousApp: NSRunningApplication?
    var closing = false
    var player: AVPlayer?
    var playerStatus: NSKeyValueObservation?

    func close() {
        guard !closing else { return }
        closing = true
        if let monitor = localMonitor { NSEvent.removeMonitor(monitor) }
        if let monitor = globalMonitor { NSEvent.removeMonitor(monitor) }
        player?.pause()
        player?.replaceCurrentItem(with: nil)
        panel.orderOut(nil)
        // An outside click should keep focus where the user clicked.
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier {
            previousApp?.activate(options: [])
        }
        NSApp.terminate(nil)
    }
    func windowWillClose(_ notification: Notification) { close() }
    func applicationDidResignActive(_ notification: Notification) { close() }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let args = CommandLine.arguments
        previousApp = NSWorkspace.shared.frontmostApplication
        let error = args.count > 2 && args[1] == "--error" ? args[2] : nil
        let video = args.count > 2 && args[1] == "--video" ? args[2] : nil
        let image: NSImage?
        if args.count > 2 && args[1] == "--file" {
            image = NSImage(contentsOfFile: args[2])
        } else if error == nil && video == nil {
            image = NSImage(pasteboard: .general)
        } else { image = nil }
        let screen = NSScreen.screens.first { $0.frame.contains(NSEvent.mouseLocation) } ?? NSScreen.main!
        let available = screen.visibleFrame
        let imageSize = video != nil ? NSSize(width: 960, height: 540) : image?.size ?? NSSize(width: 640, height: 180)
        let scale = min(1.0, min((available.width * 0.8 - 40) / max(1, imageSize.width), (available.height * 0.8 - 65) / max(1, imageSize.height)))
        let size = NSSize(width: max(380, imageSize.width * scale + 40), height: max(160, imageSize.height * scale + 65))
        panel = NSPanel(contentRect: NSRect(x: available.midX - size.width / 2, y: available.midY - size.height / 2, width: size.width, height: size.height), styleMask: [.titled, .closable, .fullSizeContentView], backing: .buffered, defer: false)
        panel.title = video == nil ? "Herdr · Image preview" : "Herdr · Video preview"
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isReleasedWhenClosed = false
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = true
        panel.delegate = self
        panel.appearance = NSAppearance(named: .darkAqua)
        let content = panel.contentView!
        if let video = video {
            let url = video.hasPrefix("https://") || video.hasPrefix("http://") ? URL(string: video) : URL(fileURLWithPath: video)
            if let url = url {
                let item = AVPlayerItem(url: url)
                let player = AVPlayer(playerItem: item)
                self.player = player
                let view = AVPlayerView(frame: NSRect(x: 20, y: 38, width: size.width - 40, height: size.height - 65))
                view.player = player
                view.controlsStyle = .inline
                view.videoGravity = .resizeAspect
                view.autoresizingMask = [.width, .height]
                content.addSubview(view)
                let status = NSTextField(labelWithString: "Loading video…")
                status.frame = NSRect(x: 30, y: size.height / 2, width: size.width - 60, height: 22)
                status.alignment = .center
                status.textColor = .secondaryLabelColor
                content.addSubview(status)
                playerStatus = item.observe(\.status, options: [.initial, .new]) { [weak status] item, _ in
                    DispatchQueue.main.async {
                        status?.isHidden = item.status == .readyToPlay
                        if item.status == .failed { status?.stringValue = "This video cannot be played by macOS." }
                    }
                }
                player.play()
            }
        } else if let image = image {
            let view = NSImageView(frame: NSRect(x: 20, y: 38, width: size.width - 40, height: size.height - 65))
            view.image = image
            view.imageScaling = .scaleProportionallyDown
            view.autoresizingMask = [.width, .height]
            content.addSubview(view)
        } else {
            let label = NSTextField(wrappingLabelWithString: error ?? "No image on the clipboard.")
            label.frame = NSRect(x: 25, y: 45, width: size.width - 50, height: size.height - 85)
            label.textColor = .labelColor
            content.addSubview(label)
        }
        let hint = NSTextField(labelWithString: "Esc or click outside to close")
        hint.frame = NSRect(x: 20, y: 12, width: size.width - 40, height: 18)
        hint.alignment = .center
        hint.textColor = .secondaryLabelColor
        hint.font = .systemFont(ofSize: 12)
        content.addSubview(hint)
        localMonitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .leftMouseDown, .rightMouseDown]) { [weak self] event in
            if event.type == .keyDown && event.keyCode == 53 { self?.close(); return nil }
            if event.type == .keyDown && event.keyCode == 49, let player = self?.player {
                if player.rate == 0 { player.play() } else { player.pause() }
                return nil
            }
            if event.type != .keyDown && event.window !== self?.panel { self?.close(); return nil }
            return event
        }
        globalMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in self?.close() }
        NSApp.setActivationPolicy(.accessory)
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
    }
}
let delegate = PreviewApp()
let app = NSApplication.shared
app.delegate = delegate
app.run()
