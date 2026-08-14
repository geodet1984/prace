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
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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


# --- server -------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Read-only HTTP rozhraní. Žádná metoda kromě GET neexistuje."""

    server_version = "TraderDashboard"

    def __init__(self, *args, state_path: Path, **kwargs) -> None:
        self.state_path = state_path
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
        return {
            "stav": self.state_path.stat().st_mtime if self.state_path.exists() else 0.0,
            "ui": INDEX.stat().st_mtime if INDEX.exists() else 0.0,
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


def make_server(state_path: str | Path, host: str = "127.0.0.1", port: int = 8765):
    """Sestaví server. Nespouští ho — kvůli testům."""
    handler = partial(_Handler, state_path=Path(state_path))
    return ThreadingHTTPServer((host, port), handler)


def serve(state_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> int:
    """Spustí přehled a běží, dokud ho někdo nepřeruší."""
    httpd = make_server(state_path, host, port)
    actual_port = httpd.server_address[1]
    print(f"\n  Přehled běží na http://{host}:{actual_port}")
    print(f"  Stav účtu:  {state_path}")
    print("  Jen ke čtení — účet posouvá výhradně `trading paper`.")
    print("  Konec: Ctrl+C\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nPřehled ukončen.")
    finally:
        httpd.server_close()
    return 0
