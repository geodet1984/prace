"""Skript ve stránce přehledu musí jít vůbec spustit.

Jediná syntaktická chyba v JavaScriptu (třeba parametr pojmenovaný
``let``, což je v přísném režimu vyhrazené slovo) shodí celou stránku:
žádný formulář, žádná čísla, jen „načítám". Python testy serveru to
nepoznají, protože stránku jen posílají. Tenhle test ji nechá přeložit.
"""

import re
import shutil
import subprocess

import pytest

from trading.dashboard import INDEX


@pytest.mark.skipif(shutil.which("node") is None, reason="node není nainstalovaný")
def test_skript_stranky_se_prelozi(tmp_path):
    html = INDEX.read_text(encoding="utf-8")
    skripty = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert skripty, "stránka nemá skript"
    soubor = tmp_path / "stranka.js"
    soubor.write_text("\n".join(skripty), encoding="utf-8")
    vysledek = subprocess.run(["node", "--check", str(soubor)], capture_output=True, text=True)
    assert vysledek.returncode == 0, vysledek.stderr
