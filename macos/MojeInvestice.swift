// Moje investice — nativní okno pro přehled portfolia.
//
// Aplikace nemá vlastní logiku: spustí výpočetní jádro projektu
// (`python -m trading dashboard`) a jeho stránku ukáže ve vlastním okně.
// Druhá implementace téhož ve Swiftu by se dřív nebo později začala
// počítat jinak než ta otestovaná — stejný důvod, proč backtest a živý
// běh sdílejí jednu smyčku.
//
// Server poslouchá jen na tomhle Macu a končí spolu s aplikací.

import AppKit
import WebKit

/// Vlastní log v ~/Library/Logs/MojeInvestice.log. Systémový log zprávy
/// aplikací skrývá, a když okno zůstane prázdné, jinde to není poznat.
func zaznam(_ text: String) {
    let cesta = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/MojeInvestice.log")
    let radek = "\(Date()) \(text)\n"
    if let h = try? FileHandle(forWritingTo: cesta) {
        h.seekToEndOfFile(); h.write(radek.data(using: .utf8)!); try? h.close()
    } else {
        try? radek.write(to: cesta, atomically: true, encoding: .utf8)
    }
}

final class Aplikace: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var okno: NSWindow!
    var web: WKWebView!
    var jadro: Process?
    var hlaska: NSTextField!

    /// Kde leží projekt. Dá se přepsat proměnnou TRADER_REPO.
    let repo: URL = {
        if let cesta = ProcessInfo.processInfo.environment["TRADER_REPO"] {
            return URL(fileURLWithPath: cesta)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Trader")
    }()

    /// Adresa serveru (NAS) i s klíčem. Když je nastavená, aplikace si
    /// nespouští vlastní jádro — dvě knihy vedle sebe by se rozešly.
    var server: String? {
        get { UserDefaults.standard.string(forKey: "server") }
        set { UserDefaults.standard.set(newValue, forKey: "server") }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        postavMenu()
        postavOkno()
        if let adresa = server {
            zaznam("připojuji k serveru \(adresa.components(separatedBy: "klic=").first ?? "")")
            hlaska.stringValue = "Připojuji k serveru…"
            otevri(adresa)
        } else {
            spustServer()
        }
    }

    @objc func pripojitServer() {
        let a = NSAlert()
        a.messageText = "Připojit k serveru (NAS)"
        a.informativeText = "Vložte párovací odkaz ze serveru (příkaz „python -m trading parovani“ "
            + "v kontejneru). Aplikace pak nebude spouštět vlastní jádro a zobrazí data ze serveru."
        let pole = NSTextField(frame: NSRect(x: 0, y: 0, width: 420, height: 24))
        pole.placeholderString = "http://192.168.1.50:8766/?klic=…"
        pole.stringValue = server ?? ""
        a.accessoryView = pole
        a.addButton(withTitle: "Připojit")
        a.addButton(withTitle: "Zrušit")
        guard a.runModal() == .alertFirstButtonReturn else { return }
        let adresa = pole.stringValue.trimmingCharacters(in: .whitespaces)
        guard adresa.hasPrefix("http"), adresa.contains("klic=") else {
            chyba("Odkaz musí začínat http a obsahovat klic=…")
            return
        }
        server = adresa
        restartovat()
    }

    @objc func pouzivatMac() {
        server = nil
        restartovat()
    }

    func restartovat() {
        let cesta = Bundle.main.bundlePath
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        p.arguments = ["-n", cesta]
        try? p.run()
        NSApp.terminate(nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        // Bez tohohle by server běžel dál na pozadí a držel port.
        jadro?.terminate()
        jadro?.waitUntilExit()
    }

    // MARK: - okno

    func postavOkno() {
        okno = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        okno.title = "Moje investice"
        okno.appearance = NSAppearance(named: .darkAqua)
        okno.backgroundColor = NSColor(red: 0.043, green: 0.102, blue: 0.2, alpha: 1)
        okno.setFrameAutosaveName("HlavniOkno")
        okno.center()

        let nastaveni = WKWebViewConfiguration()
        web = WKWebView(frame: okno.contentView!.bounds, configuration: nastaveni)
        web.autoresizingMask = [.width, .height]
        web.uiDelegate = self
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")  // žádný bílý záblesk při načítání
        web.isHidden = true
        okno.contentView!.addSubview(web)

        hlaska = NSTextField(labelWithString: "Spouštím výpočetní jádro…")
        hlaska.textColor = NSColor(red: 0.62, green: 0.72, blue: 0.88, alpha: 1)
        hlaska.font = NSFont.systemFont(ofSize: 15)
        hlaska.alignment = .center
        hlaska.frame = NSRect(x: 0, y: 400, width: 1180, height: 40)
        hlaska.autoresizingMask = [.width, .minYMargin, .maxYMargin]
        okno.contentView!.addSubview(hlaska)

        okno.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func postavMenu() {
        let hlavni = NSMenu()
        let aplikace = NSMenuItem()
        hlavni.addItem(aplikace)
        let menu = NSMenu()
        menu.addItem(withTitle: "Obnovit", action: #selector(obnovit), keyEquivalent: "r")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Připojit k serveru (NAS)…", action: #selector(pripojitServer),
                     keyEquivalent: "")
        menu.addItem(withTitle: "Používat data na tomto Macu", action: #selector(pouzivatMac),
                     keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Ukončit Moje investice",
                     action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        aplikace.submenu = menu

        // Úpravy: bez nich nefunguje ⌘C / ⌘V ve formulářích.
        let upravy = NSMenuItem()
        hlavni.addItem(upravy)
        let u = NSMenu(title: "Úpravy")
        u.addItem(withTitle: "Vyjmout", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        u.addItem(withTitle: "Kopírovat", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        u.addItem(withTitle: "Vložit", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        u.addItem(withTitle: "Vybrat vše", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        upravy.submenu = u
        NSApp.mainMenu = hlavni
    }

    @objc func obnovit() { web.reload() }

    // MARK: - výpočetní jádro

    func spustServer() {
        zaznam("start, projekt \(repo.path)")
        let python = repo.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.fileExists(atPath: python.path) else {
            chyba("Nenašel jsem projekt v \(repo.path).\nChybí .venv/bin/python.")
            return
        }
        let p = Process()
        p.executableURL = python
        // Port 0 = vybere volný; skutečný si přečteme z výpisu serveru.
        // Aplikace tak nekoliduje se spouštěčem z plochy ani s ničím jiným.
        // --s-rodicem: jádro skončí i při pádu nebo vynuceném ukončení
        // aplikace, kdy applicationWillTerminate neproběhne.
        p.arguments = ["-m", "trading", "dashboard", "--port", "0",
                       "--kniha", "data/portfolio.csv", "--s-rodicem"]
        p.currentDirectoryURL = repo
        var prostredi = ProcessInfo.processInfo.environment
        prostredi["PYTHONUNBUFFERED"] = "1"
        p.environment = prostredi

        let vystup = Pipe()
        p.standardOutput = vystup
        p.standardError = FileHandle.nullDevice
        var nacteno = ""
        vystup.fileHandleForReading.readabilityHandler = { [weak self] h in
            guard let text = String(data: h.availableData, encoding: .utf8), !text.isEmpty else { return }
            nacteno += text
            zaznam("jádro: \(text.trimmingCharacters(in: .whitespacesAndNewlines))")
            if let rozsah = nacteno.range(of: #"http://localhost:(\d+)"#, options: .regularExpression) {
                let url = String(nacteno[rozsah])
                h.readabilityHandler = nil
                DispatchQueue.main.async { self?.otevri(url) }
            }
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                if self?.web.isHidden == true {
                    self?.chyba("Výpočetní jádro skončilo (kód \(proc.terminationStatus)).")
                }
            }
        }
        do {
            try p.run()
            jadro = p
        } catch {
            chyba("Jádro se nepodařilo spustit: \(error.localizedDescription)")
        }
    }

    func otevri(_ adresa: String) {
        zaznam("jádro na \(adresa)")
        guard let url = URL(string: adresa) else { return }
        web.load(URLRequest(url: url))
    }

    func chyba(_ text: String) {
        hlaska.stringValue = text
        hlaska.textColor = NSColor(red: 1, green: 0.33, blue: 0.44, alpha: 1)
    }

    // MARK: - WebKit

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        zaznam("načtení selhalo: \(error)")
        chyba(server == nil
              ? "Stránku se nepodařilo načíst: \(error.localizedDescription)"
              : "Server není dostupný. Je zapnutá VPN a běží kontejner na NASu?\n"
                + "(\(error.localizedDescription))")
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        zaznam("chyba stránky: \(error)")
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        zaznam("stránka načtena")
        hlaska.isHidden = true
        web.isHidden = false
    }

    /// Tlačítko „Vybrat soubor" u importu výpisu. WebKit sám dialog neotevře.
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.beginSheetModal(for: okno) { odpoved in
            completionHandler(odpoved == .OK ? panel.urls : nil)
        }
    }

    /// Odkazy ven (zdroje dat apod.) do běžného prohlížeče, ne do okna aplikace.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, url.host != "localhost", url.host != "127.0.0.1",
           action.navigationType == .linkActivated {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }
}

let app = NSApplication.shared
let delegat = Aplikace()
app.delegate = delegat
app.setActivationPolicy(.regular)
app.run()
