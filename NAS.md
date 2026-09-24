# Moje investice na NASu (QNAP)

Jádro programu poběží na NASu, který je zapnutý pořád. iPhone i Mac se
k němu připojí přes VPN odkudkoli; Mac už nemusí být zapnutý.

Data (kniha, seznam sledovaných, klíč) leží na NASu ve sdílené složce.
Do internetu se nic neotevírá: port 8766 je vidět jen v síti NASu a přes
vaši VPN, a každý požadavek potřebuje spárovaný klíč.

## 1. Příprava na NASu (jednou)

1. V **App Center** nainstalujte **Container Station**, pokud ji nemáte.
   Při prvním spuštění vytvoří sdílenou složku **Container**.
2. Ověřte, že NAS má pevnou IP adresu v síti (Ovládací panel → Síť).
   Tu budete zadávat do telefonu — kdyby se měnila, spárování přestane platit.

## 2. Nahrání programu z Macu

1. Připojte se k VPN (nebo buďte v síti NASu).
2. Ve Finderu **⌘K** → `smb://<IP-NASu>/Container` → Připojit.
3. V Terminálu:

   ```bash
   cd ~/Trader && ./scripts/nahraj-na-nas.sh
   ```

   Skript nahraje program do `Container/trader`. Vaše data (knihu,
   sledované tituly) zkopíruje jen poprvé; potom je hlavní kopie na NASu.

## 3. Spuštění kontejneru

1. **Container Station → Aplikace (Applications) → Vytvořit (Create)**.
2. Název `trader`, obsah zkopírujte ze souboru `nas/docker-compose.yml`.
3. **Vytvořit.** První start trvá pár minut — doinstalovávají se knihovny.
   Další starty jsou rychlé.

Kontejner se po restartu NASu spustí sám (`restart: unless-stopped`).

## 4. Spárování iPhonu a Macu

1. Container Station → kontejner **trader** → **Terminál** (Execute) → `sh`.
2. Zadejte (IP adresu NASu doplňte):

   ```sh
   /venv/bin/python -m trading parovani --adresa http://<IP-NASu>:8766
   ```

   Vypíše odkaz a QR kód.
3. **iPhone:** zapněte VPN, naskenujte QR fotoaparátem, otevřete v Safari
   → **Sdílet → Přidat na plochu**.
4. **Mac:** v aplikaci Moje investice menu **Moje investice → Připojit
   k serveru (NAS)…** a vložte odkaz. Aplikace pak přestane spouštět
   vlastní jádro a ukazuje data z NASu.

Ztratíte-li telefon: stejný příkaz s `--novy` vydá nový klíč a všechna
dříve spárovaná zařízení ztratí přístup. Pak spárujte znovu ta, která máte.

## Aktualizace programu

Po každé změně programu na Macu: `./scripts/nahraj-na-nas.sh` a v Container
Station kontejner **trader** restartovat.

## Co na NASu nefunguje

* **Denní oznámení hlídače** jsou oznámení macOS — na NASu je nemá kdo
  ukázat. Přehled ale upozornění ukáže při otevření.
* **Přístupy (SEC) a párování** se nenastavují ve stránce, ale příkazem
  v kontejneru — na serveru je každé zařízení „cizí".

## Bezpečnost

* Spojení z iPhonu a Macu jde přes VPN, ta ho šifruje. V síti NASu samé je
  to HTTP — kdo je v téže síti, klíč by mohl odposlechnout. V domácí či
  firemní síti, kde ostatním důvěřujete, je to přijatelné.
* Port 8766 **nepřesměrovávejte na routeru do internetu.** Přístup zvenku
  má jít jen přes VPN.
* Soubor s klíčem `data/iphone_klic.txt` je na sdílené složce — kdo má
  přístup ke složce Container, vidí i klíč. Nechte ji jen pro sebe.
