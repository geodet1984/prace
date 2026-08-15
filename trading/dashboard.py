"""Přehled paper účtu v prohlížeči — čtení, nikdy zápis.

Dashboard je **výhradně pozorovatel**. Nespouští engine, needituje stav
a nemá endpoint, který by cokoli obchodoval. Účet posouvá jen
``trading paper`` (potažmo skripty v ``scripts/``); tenhle modul soubor
se stavem otevírá read-only a nikam nic neposílá. Kdyby dashboard uměl
obchodovat, existovala by druhá cesta k penězům, která nemá testy
z ``test_live.py`` za sebou.

Server stojí na standardní knihovně, aby přehled fungoval i tam, kde
není nainstalované nic navíc. Šablona i stav se čtou **při každém
požadavku znovu**, takže editace ``ui/index.html`` je vidět po F5 —
a s ``/api/verze`` se stránka obnoví sama.

Poslouchá se jen na loopbacku. Stav účtu je soukromá věc a vystavovat
ho do sítě není důvod.

Kromě papírového účtu umí stránka ukázat i **skutečné portfolio** z účetní
knihy (``ledger.py``), pokud se cesta k ní předá přes ``--kniha``. Jsou to
dvě různé věci na jedné stránce schválně: papírový účet ukazuje, co by
strategie udělala, kniha to, co jste udělali vy. Kniha je volitelná a její
nepřítomnost nesmí přehled shodit — většina uživatelů skutečné portfolio
takhle nevede a prázdný oddíl je lepší než chybová stránka.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import fx as fx_module
from . import ledger as ledger_module

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent / "ui"
INDEX = UI_DIR / "index.html"

# Stav, který engine ukládá, ale který dashboardu k ničemu není. Vypisovat
# ho do prohlížeče by jen vytvořilo dojem, že to jsou čísla k rozhodování.
_INTERNAL_KEYS = {"vol_target", "kelly_r_multiples", "cooldown_until", "bar_index"}


class DashboardError(Exception):
    """Stav účtu se nedá přečíst nebo mu nerozumíme."""


# --- výpočty ------------------------------------------------------------
#
# Všechno níž je čistá funkce nad načteným slovníkem. Server je pak jen
# obálka, takže se čísla dají testovat bez jediného socketu.


def read_state(path: str | Path) -> dict:
    """Načte stav paper účtu z disku."""
    path = Path(path)
    if not path.exists():
        raise DashboardError(
            f"stav účtu {path} neexistuje — nejdřív ho založte přes ./scripts/start-demo.sh"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DashboardError(f"stav účtu {path} není platný JSON: {exc}") from exc


def _position_rows(state: dict) -> list[dict]:
    """Otevřené pozice oceněné poslední známou cenou.

    Cena je z posledního zpracovaného baru, ne z právě teď. Dashboard běží
    nad denními bary a tvářit se, že ukazuje živý trh, by byla lež.
    """
    prices = state.get("last_price", {})
    rows = []
    for pos in state.get("positions", []):
        symbol = pos["symbol"]
        price = prices.get(symbol, pos["entry_price"])
        quantity = pos["quantity"]
        cost = pos["entry_price"] * quantity
        value = price * quantity
        stop = pos.get("stop_loss")
        rows.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "entry_price": pos["entry_price"],
                "entry_time": pos["entry_time"],
                "price": price,
                "value": value,
                "pnl": value - cost,
                # Nulová pořizovací cena je nesmysl, ale dělit jí by shodilo
                # celý přehled kvůli jednomu řádku.
                "pnl_pct": (value - cost) / cost * 100.0 if cost else 0.0,
                "stop_loss": stop,
                # Kolik procent ceny zbývá ke stopu. Tohle je jediné číslo,
                # které o otevřené pozici říká něco akčního.
                "stop_distance_pct": (price - stop) / price * 100.0 if stop and price else None,
                "days_held": _days_between(pos["entry_time"], state.get("last_bar")),
            }
        )
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    return rows


def _days_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 86400.0


def _trade_rows(state: dict) -> list[dict]:
    """Uzavřené obchody se ziskem po poplatcích."""
    rows = []
    for trade in state.get("trades", []):
        quantity = trade["quantity"]
        cost = trade["entry_price"] * quantity
        gross = (trade["exit_price"] - trade["entry_price"]) * quantity
        # Čistý zisk je jediné číslo, které skutečně platí — viz models.Trade.
        net = gross - trade.get("fees", 0.0)
        rows.append(
            {
                "symbol": trade["symbol"],
                "quantity": quantity,
                "entry_time": trade["entry_time"],
                "entry_price": trade["entry_price"],
                "exit_time": trade["exit_time"],
                "exit_price": trade["exit_price"],
                "fees": trade.get("fees", 0.0),
                "pnl": net,
                "return_pct": net / cost * 100.0 if cost else 0.0,
                "exit_reason": trade.get("exit_reason", ""),
                "days_held": _days_between(trade["entry_time"], trade["exit_time"]),
            }
        )
    return rows


def _trade_stats(trades: list[dict]) -> dict:
    """Statistika uzavřených obchodů.

    Profit factor u série bez ztrát je nekonečno; vracíme ``None``, protože
    "∞" v tabulce svádí k závěru, že strategie neprohrává, místo aby to
    četlo jako "málo dat".
    """
    if not trades:
        return {"count": 0}

    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gross_loss = abs(sum(losses))
    holding = [t["days_held"] for t in trades if t["days_held"] is not None]
    reasons: dict[str, int] = {}
    for trade in trades:
        reason = trade["exit_reason"] or "—"
        reasons[reason] = reasons.get(reason, 0) + 1

    return {
        "count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100.0,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "best": max(t["pnl"] for t in trades),
        "worst": min(t["pnl"] for t in trades),
        "expectancy": sum(t["pnl"] for t in trades) / len(trades),
        "profit_factor": (sum(wins) / gross_loss) if gross_loss > 0 else None,
        "avg_days_held": sum(holding) / len(holding) if holding else None,
        "exit_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
    }


def _realized_curve(state: dict, trades: list[dict]) -> list[dict]:
    """Křivka *realizovaného* kapitálu — počáteční kapitál plus uzavřené obchody.

    Není to equity křivka účtu: otevřené pozice se v ní projeví až ve chvíli,
    kdy se zavřou. Skutečnou equity po barech engine do stavu neukládá
    (viz STAV.md), a dopočítat ji zpětně by znamenalo znovu přehrát data —
    což je práce backtestu, ne přehledu. Křivka je proto schodovitá
    a označená tak, aby si ji nikdo nespletl s equity.
    """
    equity = state.get("initial_capital", 0.0)
    points = [{"date": None, "equity": equity}]
    for trade in sorted(trades, key=lambda t: t["exit_time"]):
        equity += trade["pnl"]
        points.append({"date": trade["exit_time"], "equity": equity})
    return points


def snapshot(path: str | Path) -> dict:
    """Kompletní podklad pro přehled — jediné, co server posílá do prohlížeče."""
    state = read_state(path)

    positions = _position_rows(state)
    trades = _trade_rows(state)
    cash = state.get("cash", 0.0)
    invested = sum(p["value"] for p in positions)
    equity = cash + invested
    initial = state.get("initial_capital", 0.0)
    peak = max(state.get("peak_equity", equity), equity)

    return {
        "ucet": {
            "strategy": state.get("strategy"),
            "created_at": state.get("created_at"),
            "updated_at": state.get("updated_at"),
            "last_bar": state.get("last_bar"),
            "initial_capital": initial,
            "cash": cash,
            "invested": invested,
            "equity": equity,
            "total_return_pct": (equity - initial) / initial * 100.0 if initial else 0.0,
            "peak_equity": peak,
            # Propad od maxima. Kill-switch v risk.py se rozhoduje podle
            # stejného čísla, takže je to nejdůležitější údaj na stránce.
            "drawdown_pct": (peak - equity) / peak * 100.0 if peak else 0.0,
            "total_fees": state.get("total_fees", 0.0),
            "rejected_signals": state.get("rejected_signals", 0),
            "open_positions": len(positions),
            "pending_orders": len(state.get("pending", [])),
        },
        "pozice": positions,
        "pending": state.get("pending", []),
        # Poslední obchody nahoru — starší jsou v grafu.
        "obchody": sorted(trades, key=lambda t: t["exit_time"], reverse=True),
        "statistika": _trade_stats(trades),
        "krivka": _realized_curve(state, trades),
        "ceny": state.get("last_price", {}),
        "upozorneni": _warnings(state, positions, equity, peak),
    }


def _warnings(state: dict, positions: list[dict], equity: float, peak: float) -> list[str]:
    """Věci, které by v tabulce plné zelených čísel zapadly."""
    out = []

    drawdown = (peak - equity) / peak * 100.0 if peak else 0.0
    if drawdown > 15.0:
        out.append(f"Propad od maxima {drawdown:.1f} %. Kill-switch v risk.py bývá na 20 %.")

    rejected = state.get("rejected_signals", 0)
    trades = len(state.get("trades", []))
    if rejected > trades * 5 and rejected > 50:
        out.append(
            f"Zamítnuto {rejected} signálů proti {trades} obchodům — limity velikosti pozice "
            "propouštějí jen zlomek toho, co strategie navrhuje."
        )

    if state.get("total_fees", 0.0) == 0.0 and trades > 0:
        out.append(
            "Nasčítané poplatky jsou nulové. Účet běží na nulovém nákladovém modelu, "
            "takže výsledek je optimističtější než realita."
        )

    for pos in positions:
        distance = pos["stop_distance_pct"]
        if distance is not None and distance < 2.0:
            out.append(f"{pos['symbol']}: cena je {distance:.1f} % nad stopem.")

    last_bar = state.get("last_bar")
    if last_bar:
        age = _days_between(last_bar, datetime.now().isoformat(timespec="seconds"))
        if age is not None and age > 5:
            out.append(
                f"Poslední zpracovaný bar je {age:.0f} dní starý — účet se nikam neposunul."
            )

    return out


# --- skutečné portfolio -------------------------------------------------
#
# Druhá polovina stránky. Papírový účet výš je hypotéza; tohle jsou peníze.

#: Jak dlouho se drží spočítaný přehled portfolia, než se přepočítá.
#: Ceny i kurzy se tahají ze sítě a stránka se ptá po sekundách — bez
#: tohohle by jedno otevřené okno bombardovalo Yahoo i ČNB.
PORTFOLIO_TTL_S = 600.0

_PORTFOLIO_MEMO: dict[tuple, tuple[float, dict]] = {}


def _ceny_pozic(symbols: list[str]) -> dict[str, float]:
    """Poslední známé závěrečné ceny držených titulů.

    Chybějící cena není chyba: kniha smí obsahovat titul, který Yahoo
    nezná (třeba evropský UCITS pod jiným tickerem). Ocení se pak
    pořizovací cenou a evidence to přizná — viz ``Ledger.valuation``.
    """
    from . import data as data_module

    ceny: dict[str, float] = {}
    for symbol in symbols:
        try:
            df = data_module.fetch(symbol)
            ceny[symbol] = float(df["close"].iloc[-1])
        except Exception as exc:  # noqa: BLE001 - přehled nesmí spadnout kvůli ceně
            logger.warning("%s: cenu se nepodařilo zjistit (%s)", symbol, exc)
    return ceny


def portfolio_snapshot(
    path: str | Path,
    *,
    prices: dict[str, float] | None = None,
    kurzovnik=None,
    k_datu: date | None = None,
) -> dict:
    """Přehled skutečného portfolia v korunách.

    ``prices`` a ``kurzovnik`` se dají předat zvenčí — testy tak nesahají
    na síť a výsledek je dán jen obsahem knihy.
    """
    path = Path(path)
    kniha = ledger_module.Ledger.from_csv(path)
    k_datu = k_datu or date.today()

    drzeno = kniha.holdings()
    if prices is None:
        prices = _ceny_pozic(sorted(drzeno))

    if kurzovnik is None:
        kurzovnik = fx_module.Kurzovnik()
        cizi = {(t.currency or "").upper() for t in kniha.transactions} - {fx_module.DOMACI_MENA}
        if cizi:
            # Kurzy dopředu, ať se prohlížeč nedívá do prázdna, než doběhne
            # dvacet dotazů na ČNB uvnitř výpočtu. Kniha vedená jen
            # v korunách se přitom ČNB nezeptá vůbec — přepočet 1:1 nikdo
            # potvrzovat nemusí.
            kurzovnik.predehraj([t.day for t in kniha.transactions] + [k_datu])

    v = kniha.valuation_czk(prices, kurzovnik, k_datu=k_datu)
    hodiny = kniha.tax_clock(k_datu=k_datu)
    nahrady = dict(getattr(kurzovnik, "nahrady", {}))

    return {
        "vedena": True,
        "soubor": str(path),
        "pohybu": len(kniha.transactions),
        "prehled": v,
        "casovy_test": [
            {
                **r,
                "nakoupeno": r["nakoupeno"].isoformat(),
                "osvobozeno_od": r["osvobozeno_od"].isoformat(),
            }
            for r in hodiny
        ],
        # Kolik dat se muselo nahradit starším kurzovním lístkem. Nenulové
        # číslo znamená "běželo se bez sítě a čísla jsou přibližná".
        "nahrazenych_kurzu": len(nahrady),
        "upozorneni": _portfolio_warnings(v, hodiny, nahrady, k_datu),
    }


def _koruny(castka: float, mena: str) -> str:
    """Částka česky — mezera jako oddělovač tisíců, ne čárka."""
    return f"{castka:,.0f}".replace(",", " ") + f" {mena}"


def _portfolio_warnings(v: dict, hodiny: list[dict], nahrady: dict, k_datu: date) -> list[str]:
    """Věci, kvůli kterým je korunové číslo jiné, než se na první pohled zdá."""
    out: list[str] = []

    aktivum, kurz = v["vykon_aktiva"], v["kurzovy_rozdil"]
    # Tohle je celý důvod, proč se kurz vůbec rozpadá. Kdo vydělal na titulu
    # a přišel o to na kurzu, vidí v součtu skoro nulu a bez rozpadu si
    # myslí, že se nestalo nic.
    if aktivum * kurz < 0 and abs(kurz) > 0.3 * abs(aktivum):
        vydelaly = "vydělaly" if aktivum >= 0 else "prodělaly"
        smer = "ubral" if kurz < 0 else "přidal"
        out.append(
            f"Tituly samy {vydelaly} {_koruny(abs(aktivum), v['mena'])}, ale pohyb kurzu {smer} "
            f"{_koruny(abs(kurz), v['mena'])}. Bez rozpadu by výsledek vypadal jako výkon "
            "strategie, a přitom je z velké části měnový."
        )
    elif abs(kurz) > abs(aktivum) and abs(kurz) > 0:
        out.append(
            f"Na výsledku se podepsal kurz ({_koruny(kurz, v['mena'])}) víc než samotné tituly "
            f"({_koruny(aktivum, v['mena'])}) — držíte spíš měnovou sázku než akciovou."
        )

    if v["bez_kurzu"]:
        out.append(
            f"Bez kurzu: {', '.join(v['bez_kurzu'])}. Pohyby v těchhle měnách nejsou "
            "v součtech vůbec — radši chybějící řádek než přepočet kurzem 1:1."
        )

    if v["bez_ceny"]:
        out.append(
            f"Bez aktuální ceny, oceněno pořizovací: {', '.join(v['bez_ceny'])}. "
            "Nerealizovaný zisk je u nich z definice nula, ne skutečnost."
        )

    if nahrady:
        nejstarsi = min(nahrady.values())
        out.append(
            f"U {len(nahrady)} dat nebyl kurz k dispozici a použil se starší lístek "
            f"(nejstarší z {nejstarsi}). Bez sítě jsou korunová čísla přibližná."
        )

    blizko = [r for r in hodiny if not r["splneno"] and r["dni_zbyva"] <= 90]
    if blizko:
        nejblizsi = min(blizko, key=lambda r: r["dni_zbyva"])
        out.append(
            f"{nejblizsi['symbol']}: tříletý časový test dobíhá za {nejblizsi['dni_zbyva']} dní "
            f"({nejblizsi['osvobozeno_od']}). Není to daňové poradenství, jen rozdíl dat."
        )

    return out


def _portfolio_payload(path: Path | None) -> dict:
    """Odpověď pro ``/api/portfolio`` včetně případu "kniha se nevede".

    Nevedená kniha **není chyba** — je to výchozí stav. Vrací se proto
    200 s ``vedena: false`` a stránka oddíl prostě neukáže; 404 by
    v konzoli prohlížeče vypadalo jako rozbitý dashboard.
    """
    if path is None:
        return {"vedena": False, "duvod": "kniha se nevede — spusťte s --kniha data/portfolio.csv"}
    if not path.exists():
        return {"vedena": False, "duvod": f"kniha {path} neexistuje"}

    klic = (str(path), path.stat().st_mtime, date.today().isoformat())
    hotovo = _PORTFOLIO_MEMO.get(klic)
    if hotovo and time.monotonic() - hotovo[0] < PORTFOLIO_TTL_S:
        return hotovo[1]

    try:
        payload = portfolio_snapshot(path)
    except ledger_module.LedgerError as exc:
        # Rozbitá kniha je chyba uživatele v CSV, ne pád serveru. Zbytek
        # stránky (papírový účet) musí dál fungovat.
        return {"vedena": False, "duvod": str(exc)}

    _PORTFOLIO_MEMO.clear()
    _PORTFOLIO_MEMO[klic] = (time.monotonic(), payload)
    return payload


# --- server -------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Read-only HTTP rozhraní. Žádná metoda kromě GET neexistuje."""

    server_version = "TraderDashboard"

    def __init__(self, *args, state_path: Path, ledger_path: Path | None = None, **kwargs) -> None:
        self.state_path = state_path
        self.ledger_path = ledger_path
        super().__init__(*args, **kwargs)

    # Výchozí handler loguje každý request na stderr, což u stránky, která
    # se sama obnovuje, zaplaví terminál.
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - podpis stdlib
        logger.debug("%s %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # noqa: N802 - podpis stdlib
        route = self.path.split("?")[0]
        if route == "/":
            self._send_html()
        elif route == "/api/prehled":
            self._send_snapshot()
        elif route == "/api/portfolio":
            self._send_portfolio()
        elif route == "/api/verze":
            self._send_json(self._versions())
        else:
            self._send_json({"chyba": "neznámá cesta"}, status=404)

    def _versions(self) -> dict:
        """Otisky souborů pro automatické obnovení stránky.

        Stránka se ptá po sekundách; přenášet přitom celý přehled by bylo
        zbytečné. Změní-li se otisk, teprve pak si stránka řekne o data —
        nebo se rovnou přenačte, když jsme editovali šablonu.
        """
        kniha = self.ledger_path
        return {
            "stav": self.state_path.stat().st_mtime if self.state_path.exists() else 0.0,
            "ui": INDEX.stat().st_mtime if INDEX.exists() else 0.0,
            # Dopsaný obchod v knize se má projevit stejně jako pohyb účtu.
            "kniha": kniha.stat().st_mtime if kniha and kniha.exists() else 0.0,
        }

    def _send_html(self) -> None:
        if not INDEX.exists():
            self._send_json({"chyba": f"šablona {INDEX} chybí"}, status=500)
            return
        # Číst při každém požadavku je záměr: jinak by se změny v šabloně
        # projevily až po restartu serveru.
        self._send_bytes(INDEX.read_bytes(), "text/html; charset=utf-8")

    def _send_snapshot(self) -> None:
        try:
            payload = snapshot(self.state_path)
        except DashboardError as exc:
            self._send_json({"chyba": str(exc)}, status=503)
            return
        except (KeyError, TypeError, ValueError) as exc:
            self._send_json({"chyba": f"stavu účtu nerozumím: {exc}"}, status=500)
            return
        self._send_json(payload)

    def _send_portfolio(self) -> None:
        """Skutečné portfolio. Chyba tady nesmí shodit zbytek přehledu."""
        try:
            payload = _portfolio_payload(self.ledger_path)
        except Exception as exc:  # noqa: BLE001 - i neznámá chyba je jen prázdný oddíl
            logger.exception("přehled portfolia selhal")
            payload = {"vedena": False, "duvod": f"knihu nelze zpracovat: {exc}"}
        self._send_json(payload)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Bez tohohle by prohlížeč servíroval starou verzi a editace šablony
        # by vypadala, že nic nedělá.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _reject(self) -> None:
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # Dashboard zásadně nic nemění. Metody nejsou "nenaimplementované",
    # jsou odmítnuté — a test to hlídá.
    do_POST = do_PUT = do_DELETE = do_PATCH = _reject  # noqa: N815 - podpis stdlib


def make_server(
    state_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    ledger_path: str | Path | None = None,
):
    """Sestaví server. Nespouští ho — kvůli testům."""
    handler = partial(
        _Handler,
        state_path=Path(state_path),
        ledger_path=Path(ledger_path) if ledger_path else None,
    )
    return ThreadingHTTPServer((host, port), handler)


def serve(
    state_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    ledger_path: str | Path | None = None,
) -> int:
    """Spustí přehled a běží, dokud ho někdo nepřeruší."""
    httpd = make_server(state_path, host, port, ledger_path)
    actual_port = httpd.server_address[1]
    # Vypisujeme "localhost", ne číselnou adresu: Safari s vynuceným HTTPS
    # (mj. anonymní okna) http://127.0.0.1 zablokuje, localhost má výjimku.
    display_host = "localhost" if host == "127.0.0.1" else host
    print(f"\n  Přehled běží na http://{display_host}:{actual_port}")
    print(f"  Stav účtu:  {state_path}")
    if ledger_path:
        print(f"  Kniha:      {ledger_path}  (skutečné portfolio, přepočet kurzy ČNB)")
    print("  Jen ke čtení — účet posouvá výhradně `trading paper`.")
    print("  Konec: Ctrl+C\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nPřehled ukončen.")
    finally:
        httpd.server_close()
    return 0
