// Nakreslí ikonu aplikace (1024×1024 PNG). Spouští ji scripts/postav-aplikaci.sh.
// Modrá paleta shodná s přehledem: noční modř, elektrická modř, mátová čára.

import AppKit

let velikost: CGFloat = 1024
let obrazek = NSImage(size: NSSize(width: velikost, height: velikost))
obrazek.lockFocus()
let ctx = NSGraphicsContext.current!.cgContext

// Zaoblený čtverec podle mřížky macOS ikon (okraj 100 px, poloměr ~185).
let plocha = NSRect(x: 100, y: 100, width: 824, height: 824)
let tvar = NSBezierPath(roundedRect: plocha, xRadius: 185, yRadius: 185)

ctx.saveGState()
let stin = NSShadow()
stin.shadowColor = NSColor.black.withAlphaComponent(0.45)
stin.shadowBlurRadius = 30
stin.shadowOffset = NSSize(width: 0, height: -14)
stin.set()
NSColor(red: 0.04, green: 0.10, blue: 0.20, alpha: 1).setFill()
tvar.fill()
ctx.restoreGState()

tvar.addClip()
NSGradient(colors: [
    NSColor(red: 0.07, green: 0.16, blue: 0.35, alpha: 1),
    NSColor(red: 0.02, green: 0.07, blue: 0.17, alpha: 1),
])!.draw(in: plocha, angle: -90)

// Záře v horní části.
NSGradient(colors: [
    NSColor(red: 0.24, green: 0.61, blue: 1.0, alpha: 0.45),
    NSColor(red: 0.24, green: 0.61, blue: 1.0, alpha: 0.0),
])!.draw(fromCenter: NSPoint(x: 512, y: 860), radius: 0,
         toCenter: NSPoint(x: 512, y: 860), radius: 620, options: [])

// Jemná mřížka jako v účetní knize.
NSColor(red: 0.24, green: 0.45, blue: 0.80, alpha: 0.22).setStroke()
for i in 1...4 {
    let y = 100 + CGFloat(i) * 824 / 5
    let cara = NSBezierPath()
    cara.move(to: NSPoint(x: 170, y: y))
    cara.line(to: NSPoint(x: 854, y: y))
    cara.lineWidth = 4
    cara.stroke()
}

// Rostoucí křivka s plochou pod ní.
let body: [NSPoint] = [
    NSPoint(x: 190, y: 330), NSPoint(x: 300, y: 390), NSPoint(x: 400, y: 360),
    NSPoint(x: 500, y: 480), NSPoint(x: 600, y: 450), NSPoint(x: 710, y: 600),
    NSPoint(x: 820, y: 700),
]
let plochaPod = NSBezierPath()
plochaPod.move(to: NSPoint(x: body[0].x, y: 180))
body.forEach { plochaPod.line(to: $0) }
plochaPod.line(to: NSPoint(x: body.last!.x, y: 180))
plochaPod.close()
NSGradient(colors: [
    NSColor(red: 0.24, green: 0.61, blue: 1.0, alpha: 0.55),
    NSColor(red: 0.24, green: 0.61, blue: 1.0, alpha: 0.02),
])!.draw(in: plochaPod, angle: -90)

let krivka = NSBezierPath()
krivka.move(to: body[0])
body.dropFirst().forEach { krivka.line(to: $0) }
krivka.lineWidth = 34
krivka.lineCapStyle = .round
krivka.lineJoinStyle = .round
NSColor(red: 0.18, green: 0.91, blue: 0.65, alpha: 1).setStroke()
krivka.stroke()

// Bod na konci.
let konec = body.last!
NSColor.white.setFill()
NSBezierPath(ovalIn: NSRect(x: konec.x - 34, y: konec.y - 34, width: 68, height: 68)).fill()
NSColor(red: 0.18, green: 0.91, blue: 0.65, alpha: 1).setFill()
NSBezierPath(ovalIn: NSRect(x: konec.x - 20, y: konec.y - 20, width: 40, height: 40)).fill()

obrazek.unlockFocus()

let rep = NSBitmapImageRep(data: obrazek.tiffRepresentation!)!
let png = rep.representation(using: .png, properties: [:])!
try! png.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
