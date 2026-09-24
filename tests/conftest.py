"""Společné přípravy pro všechny testy."""

import pytest


@pytest.fixture(autouse=True)
def _iphone_klic_mimo_data(tmp_path, monkeypatch):
    """Žádný test nesmí sáhnout na skutečný klíč pro iPhone ani otevřít
    port do domácí sítě — i kdyby ho uživatel na Macu zapnutý měl."""
    from trading import iphone

    monkeypatch.setattr(iphone, "KLIC", tmp_path / "iphone_klic.txt")


@pytest.fixture(autouse=True)
def _neskutecny_seznam_sledovanych(tmp_path, monkeypatch):
    """Totéž pro seznam sledovaných titulů: test, který zkouší zápis přes
    server, ho jinak tiše zapsal do skutečného data/sledovane.txt."""
    from trading import dashboard

    monkeypatch.setattr(dashboard, "SLEDOVANE", tmp_path / "sledovane.txt")
