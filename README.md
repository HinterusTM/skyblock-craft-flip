# Skyblock Craft-Flip

Findet profitable Craft-Flips in Hypixel Skyblock: Zutaten per **Bazaar Buy Order** kaufen, Item craften, im **Auction House** (BIN) verkaufen.

![Screenshot](docs/screenshot.png)

## Download

Die fertige `Skyblock-Flip.exe` gibt es unter [Releases](../../releases/latest). Einfach doppelklicken, kein Python und kein API-Key nötig.

> Windows SmartScreen warnt evtl. beim ersten Start (die Exe ist nicht signiert): **Weitere Informationen → Trotzdem ausführen**.

## Was es anzeigt

| Spalte | Bedeutung |
|---|---|
| EK (Order) | Kosten aller Zutaten, wenn du per Buy Order kaufst (höchste Order + 0.1) |
| EK (Insta) | Kosten beim Sofortkauf, zum Vergleich |
| VK (BIN) | Günstigstes Buy-It-Now-Angebot im Auction House |
| BINs | Anzahl BIN-Angebote (wenige = Preis unsicher) |
| Profit | VK − AH-Steuer − EK (Order) |
| Fill-Zeit | Geschätzte Zeit, bis die Buy Order voll ist, wenn du oben im Orderbuch stehst (Insta-Sell-Volumen der letzten 7 Tage) |

Geprüft werden **alle craftbaren Items**, die gerade im Auction House angeboten werden und deren Zutaten einen Preis haben (Bazaar oder AH), aktuell gut 600 Stück. Das Feld **Suche** filtert nach Name oder ID (mehrere mit Komma, z.B. `dragon, hyperion`). **Sichere Preise** (standardmäßig an) blendet Items mit weniger als 3 BIN-Angeboten oder über 1000 % Marge aus (meist Troll-Listings). Ein Klick auf eine Zeile zeigt Zutaten, Preise, Konkurrenz im Orderbuch und nötige Collections.

> Sehr hohe Margen sind oft kein echter Flip: Das Rezept braucht eine Collection bzw. Freischaltung, oder eine Zutat hat einen Wert, den der Lowest BIN nicht abbildet (z.B. Midas Sword). Solche Zeilen werden in den Details markiert.

## Datenquellen

- Preise: [Hypixel API](https://api.hypixel.net/) (`/v2/skyblock/bazaar` und `/v2/skyblock/auctions`, öffentlich, kein Key)
- Rezepte: [NotEnoughUpdates-REPO](https://github.com/NotEnoughUpdates/NotEnoughUpdates-REPO), einmal am Tag geladen und unter `%LOCALAPPDATA%\SkyblockCraftFlip` gecacht

## Aus dem Code starten

Nur Python 3 nötig, keine Pakete:

```bash
python skyblock_flip.py                              # GUI
python skyblock_flip.py --cli                        # Top 40 über alle Items in der Konsole
python skyblock_flip.py --cli WISE_DRAGON_BOOTS      # bestimmte Items
```

Exe selbst bauen: `build.bat` (installiert PyInstaller und erzeugt `dist/Skyblock-Flip.exe`).

## Credits

Pixel-Fonts unter der SIL Open Font License (siehe `fonts/`): [Pixelify Sans](https://fonts.google.com/specimen/Pixelify+Sans), [Silkscreen](https://fonts.google.com/specimen/Silkscreen), [VT323](https://fonts.google.com/specimen/VT323).

Nicht mit Hypixel oder Mojang verbunden.
