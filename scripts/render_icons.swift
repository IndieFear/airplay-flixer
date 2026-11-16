import AppKit

// Keep the toolbar's transparent states separate from the app and store badges.
func render(_ image: NSImage, size: Int, to destination: URL) throws {
    let pixels = size < 128 ? size * 4 : size
    func bitmap(_ width: Int) -> NSBitmapImageRep {
        NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width, pixelsHigh: width,
                         bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                         colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    }
    func draw(_ source: NSImage, into target: NSBitmapImageRep, width: Int) {
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: target)
        NSGraphicsContext.current?.imageInterpolation = .high
        source.draw(in: NSRect(x: 0, y: 0, width: width, height: width),
                    from: .zero, operation: .copy, fraction: 1)
        NSGraphicsContext.restoreGraphicsState()
    }
    let large = bitmap(pixels)
    draw(image, into: large, width: pixels)
    let final: NSBitmapImageRep
    if pixels == size {
        final = large
    } else {
        let rendered = NSImage(size: NSSize(width: size, height: size))
        rendered.addRepresentation(large)
        final = bitmap(size)
        draw(rendered, into: final, width: size)
    }
    try final.representation(using: .png, properties: [:])!.write(to: destination)
}

let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
func load(_ name: String) -> NSImage {
    let source = output.appendingPathComponent(name + ".svg")
    guard let image = NSImage(contentsOf: source) else { fatalError("Cannot decode \(source.lastPathComponent)") }
    return image
}

let modes = CommandLine.arguments.count > 2 ? ["brand"] : ["brand", "idle", "ready"]
for mode in modes {
    let image = load(mode)
    for size in [16, 24, 32, 48, 64, 128] {
        try render(image, size: size, to: output.appendingPathComponent("\(mode)-\(size).png"))
    }
}

if CommandLine.arguments.count > 2 {
    let iconset = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
    try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
    let image = load("app")
    for size in [16, 32, 128, 256, 512] {
        try render(image, size: size, to: iconset.appendingPathComponent("icon_\(size)x\(size).png"))
        try render(image, size: 2 * size, to: iconset.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
    }
}
