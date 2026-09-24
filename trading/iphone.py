"""Přístup z iPhonu v domácí síti.

Přehled normálně poslouchá jen na tomhle Macu. Na výslovné zapnutí se
otevře i do domácí sítě — ale **jen se spárovaným klíčem**. Bez něj každý
požadavek z jiného zařízení dostane 401 a nic se nedozví; zapomenutý
zapnutý přístup tedy neznamená portfolio otevřené celé Wi-Fi.

Párování: Mac ukáže QR kód s adresou a klíčem, iPhone ho naskenuje
fotoaparátem a otevře v Safari. Server klíč uloží do cookie (jen pro
tenhle server, nikam jinam se neposílá) a stránku jde přidat na plochu
jako aplikaci. Klíč je i v adrese, se kterou se aplikace z plochy
spouští — iOS dává aplikacím z plochy vlastní úložiště cookies, takže by
je jinak ztratila.

**Síť je domácí, ale spojení je nešifrované HTTP.** Kdo odposlouchává
vaši Wi-Fi, klíč uvidí. Pro domácí síť s heslem je to přijatelné; na
veřejné Wi-Fi přístup nezapínejte. Klíč jde kdykoli vyměnit — starý tím
přestane platit.

Zapisující ochrany (vlastní hlavička, kontrola původu) platí i tady;
z iPhonu je ale původem adresa Macu, ne localhost.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger(__name__)

KLIC = Path("data/iphone_klic.txt")
PORT = 8766
"""Pevný port, aby adresa v iPhonu platila i po restartu Macu."""
COOKIE = "trader_klic"


def nacti_klic(soubor: Path = KLIC) -> str | None:
    """Platný klíč, nebo None, když přístup z iPhonu není zapnutý."""
    if not soubor.exists():
        return None
    return soubor.read_text(encoding="utf-8").strip() or None


def novy_klic(soubor: Path = KLIC) -> str:
    """Vytvoří (nebo vymění) klíč. Starý tím okamžitě přestane platit."""
    klic = secrets.token_urlsafe(24)
    soubor.parent.mkdir(parents=True, exist_ok=True)
    docasny = soubor.with_suffix(".tmp")
    docasny.write_text(klic + "\n", encoding="utf-8")
    docasny.chmod(0o600)  # jen pro vás, ne pro ostatní uživatele Macu
    docasny.replace(soubor)
    return klic


def vypnout(soubor: Path = KLIC) -> None:
    if soubor.exists():
        soubor.unlink()


def over(zadany: str | None, soubor: Path = KLIC) -> bool:
    """Porovnání v konstantním čase — rychlost odmítnutí neprozradí shodu."""
    klic = nacti_klic(soubor)
    return bool(klic and zadany) and hmac.compare_digest(klic, zadany)


def je_mistni(adresa: str) -> bool:
    """Požadavek z tohohle Macu (tam se klíč nevyžaduje)."""
    return adresa in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def adresa_macu() -> str:
    """Jméno Macu v domácí síti (Bonjour, *.local).

    Jméno přežije změnu IP adresy, kterou router přidělí jinak — adresa
    v iPhonu tak dál platí.
    """
    jmeno = socket.gethostname()
    if not jmeno.endswith(".local"):
        jmeno = jmeno.split(".")[0] + ".local"
    return jmeno


def odkaz_parovani(klic: str, port: int = PORT) -> str:
    return f"http://{adresa_macu()}:{port}/?klic={klic}"


def qr_svg(text: str) -> str:
    """QR kód jako SVG — bílé moduly na tmavém, ať ho fotoaparát přečte."""
    import io

    import segno

    buf = io.BytesIO()
    # omitsize: jen viewBox, bez pevné šířky a výšky. S pevnou velikostí
    # stránka zmenšila jen výřez, ne kód — zbyl levý horní roh a telefon
    # takový kód nepřečte.
    segno.make(text, error="m").save(buf, kind="svg", scale=8, border=2, omitsize=True,
                                     dark="#0b1a33", light="#ffffff", xmldecl=False)
    return buf.getvalue().decode("utf-8")


class Sit:
    """Druhý posluchač přehledu, otevřený do domácí sítě.

    Běží vedle toho místního a jde zapnout a vypnout bez restartu —
    přehled se kvůli tomu nemusí zavírat.
    """

    def __init__(self, handler) -> None:
        self.handler = handler
        self.server: ThreadingHTTPServer | None = None
        self.chyba: str | None = None

    @property
    def bezi(self) -> bool:
        return self.server is not None

    def zapnout(self, port: int = PORT) -> None:
        if self.server:
            return
        try:
            self.server = ThreadingHTTPServer(("0.0.0.0", port), self.handler)
        except OSError as exc:
            # Typicky běží ještě jiný přehled (spouštěč i aplikace zároveň).
            self.chyba = f"port {port} je obsazený: {exc}"
            logger.warning("přístup z iPhonu nejde zapnout — %s", self.chyba)
            return
        self.chyba = None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        logger.info("přístup z iPhonu zapnut na portu %s", port)

    def vypnout(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
