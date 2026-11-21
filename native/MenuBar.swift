import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var item: NSStatusItem!
    private var engine: Process?
    private var timer: Timer?
    private var state: [String: Any] = [:]
    private var busy = false
    private var discoverySpinner: NSProgressIndicator?
    private var polling = false
    private var menuOpen = false
    private var playbackPanel: PlaybackPanel?
    private let fm = FileManager.default
    private var folder: URL {
        if let override = ProcessInfo.processInfo.environment["AIRPLAY_FLIXER_HOME"] {
            return URL(fileURLWithPath: override)
        }
        return fm.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Airplay Flixer")
    }
    private var engineURL: URL { Bundle.main.resourceURL!.appendingPathComponent("engine/FlixerEngine") }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "airplay.video", accessibilityDescription: "Airplay Flixer")
        item.button?.image?.isTemplate = true
        item.button?.toolTip = "Airplay Flixer"
        do {
            try fm.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            try registerExtension()
            try startEngine()
        } catch { alert(error.localizedDescription) }
        rebuildMenu()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.refresh() }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.refresh() }
        if !UserDefaults.standard.bool(forKey: "hasShownPlaybackControls") {
            UserDefaults.standard.set(true, forKey: "hasShownPlaybackControls")
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { self.showPlayback() }
        }
    }

    private func registerExtension() throws {
        let id = try String(contentsOf: Bundle.main.resourceURL!.appendingPathComponent("extension-id.txt"), encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
        let origin = "chrome-extension://\(id)/"
        let installation: [String: Any] = ["app_path": Bundle.main.bundlePath, "extension_origin": origin]
        try save(installation, to: folder.appendingPathComponent("installation.json"))
        let host = fm.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Google/Chrome/NativeMessagingHosts")
        try fm.createDirectory(at: host, withIntermediateDirectories: true)
        try save(["name": "com.airplay_flixer.companion", "description": "Airplay Flixer", "path": engineURL.path, "type": "stdio", "allowed_origins": [origin]], to: host.appendingPathComponent("com.airplay_flixer.companion.json"))
    }

    private func save(_ value: [String: Any], to url: URL) throws {
        try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]).write(to: url, options: .atomic)
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    private func startEngine() throws {
        if engine?.isRunning == true { return }
        let process = Process()
        process.executableURL = engineURL
        process.arguments = ["--serve"]
        let log = folder.appendingPathComponent("companion.log")
        if !fm.fileExists(atPath: log.path) { fm.createFile(atPath: log.path, contents: nil) }
        // Bound the journal across launches; it contains no media URLs or cookies.
        if let attrs = try? fm.attributesOfItem(atPath: log.path), (attrs[.size] as? Int ?? 0) > 2_000_000 { try? Data().write(to: log) }
        let handle = try FileHandle(forWritingTo: log)
        try handle.seekToEnd()
        process.standardOutput = handle
        process.standardError = handle
        process.environment = ProcessInfo.processInfo.environment
        try process.run()
        engine = process
    }

    private func rpc(_ command: [String: Any], completion: @escaping (Result<[String: Any], Error>) -> Void) {
        do {
            let runtime = try JSONSerialization.jsonObject(with: Data(contentsOf: folder.appendingPathComponent("runtime.json"))) as? [String: Any]
            guard let port = runtime?["port"] as? Int, let token = runtime?["token"] as? String, (1024...65535).contains(port) else { throw NSError(domain: "Flixer", code: 1, userInfo: [NSLocalizedDescriptionKey: "The companion is starting… Try again shortly."]) }
            var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/rpc")!)
            request.httpMethod = "POST"
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.timeoutInterval = 90
            request.httpBody = try JSONSerialization.data(withJSONObject: command)
            let configuration = URLSessionConfiguration.ephemeral
            configuration.connectionProxyDictionary = [:]
            let session = URLSession(configuration: configuration)
            session.dataTask(with: request) { data, _, error in
                defer { session.finishTasksAndInvalidate() }
                let result: Result<[String: Any], Error>
                do {
                    if let error { throw error }
                    guard let data, let response = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw URLError(.badServerResponse) }
                    guard response["ok"] as? Bool == true else { throw NSError(domain: "Flixer", code: 2, userInfo: [NSLocalizedDescriptionKey: response["error"] as? String ?? "Cannot connect."]) }
                    result = .success(response["result"] as? [String: Any] ?? [:])
                } catch { result = .failure(error) }
                DispatchQueue.main.async { completion(result) }
            }.resume()
        } catch { completion(.failure(error)) }
    }

    private func refresh() {
        guard !polling else { return }
        polling = true
        rpc(["action": "status"]) { result in
            self.polling = false
            if case .success(let value) = result {
                self.state = value
                self.playbackPanel?.update(value, busy: self.busy)
                self.item.button?.toolTip = value["message"] as? String ?? "Airplay Flixer"
                self.rebuildMenu()
            } else if self.engine?.isRunning == false {
                self.state["message"] = "Companion stopped. Reopen the app."
                self.rebuildMenu()
            }
        }
    }

    private func menuItem(_ title: String, _ action: Selector? = nil) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: action, keyEquivalent: "")
        entry.target = self
        return entry
    }

    private func rebuildMenu() {
        guard !menuOpen else { return }
        let menu = NSMenu()
        menu.delegate = self
        let heading = menuItem("Airplay Flixer")
        heading.isEnabled = false
        menu.addItem(heading)
        let status = menuItem(busy ? "Connecting…" : state["message"] as? String ?? "Starting…")
        status.isEnabled = false
        menu.addItem(status)
        if let error = state["error"] as? String, !error.isEmpty {
            let line = menuItem(String(error.prefix(95)))
            line.toolTip = error
            line.isEnabled = false
            menu.addItem(line)
        }
        menu.addItem(.separator())
        menu.addItem(menuItem("Mini controller…", #selector(showPlayback)))
        let devices = menuItem(state["selectedName"] as? String ?? "Choose an Apple TV")
        let submenu = NSMenu()
        for device in state["devices"] as? [[String: Any]] ?? [] {
            let name = device["name"] as? String ?? "Device"
            let dlna = device["protocol"] as? String == "dlna"
            let entry = menuItem(name + (dlna ? " · DLNA" : " · AirPlay"), #selector(selectDevice(_:)))
            entry.image = NSImage(systemSymbolName: dlna ? "network" : "airplay.video", accessibilityDescription: dlna ? "DLNA" : "AirPlay")
            entry.isEnabled = !busy
            entry.representedObject = device["id"]
            entry.state = device["id"] as? String == state["selected"] as? String ? .on : .off
            submenu.addItem(entry)
        }
        submenu.addItem(.separator())
        submenu.addItem(menuItem("Search AirPlay and DLNA…", #selector(scan)))
        if state["selected"] != nil {
            let dlna = state["selectedProtocol"] as? String == "dlna"
            devices.image = NSImage(systemSymbolName: dlna ? "network" : "airplay.video", accessibilityDescription: dlna ? "DLNA" : "AirPlay")
        }
        devices.submenu = submenu
        menu.addItem(devices)
        let pair = menuItem("Pair Apple TV…", #selector(pairTV))
        pair.isEnabled = !busy && state["selectedProtocol"] as? String != "dlna"
        menu.addItem(pair)
        if state["casting"] as? Bool == true {
            menu.addItem(.separator())
            menu.addItem(menuItem("Play / Pause", #selector(toggle)))
            menu.addItem(menuItem("Stop casting", #selector(stop)))
        }
        menu.addItem(.separator())
        menu.addItem(menuItem("Install Chrome extension…", #selector(showExtension)))
        menu.addItem(menuItem("View log", #selector(showLog)))
        menu.addItem(menuItem("About Airplay Flixer…", #selector(showAbout)))
        menu.addItem(menuItem("Check for Updates…", #selector(checkUpdates)))
        menu.addItem(.separator())
        let quit = menuItem("Quit Airplay Flixer", #selector(quitApp))
        quit.keyEquivalent = "q"
        menu.addItem(quit)
        item.menu = menu
    }

    func menuWillOpen(_ menu: NSMenu) { menuOpen = true }
    func menuDidClose(_ menu: NSMenu) {
        menuOpen = false
        DispatchQueue.main.async { self.rebuildMenu() }
    }

    private func perform(_ command: [String: Any], completion: (([String: Any]) -> Void)? = nil) {
        guard !busy else { return }
        busy = true
        playbackPanel?.update(state, busy: true)
        rebuildMenu()
        rpc(command) { result in
            self.busy = false
            if command["action"] as? String == "scan" {
                self.discoverySpinner?.stopAnimation(nil)
                self.discoverySpinner?.removeFromSuperview()
                self.discoverySpinner = nil
                self.item.button?.image = NSImage(systemSymbolName: "airplay.video", accessibilityDescription: "Airplay Flixer")
                self.item.button?.image?.isTemplate = true
                self.item.button?.toolTip = "Airplay Flixer"
            }
            self.playbackPanel?.update(self.state, busy: false)
            switch result {
            case .success(let value):
                if value["version"] != nil { self.state = value }
                self.playbackPanel?.update(self.state, busy: false)
                completion?(value)
            case .failure(let error): self.alert(error.localizedDescription)
            }
            self.refresh()
        }
    }

    @objc private func scan() {
        guard !busy else { return }
        if let button = item.button {
            let spinner = NSProgressIndicator(frame: NSRect(x: button.bounds.midX - 7, y: button.bounds.midY - 7, width: 14, height: 14))
            spinner.style = .spinning
            spinner.controlSize = .small
            spinner.setAccessibilityLabel("Searching for AirPlay and DLNA devices")
            button.image = nil
            button.toolTip = "Searching for AirPlay and DLNA devices…"
            button.addSubview(spinner)
            spinner.startAnimation(nil)
            discoverySpinner = spinner
        }
        perform(["action": "scan"])
    }
    @objc private func selectDevice(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        perform(["action": "select", "id": id])
    }
    @objc private func pairTV() {
        perform(["action": "pair_begin"]) { result in
            guard result["pinRequired"] as? Bool == true else { return }
            NSApp.activate(ignoringOtherApps: true)
            let dialog = NSAlert()
            dialog.messageText = "Pair Apple TV"
            dialog.informativeText = "Enter the 4-digit code displayed on the TV. You only need to do this once."
            let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 230, height: 28))
            field.placeholderString = "Apple TV code"
            dialog.accessoryView = field
            dialog.addButton(withTitle: "Pair")
            dialog.addButton(withTitle: "Cancel")
            dialog.window.initialFirstResponder = field
            if dialog.runModal() == .alertFirstButtonReturn {
                self.perform(["action": "pair_finish", "pin": field.stringValue.trimmingCharacters(in: .whitespaces)])
            } else { self.perform(["action": "pair_cancel"]) }
        }
    }
    @objc private func toggle() { perform(["action": "toggle"]) }
    @objc private func showPlayback() {
        if playbackPanel == nil {
            playbackPanel = PlaybackPanel()
            playbackPanel?.onCommand = { [weak self] action, position in
                var command: [String: Any] = ["action": action]
                if let position { command["position"] = position }
                self?.perform(command)
            }
        }
        playbackPanel?.update(state, busy: busy)
        playbackPanel?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        refresh()
    }
    @objc private func stop() { perform(["action": "stop"]) }
    @objc private func showExtension() {
        let extensionFolder = Bundle.main.resourceURL!.appendingPathComponent("extension")
        NSWorkspace.shared.activateFileViewerSelecting([extensionFolder])
        let dialog = NSAlert()
        dialog.messageText = "Install Chrome extension"
        dialog.informativeText = "In Chrome, open chrome://extensions, enable Developer mode, and click Load unpacked. Select the extension folder revealed in Finder, then pin Airplay Flixer in Chrome."
        dialog.addButton(withTitle: "Open Chrome")
        dialog.addButton(withTitle: "Close")
        NSApp.activate(ignoringOtherApps: true)
        if dialog.runModal() == .alertFirstButtonReturn {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
            process.arguments = ["-a", "Google Chrome", "chrome://extensions/"]
            try? process.run()
        }
    }
    private var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown"
    }

    @objc private func showAbout() {
        var details = "Version " + appVersion
        if let engineVersion = state["version"] as? String {
            details += "\nCompanion engine " + engineVersion
        }
        showUpdateDialog(title: "About Airplay Flixer", details: details, showRelease: false)
    }

    @objc private func checkUpdates() {
        guard let url = URL(string: "https://api.github.com/repos/IndieFear/airplay-flixer/releases/latest") else { return }
        var request = URLRequest(url: url, timeoutInterval: 12)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                guard (response as? HTTPURLResponse)?.statusCode == 200,
                      let data, let release = try? JSONDecoder().decode(GitHubRelease.self, from: data),
                      release.tag_name.range(of: #"^v?[0-9]+\.[0-9]+\.[0-9]+$"#, options: .regularExpression) != nil else {
                    self.showUpdateDialog(title: "Update check failed", details: "Could not check GitHub. Try again later.", showRelease: false)
                    return
                }
                let latest = String(release.tag_name.dropFirst(release.tag_name.hasPrefix("v") ? 1 : 0))
                let available = latest.compare(self.appVersion, options: .numeric) == .orderedDescending
                self.showUpdateDialog(
                    title: available ? "Update available" : "Airplay Flixer is up to date",
                    details: "Installed: \(self.appVersion)\nLatest release: \(latest)",
                    showRelease: available
                )
            }
        }.resume()
    }

    private struct GitHubRelease: Decodable {
        let tag_name: String
    }

    private func showUpdateDialog(title: String, details: String, showRelease: Bool) {
        NSApp.activate(ignoringOtherApps: true)
        let dialog = NSAlert()
        dialog.messageText = title
        dialog.informativeText = details
        if showRelease { dialog.addButton(withTitle: "Open GitHub Release") }
        dialog.addButton(withTitle: "Close")
        if dialog.runModal() == .alertFirstButtonReturn, showRelease,
           let url = URL(string: "https://github.com/IndieFear/airplay-flixer/releases/latest") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc private func showLog() { NSWorkspace.shared.open(folder.appendingPathComponent("companion.log")) }
    @objc private func quitApp() { NSApp.terminate(nil) }
    private func alert(_ message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let dialog = NSAlert()
        dialog.messageText = "Airplay Flixer"
        dialog.informativeText = message
        dialog.runModal()
    }
    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        engine?.terminate()
    }
}

@main
struct Launcher {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
