"""
Hypixel Skyblock Craft-Flip Rechner

Vergleicht: Zutaten per Bazaar-Buy-Order kaufen -> Item craften -> im Auction House (BIN) verkaufen.
Rezepte kommen aus dem NotEnoughUpdates-Repo (einmal am Tag geladen und lokal gecacht),
Preise live aus der Hypixel API (kein Key noetig). Geprueft werden alle craftbaren Items im Auction House.

Benutzung:
    python skyblock_flip.py                                # GUI (oder Doppelklick)
    python skyblock_flip.py --cli                          # Konsole, Top 40 ueber alle Items
    python skyblock_flip.py --cli WISE_DRAGON_BOOTS ...    # Konsole, beliebige Item-IDs

Exe bauen: build.bat
"""

import base64
import collections
import gzip
import json
import io
import os
import re
import statistics
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

API = "https://api.hypixel.net/v2/skyblock"
NEU_ZIP = "https://github.com/NotEnoughUpdates/NotEnoughUpdates-REPO/archive/refs/heads/master.zip"
CACHE = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SkyblockCraftFlip", "recipes.json")
SLOTS = [row + col for row in "ABC" for col in "123"]
COLOR_CODE = re.compile("§.|[-]")  # Farbcodes wie "§6" und Hypixel-Icon-Glyphen aus Namen
MIN_BINS = 3  # "Sichere Preise": genug BIN-Angebote ...
MAX_MARGIN = 1000  # ... und keine absurde Marge (meist Troll-Listings oder gesperrte Rezepte)

EXPLANATION = ("Profit = VK - AH-Steuer - EK (Order). Fill-Zeit = geschaetzte Zeit bis die Buy Order voll ist,\n"
               "wenn du ganz oben im Orderbuch stehst (basiert auf Insta-Sell-Volumen der letzten 7 Tage).")


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "skyblock-flip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def download_recipes():
    """Alle Crafting-Rezepte aus dem NotEnoughUpdates-Repo (ein ZIP, ~20 MB)."""
    req = urllib.request.Request(NEU_ZIP, headers={"User-Agent": "skyblock-flip"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    recipes = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if "/items/" not in name or not name.endswith(".json"):
                continue
            item = json.loads(z.read(name))
            recipe = item.get("recipe") or next(
                (r for r in item.get("recipes", []) if r.get("type") == "crafting"), None)
            if not recipe:
                continue
            ingredients = {}
            for slot in SLOTS:
                if recipe.get(slot):
                    ing, _, count = recipe[slot].partition(":")
                    ingredients[ing] = ingredients.get(ing, 0) + int(float(count or 1))
            if ingredients:
                recipes[item["internalname"]] = dict(
                    ingredients=ingredients, count=int(recipe.get("count") or 1),
                    name=COLOR_CODE.sub("", item.get("displayname", "")).strip(), req=item.get("crafttext", ""))
    return recipes


def load_recipes(max_age=24 * 3600):
    """Rezepte aus dem lokalen Cache, einmal am Tag neu laden. Offline: alter Cache."""
    try:
        if time.time() - os.path.getmtime(CACHE) < max_age:
            with open(CACHE, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    try:
        recipes = download_recipes()
    except Exception:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(recipes, f)
    return recipes


ID_TAG = re.compile(rb"\x08\x00\x02id")  # NBT TAG_String "id" = ExtraAttributes.id


def item_id_from_bytes(item_bytes):
    raw = gzip.decompress(base64.b64decode(item_bytes))
    m = ID_TAG.search(raw)
    if not m:
        return None
    n = int.from_bytes(raw[m.end():m.end() + 2], "big")
    return raw[m.end() + 2:m.end() + 2 + n].decode("utf-8", "replace")


def fetch_lowest_bins():
    """{ITEM_ID: (niedrigster BIN, Anzahl BIN-Angebote)} fuer alles im Auction House."""
    first = get_json(f"{API}/auctions?page=0")
    with ThreadPoolExecutor(16) as ex:
        pages = [first] + list(ex.map(lambda p: get_json(f"{API}/auctions?page={p}"),
                                      range(1, first["totalPages"])))
    result = {}
    for page in pages:
        for a in page.get("auctions", []):
            if not a.get("bin"):
                continue
            iid = item_id_from_bytes(a["item_bytes"])
            if iid:
                low, count = result.get(iid, (float("inf"), 0))
                result[iid] = (min(low, a["starting_bid"]), count + 1)
    return result


def ah_tax(price):
    """BIN-Listing-Gebuehr + 1% Claim-Steuer ueber 1M."""
    fee = 0.01 if price < 10e6 else 0.02 if price < 100e6 else 0.025
    return price * (fee + (0.01 if price > 1e6 else 0))


def fmt(n):
    if n is None:
        return "-"
    if abs(n) >= 1e6:
        return f"{n / 1e6:.2f}M"
    if abs(n) >= 1e3:
        return f"{n / 1e3:.1f}k"
    return f"{n:.0f}"


def signed(n):
    return "-" if n is None else ("+" if n > 0 else "") + fmt(n)


def fmt_hours(h):
    if h is None:
        return "-"
    if h == float("inf"):
        return "nie"
    if h < 1:
        return f"{h * 60:.0f} min"
    if h < 48:
        return f"{h:.1f} h"
    return f"{h / 24:.1f} Tage"


def reliable(row):
    return row["listings"] >= MIN_BINS and (row["margin"] is None or row["margin"] <= MAX_MARGIN)


def pretty(item_id):
    return item_id.replace("_", " ").title()


def ingredient_line(i):
    line = f"{i['qty']}x {i['name']} @ {fmt(i['price'])} ({i['source']})"
    if i["per_hour"] is not None:
        line += (f" | {fmt(i['per_hour'])}/h Insta-Sells, {fmt(i['competing'])} Stk. "
                 f"in {i['orders']} konkurrierenden Buy Orders")
    return line


def analyze(targets=None):
    """Gibt (rows, warnings) zurueck, rows nach Profit sortiert.

    targets=None: alle craftbaren Items, die gerade im Auction House angeboten werden und
    deren Zutaten alle einen Preis haben (Bazaar oder AH)."""
    with ThreadPoolExecutor(2) as ex:
        recipes_job = ex.submit(load_recipes)
        bazaar_job = ex.submit(get_json, f"{API}/bazaar")
        bins = fetch_lowest_bins()
    recipes, bazaar = recipes_job.result(), bazaar_job.result()["products"]

    if targets:
        warnings = [f"Kein Crafting-Rezept fuer {t}" for t in targets if t not in recipes]
        items = [t for t in targets if t in recipes]
    else:
        warnings = []
        items = [i for i in recipes if i in bins]

    def name(item_id):
        return recipes[item_id]["name"] if item_id in recipes and recipes[item_id]["name"] else pretty(item_id)

    rows = []
    for item in items:
        recipe = recipes[item]
        cost_order = cost_insta = 0.0
        fill_hours = None  # bleibt None, wenn keine Zutat aus dem Bazaar kommt
        missing = []
        ingredients = []
        for ing, qty in recipe["ingredients"].items():
            bz = ing.replace("-", ":")  # NEU "INK_SACK-3" = Bazaar "INK_SACK:3"
            if bz in bazaar:
                q = bazaar[bz]
                orders = q["sell_summary"]  # = offene Buy Orders
                sells = q["buy_summary"]  # = offene Sell Offers
                order_price = orders[0]["pricePerUnit"] + 0.1 if orders else q["quick_status"]["sellPrice"]
                insta_price = sells[0]["pricePerUnit"] if sells else q["quick_status"]["buyPrice"]
                # Buy Orders werden durch Insta-Sells gefuellt -> sellMovingWeek
                per_hour = q["quick_status"]["sellMovingWeek"] / 168
                hours = qty / per_hour if per_hour else float("inf")
                fill_hours = max(fill_hours or 0.0, hours)
                cost_order += order_price * qty
                cost_insta += insta_price * qty
                ingredients.append(dict(id=ing, name=name(ing), qty=qty, price=order_price, source="Buy Order",
                                        per_hour=per_hour, hours=hours,
                                        competing=q["quick_status"]["sellVolume"],
                                        orders=q["quick_status"]["sellOrders"]))
            elif ing in bins:
                price = bins[ing][0]
                cost_order += price * qty
                cost_insta += price * qty
                ingredients.append(dict(id=ing, name=name(ing), qty=qty, price=price, source="AH BIN", per_hour=None))
            else:
                missing.append(name(ing))
        if (missing or not cost_order) and not targets:
            continue  # im Alle-Items-Modus nur Rezepte, die komplett (und nicht mit 0) bepreist werden koennen

        # Manche Rezepte ergeben mehrere Stueck: Kosten pro Stueck rechnen
        cost_order /= recipe["count"]
        cost_insta /= recipe["count"]
        sell, listings = bins.get(item, (None, 0))
        tax = ah_tax(sell) if sell else None
        profit = sell - tax - cost_order if sell and not missing else None
        margin = profit / cost_order * 100 if profit is not None and cost_order else None
        rows.append(dict(item=item, name=name(item), req=recipe["req"], count=recipe["count"],
                         cost=cost_order, insta=cost_insta, sell=sell, listings=listings, tax=tax,
                         profit=profit, margin=margin, fill=fill_hours, missing=missing, ingredients=ingredients))

    # Gleiche Anzeigenamen (z.B. 15 Varianten "Inferno Minion Fuel") ueber die ID unterscheidbar machen
    name_count = collections.Counter(r["name"] for r in rows)
    for r in rows:
        if name_count[r["name"]] > 1:
            r["name"] += f" ({pretty(r['item'])})"

    rows.sort(key=lambda r: r["profit"] if r["profit"] is not None else float("-inf"), reverse=True)
    return rows, warnings


def run_cli(targets):
    print("Lade Rezepte, Bazaar und Auction House...")
    rows, warnings = analyze(targets)
    for w in warnings:
        print(f"  ! {w}")
    if not targets:  # Alle Items: nur verlaessliche Preise, Top 40
        rows = [r for r in rows if reliable(r)][:40]

    print()
    header = f"{'Item':<34}{'EK (Order)':>12}{'EK (Insta)':>12}{'VK (BIN)':>11}{'BINs':>6}{'Profit':>11}{'Marge':>8}{'Fill-Zeit':>12}"
    print(header)
    print("-" * len(header))
    for r in rows:
        margin = f"{r['margin']:.0f}%" if r["margin"] is not None else "-"
        print(f"{r['name'][:33]:<34}{fmt(r['cost']):>12}{fmt(r['insta']):>12}{fmt(r['sell']):>11}"
              f"{r['listings']:>6}{fmt(r['profit']):>11}{margin:>8}{fmt_hours(r['fill']):>12}")

    print("\nDetails (Top 5):")
    for r in rows[:5]:
        print(f"  {r['name']}" + (f"  ({r['req']})" if r["req"] else ""))
        for i in r["ingredients"]:
            print(f"    {ingredient_line(i)}")
        if r["missing"]:
            print(f"    ! Kein Preis fuer: {', '.join(r['missing'])}")

    print("\n" + EXPLANATION)


def resource_path(name):
    """Pfad zu mitgelieferten Dateien, auch in der PyInstaller-Exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def load_fonts():
    """Mitgelieferte Pixel-Fonts (OFL, Google Fonts) nur fuer diesen Prozess registrieren (Windows)."""
    try:
        import ctypes
        folder = resource_path("fonts")
        for name in os.listdir(folder):
            if name.endswith(".ttf"):
                ctypes.windll.gdi32.AddFontResourceExW(os.path.join(folder, name), 0x10, 0)  # FR_PRIVATE
    except Exception:
        pass


def notched(x0, y0, x1, y1, p):
    """Polygon-Punkte fuer ein Rechteck mit treppenfoermig abgeschnittenen Ecken (Pixel-Look)."""
    return [x0 + 2 * p, y0, x1 - 2 * p, y0, x1 - 2 * p, y0 + p, x1 - p, y0 + p, x1 - p, y0 + 2 * p, x1, y0 + 2 * p,
            x1, y1 - 2 * p, x1 - p, y1 - 2 * p, x1 - p, y1 - p, x1 - 2 * p, y1 - p, x1 - 2 * p, y1,
            x0 + 2 * p, y1, x0 + 2 * p, y1 - p, x0 + p, y1 - p, x0 + p, y1 - 2 * p, x0, y1 - 2 * p,
            x0, y0 + 2 * p, x0 + p, y0 + 2 * p, x0 + p, y0 + p, x0 + 2 * p, y0 + p]


# Retro-Terminal-Look: fast schwarz, Scanlines, ein Orange als Akzent
C = dict(bg="#0b0a09", scan="#141210", panel="#151311", panel2="#1c1916", border="#3b352f",
         text="#f5f2ee", white="#ffffff", muted="#8f877e", dim="#4a443e", accent="#ed670f",
         accent_hover="#ff8133", green="#7ee36b", red="#ff5f56", yellow="#f2c14e")


def run_gui():
    import random
    import threading
    import tkinter as tk
    from tkinter import font as tkfont, ttk

    load_fonts()
    try:  # scharfe Schrift auf Windows mit Skalierung
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    root.title("Skyblock Craft-Flip")
    root.configure(bg=C["bg"])
    scale = root.winfo_fpixels("1i") / 96

    def px(n):
        return int(n * scale)

    root.geometry(f"{px(1240)}x{min(px(900), root.winfo_screenheight() - px(90))}+{px(40)}+{px(20)}")
    root.minsize(px(1040), px(640))
    root.state("zoomed")  # maximiert starten
    try:
        root.iconbitmap(resource_path("icon.ico"))
    except Exception:
        pass
    try:  # Titelleiste im Theme: Dark Mode (Win 10/11) + eigene Farben (Win 11)
        root.update()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())

        def dwm(attribute, value):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(ctypes.c_int(value)), 4)

        def colorref(hex_color):  # "#rrggbb" -> 0x00bbggrr
            r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
            return b << 16 | g << 8 | r

        dwm(20, 1)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        dwm(34, colorref(C["border"]))  # DWMWA_BORDER_COLOR
        dwm(35, colorref(C["bg"]))  # DWMWA_CAPTION_COLOR
        dwm(36, colorref(C["text"]))  # DWMWA_TEXT_COLOR
    except Exception:
        pass

    families = set(tkfont.families())
    DISPLAY = "Pixelify Sans" if "Pixelify Sans" in families else "Segoe UI"  # grosse Titel/Zahlen
    CAPS = "Silkscreen" if "Silkscreen" in families else "Segoe UI"  # kleine Labels in Grossbuchstaben
    MONO = "VT323" if "VT323" in families else "Consolas"  # Fliesstext und Tabelle

    # ---------- Pixel-Widgets ----------
    class PixelPanel(tk.Frame):
        """Box mit Pixel-Ecken; Inhalt kommt in .inner, die Groesse ergibt sich aus dem Inhalt."""

        def __init__(self, parent, fill=C["panel"]):
            super().__init__(parent, bg=parent["bg"])
            self.fill, self.outline, self.p = fill, C["border"], px(3)
            # Canvas als Hintergrund: per place, damit es die Groesse nicht beeinflusst
            self.canvas = tk.Canvas(self, bg=parent["bg"], highlightthickness=0, bd=0)
            self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
            self.canvas.bind("<Configure>", self.redraw)
            b = 2 * self.p + px(4)
            self.inner = tk.Frame(self, bg=fill)
            self.inner.pack(fill="both", expand=True, padx=b, pady=b)

        def redraw(self, event):
            self.canvas.delete("all")
            self.canvas.create_polygon(notched(1, 1, event.width - 1, event.height - 1, self.p), fill=self.fill,
                                       outline=self.outline, width=px(2), tags="shape")

        def set_outline(self, color):
            self.outline = color
            self.canvas.itemconfigure("shape", outline=color)

    class PixelButton(tk.Canvas):
        def __init__(self, parent, text, command):
            font = (CAPS, 9)
            f = tkfont.Font(family=CAPS, size=9)
            w, h = f.measure(text) + px(44), f.metrics("linespace") + px(24)
            super().__init__(parent, width=w, height=h, bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2")
            self.command, self.enabled, self.hover = command, True, False
            self.shape = self.create_polygon(notched(0, 0, w, h, px(3)), outline="")
            self.label = self.create_text(w // 2, h // 2, text=text, font=font)
            self.bind("<Enter>", lambda e: self.set_hover(True))
            self.bind("<Leave>", lambda e: self.set_hover(False))
            self.bind("<ButtonRelease-1>", lambda e: self.enabled and self.command())
            self.paint()

        def set_hover(self, hover):
            self.hover = hover
            self.paint()

        def set_enabled(self, enabled):
            self.enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
            self.paint()

        def paint(self):
            fill = C["border"] if not self.enabled else C["accent_hover"] if self.hover else C["accent"]
            self.itemconfigure(self.shape, fill=fill)
            self.itemconfigure(self.label, fill=C["white"] if self.enabled else C["muted"])

    class PixelCheck(tk.Frame):
        def __init__(self, parent, text, variable, command=None):
            super().__init__(parent, bg=parent["bg"], cursor="hand2")
            self.var, self.command, self.s = variable, command, px(16)
            self.box = tk.Canvas(self, width=self.s, height=self.s, bg=parent["bg"], highlightthickness=0, bd=0)
            self.box.pack(side="left")
            tk.Label(self, text=text, font=(CAPS, 8), fg=C["text"], bg=parent["bg"]).pack(side="left", padx=(px(8), 0))
            for widget in (self, *self.winfo_children()):
                widget.bind("<Button-1>", self.toggle)
            variable.trace_add("write", lambda *_: self.draw())
            self.draw()

        def toggle(self, _event=None):
            self.var.set(not self.var.get())
            if self.command:
                self.command()

        def draw(self):
            s, b = self.s, px(2)
            self.box.delete("all")
            on = self.var.get()
            self.box.create_rectangle(b / 2, b / 2, s - b / 2, s - b / 2, width=b,
                                      outline=C["accent"] if on else C["border"])
            if on:
                self.box.create_rectangle(2 * b, 2 * b, s - 2 * b, s - 2 * b, fill=C["accent"], outline="")

    class PixelLoader(tk.Canvas):
        """Lauflicht aus Pixel-Bloecken statt Progressbar."""
        N = 12

        def __init__(self, parent):
            self.s, self.g = px(6), px(3)
            super().__init__(parent, width=self.N * (self.s + self.g), height=self.s, bg=parent["bg"],
                             highlightthickness=0, bd=0)
            self.cells = [self.create_rectangle(i * (self.s + self.g), 0, i * (self.s + self.g) + self.s, self.s,
                                                fill=C["border"], outline="") for i in range(self.N)]
            self.pos, self.job = 0, None
            self.trail = [C["accent"], "#9c4511", "#5a2c10"]

        def start(self):
            self.stop()
            self.step()

        def stop(self):
            if self.job:
                self.after_cancel(self.job)
                self.job = None
            for c in self.cells:
                self.itemconfigure(c, fill=C["border"])

        def step(self):
            for i, c in enumerate(self.cells):
                d = (self.pos - i) % self.N
                self.itemconfigure(c, fill=self.trail[d] if d < len(self.trail) else C["border"])
            self.pos += 1
            self.job = self.after(70, self.step)

    class PixelScrollbar(tk.Canvas):
        """Schmaler Scrollbalken ohne Pfeile: gepunktete Spur, Thumb mit Pixel-Ecken."""

        def __init__(self, parent, command):
            super().__init__(parent, width=px(10), bg=parent["bg"], highlightthickness=0, bd=0)
            self.command, self.first, self.last = command, 0.0, 1.0
            self.grab, self.hover = None, False
            self.bind("<Configure>", lambda e: self.draw())
            self.bind("<Enter>", lambda e: self.set_hover(True))
            self.bind("<Leave>", lambda e: self.set_hover(False))
            self.bind("<ButtonPress-1>", self.press)
            self.bind("<B1-Motion>", self.motion)
            self.bind("<ButtonRelease-1>", self.release)

        def set(self, first, last):  # wird vom Treeview aufgerufen
            self.first, self.last = float(first), float(last)
            self.draw()

        def set_hover(self, hover):
            self.hover = hover
            self.draw()

        def draw(self):
            self.delete("all")
            w, h, d = self.winfo_width(), self.winfo_height(), px(2)
            for y in range(0, h, 2 * d):
                self.create_rectangle(w // 2 - d // 2, y, w // 2 - d // 2 + d, y + d, fill=C["border"], outline="")
            if self.last - self.first >= 1:
                return
            y0 = self.first * h
            y1 = max(self.last * h, y0 + px(24))
            color = C["accent"] if self.hover or self.grab is not None else "#5c544b"
            self.create_polygon(notched(0, y0, w, y1, px(2)), fill=color, outline="")

        def press(self, event):
            pos, size = event.y / self.winfo_height(), self.last - self.first
            if not self.first <= pos <= self.last:  # Klick in die Spur: Thumb dorthin springen lassen
                self.command("moveto", pos - size / 2)
                self.first = pos - size / 2
            self.grab = pos - self.first

        def motion(self, event):
            if self.grab is not None:
                self.command("moveto", event.y / self.winfo_height() - self.grab)

        def release(self, _event):
            self.grab = None
            self.draw()

    # ---------- Styles (nur noch die Tabelle ist ttk) ----------
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Treeview", background=C["panel"], fieldbackground=C["panel"], foreground=C["text"],
                    borderwidth=0, font=(MONO, 15),
                    rowheight=int(tkfont.Font(family=MONO, size=15).metrics("linespace") * 1.55))
    style.map("Treeview", background=[("selected", C["accent"])], foreground=[("selected", C["white"])])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Treeview.Heading", background=C["panel"], foreground=C["muted"], font=(CAPS, 8),
                    relief="flat", borderwidth=0, padding=(px(10), px(10)),
                    lightcolor=C["panel"], darkcolor=C["panel"], bordercolor=C["border"])
    style.map("Treeview.Heading", background=[("active", C["panel2"])], foreground=[("active", C["accent"])])

    def label(parent, text="", font=None, fg="text", **kw):
        return tk.Label(parent, text=text, font=font, fg=C[fg], bg=parent["bg"], **kw)

    # ---------- Header mit Scanlines und Pixel-Sternen ----------
    header = tk.Canvas(root, height=px(148), bg=C["bg"], highlightthickness=0, bd=0)
    header.pack(fill="x")
    rng = random.Random(7)
    stars = [(rng.uniform(0.35, 0.98), rng.uniform(0.05, 0.95), rng.random() < 0.2) for _ in range(26)]
    sparkles = [(0.62, 0.22), (0.9, 0.78), (0.44, 0.7)]

    header.create_text(px(28), px(30), anchor="w", text="HYPIXEL SKYBLOCK   /   CRAFT FLIPPING",
                       font=(CAPS, 8), fill=C["accent"])
    header.create_text(px(25), px(70), anchor="w", text="Skyblock Craft-Flip", font=(DISPLAY, 32), fill=C["white"])
    header.create_text(px(28), px(114), anchor="w",
                       text="Buy Order im Bazaar  >  craften  >  im Auction House verkaufen. Live aus der Hypixel API.",
                       font=(MONO, 16), fill=C["muted"])

    right = tk.Frame(header, bg=C["bg"])
    right_window = header.create_window(0, 0, window=right, anchor="e")
    status_col = tk.Frame(right, bg=C["bg"])
    status_col.pack(side="left", padx=(0, px(18)))
    status_label = label(status_col, "Bereit", (MONO, 15), fg="muted")
    status_label.pack(anchor="e")
    loader = PixelLoader(status_col)
    loader.pack(anchor="e", pady=(px(4), 0))
    button = PixelButton(right, "AKTUALISIEREN", lambda: refresh())
    button.pack(side="left")

    def draw_header(_event=None):
        w, h = header.winfo_width(), header.winfo_height()
        header.delete("deco")
        for y in range(0, h, px(3)):
            header.create_line(0, y, w, y, fill=C["scan"], tags="deco")
        for sx, sy, bright in stars:
            x, y, s = int(sx * w), int(sy * h), px(3) if bright else px(2)
            header.create_rectangle(x, y, x + s, y + s, fill="#bdb6ad" if bright else C["dim"], outline="", tags="deco")
        for sx, sy in sparkles:  # 4-zackiger Pixel-Stern
            x, y, p = int(sx * w), int(sy * h), px(2)
            for dx, dy in [(0, 0), (-p, 0), (p, 0), (0, -p), (0, p), (-2 * p, 0), (2 * p, 0), (0, -2 * p), (0, 2 * p)]:
                header.create_rectangle(x + dx, y + dy, x + dx + p, y + dy + p, fill="#d8d2ca", outline="", tags="deco")
        header.create_line(0, h - px(1), w, h - px(1), fill=C["border"], dash=(px(4), px(4)), width=px(2), tags="deco")
        header.tag_lower("deco")
        header.coords(right_window, w - px(28), h // 2)

    header.bind("<Configure>", draw_header)

    # ---------- Kennzahl-Karten ----------
    cards = tk.Frame(root, bg=C["bg"])
    cards.pack(fill="x", padx=px(24), pady=(px(18), 0))

    def stat_card(col, caption):
        panel = PixelPanel(cards)
        panel.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else px(14), 0))
        cards.columnconfigure(col, weight=1, uniform="cards")
        head = tk.Frame(panel.inner, bg=C["panel"])
        head.pack(anchor="w", padx=px(8), pady=(px(6), 0))
        label(head, f"0{col + 1} /", (CAPS, 8), fg="muted").pack(side="left")
        label(head, caption, (CAPS, 8), fg="accent").pack(side="left", padx=(px(6), 0))
        value = label(panel.inner, "-", (DISPLAY, 22), fg="white")
        value.pack(anchor="w", padx=px(8), pady=(px(4), 0))
        sub = label(panel.inner, " ", (MONO, 15), fg="muted")
        sub.pack(anchor="w", padx=px(8), pady=(0, px(6)))
        return value, sub

    best_value, best_sub = stat_card(0, "BESTER FLIP")
    count_value, count_sub = stat_card(1, "PROFITABEL")
    margin_value, margin_sub = stat_card(2, "MEDIAN-MARGE")
    time_value, time_sub = stat_card(3, "LETZTES UPDATE")

    # ---------- Toolbar ----------
    toolbar = tk.Frame(root, bg=C["bg"])
    toolbar.pack(fill="x", padx=px(24), pady=(px(14), px(10)))
    label(toolbar, "SUCHE", (CAPS, 9)).pack(side="left")
    entry_panel = PixelPanel(toolbar)
    entry_panel.pack(side="left", padx=(px(12), px(12)))
    search_var = tk.StringVar()
    entry = tk.Entry(entry_panel.inner, textvariable=search_var, width=30, font=(MONO, 16), bg=C["panel"],
                     fg=C["text"], insertbackground=C["accent"], relief="flat", bd=0,
                     selectbackground=C["accent"], selectforeground=C["white"])
    entry.pack(padx=px(4), pady=px(1))
    entry.bind("<FocusIn>", lambda e: entry_panel.set_outline(C["accent"]))
    entry.bind("<FocusOut>", lambda e: entry_panel.set_outline(C["border"]))
    label(toolbar, "Name oder ID, mehrere mit Komma  /  z.B. dragon, hyperion", (MONO, 15), fg="muted").pack(side="left")
    auto_var = tk.BooleanVar()
    only_profit_var = tk.BooleanVar()
    min_bins_var = tk.BooleanVar(value=True)
    PixelCheck(toolbar, "AUTO-REFRESH 5 MIN", auto_var).pack(side="right")
    PixelCheck(toolbar, "NUR PROFITABLE", only_profit_var, command=lambda: render()).pack(side="right", padx=px(22))
    PixelCheck(toolbar, "SICHERE PREISE", min_bins_var, command=lambda: render()).pack(side="right")

    # ---------- Tabelle ----------
    # (Key, Ueberschrift, Breite, Ausrichtung, Formatierer)
    columns = [
        ("name", "ITEM", 290, "w", str),
        ("cost", "EK (ORDER)", 110, "e", fmt),
        ("insta", "EK (INSTA)", 110, "e", fmt),
        ("sell", "VK (BIN)", 105, "e", fmt),
        ("listings", "BINS", 70, "e", str),
        ("profit", "PROFIT", 110, "e", signed),
        ("margin", "MARGE", 90, "e", lambda m: f"{m:.0f}%" if m is not None else "-"),
        ("fill", "FILL-ZEIT", 110, "e", fmt_hours),
    ]

    table_panel = PixelPanel(root)
    tree = ttk.Treeview(table_panel.inner, columns=[c[0] for c in columns], show="headings", selectmode="browse")
    scrollbar = PixelScrollbar(table_panel.inner, command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y", padx=(px(8), 0))
    tree.tag_configure("odd", background=C["panel2"])
    tree.tag_configure("plus", foreground=C["green"])
    tree.tag_configure("minus", foreground=C["red"])
    tree.tag_configure("none", foreground=C["muted"])

    # ---------- Details ----------
    details_panel = PixelPanel(root)
    details = tk.Text(details_panel.inner, height=6, bg=C["panel"], fg=C["text"], font=(MONO, 15), relief="flat",
                      highlightthickness=0, bd=0, padx=px(8), pady=px(4), wrap="word", cursor="arrow",
                      state="disabled", selectbackground=C["accent"])
    details.pack(fill="x")
    details.tag_configure("title", font=(DISPLAY, 15), foreground=C["white"], spacing3=px(6))
    details.tag_configure("bold", foreground=C["white"])
    for name in ("muted", "green", "red", "yellow", "accent"):
        details.tag_configure(name, foreground=C[name])

    footer = label(root, EXPLANATION, (MONO, 14), fg="muted", justify="left")

    # Von unten packen, damit bei kleinem Fenster nur die Tabelle schrumpft
    footer.pack(side="bottom", anchor="w", padx=px(26), pady=(px(8), px(10)))
    details_panel.pack(side="bottom", fill="x", padx=px(24), pady=(px(12), 0))
    table_panel.pack(fill="both", expand=True, padx=px(24))

    # ---------- Logik ----------
    state = {"rows": [], "shown": [], "sort": ("profit", True), "loading": False}
    TEXT_COLOR = {"plus": "green", "minus": "red", "none": "muted"}

    def profit_tag(p):
        return "none" if p is None else "plus" if p > 0 else "minus"

    def write_details(parts):
        details.configure(state="normal")
        details.delete("1.0", "end")
        for text, tag in parts:
            details.insert("end", text, tag or ())
        details.configure(state="disabled")

    def render():
        col, desc = state["sort"]
        rows = state["rows"]
        terms = [t.strip().lower() for t in search_var.get().split(",") if t.strip()]
        if terms:
            rows = [r for r in rows if any(t in r["name"].lower() or t.replace(" ", "_") in r["item"].lower()
                                           for t in terms)]
        if min_bins_var.get():
            rows = [r for r in rows if reliable(r)]
        update_cards(rows)
        if only_profit_var.get():
            rows = [r for r in rows if r["profit"] is not None and r["profit"] > 0]
        # None-Werte (kein Preis) immer ans Ende
        rows = sorted(rows, key=lambda r: (r[col] is not None, r[col]), reverse=desc)
        if not desc:
            rows.sort(key=lambda r: r[col] is None)
        state["shown"] = rows
        tree.delete(*tree.get_children())
        for i, r in enumerate(rows):
            tags = (profit_tag(r["profit"]),) + (("odd",) if i % 2 else ())
            tree.insert("", "end", iid=str(i), values=[f(r[k]) for k, _, _, _, f in columns], tags=tags)
        tree.yview_moveto(0)
        for k, title, *_ in columns:
            arrow = ("  ▼" if desc else "  ▲") if k == col else ""
            tree.heading(k, text=title + arrow)
        write_details([("> Klick auf eine Zeile fuer Zutaten, Preise und Fill-Zeit.", "muted")])

    def sort_by(col):
        current, desc = state["sort"]
        state["sort"] = (col, not desc) if col == current else (col, col != "name")
        render()

    for key, title, width, anchor, _ in columns:
        tree.heading(key, text=title, anchor=anchor, command=lambda k=key: sort_by(k))
        tree.column(key, width=px(width), minwidth=px(50), anchor=anchor, stretch=True)

    def show_details(_event=None):
        selection = tree.selection()
        if not selection:
            return
        r = state["shown"][int(selection[0])]
        parts = [(r["name"], "title")]
        if r["req"]:
            parts.append((f"   {r['req']}", "yellow"))
        parts.append(("\n", None))
        for caption, value, tag in [("Einkauf (Order)", fmt(r["cost"]), None),
                                    ("Einkauf (Insta)", fmt(r["insta"]), None),
                                    ("Verkauf (BIN)", fmt(r["sell"]), None),
                                    ("AH-Steuer", fmt(r["tax"]), None),
                                    ("Profit", signed(r["profit"]), TEXT_COLOR[profit_tag(r["profit"])])]:
            parts += [(caption + "  ", "muted"), (value, tag or "bold"), ("      ", None)]
        parts.append(("\n", None))
        if r["count"] > 1:
            parts.append((f"Rezept ergibt {r['count']} Stueck, Preise oben pro Stueck. Zutaten pro Craft:\n", "muted"))
        for i in r["ingredients"]:
            parts.append((f"{i['qty']}x {i['name']}", "accent"))
            parts.append((f"   {fmt(i['price'])}/Stk. via {i['source']}", "muted"))
            if i["per_hour"] is not None:
                parts.append((f"  /  {fmt(i['per_hour'])}/h Insta-Sells  /  {fmt(i['competing'])} Stk. in "
                              f"{i['orders']} konkurrierenden Buy Orders  /  Fill ~{fmt_hours(i['hours'])}", "muted"))
            parts.append(("\n", None))
        for m in r["missing"]:
            parts.append((f"[!] Kein Preis fuer {m}\n", "yellow"))
        if r["sell"] is None:
            parts.append(("[!] Gerade kein BIN-Angebot im Auction House\n", "yellow"))
        elif r["listings"] < 5:
            parts.append((f"[!] Nur {r['listings']} BIN-Angebote - Verkaufspreis unsicher\n", "yellow"))
        if r["margin"] is not None and r["margin"] > 200:
            parts.append(("[!] Sehr hohe Marge: pruefen, ob das Rezept eine Collection/Freischaltung braucht "
                          "oder der AH-Preis nur von wenigen Angeboten kommt\n", "yellow"))
        write_details(parts)

    def update_cards(rows):
        """Karten fuer die aktuell gefilterten Zeilen (Suche, Min. BINs)."""
        profitable = [r for r in rows if r["profit"] is not None and r["profit"] > 0]
        if profitable:
            best = max(profitable, key=lambda r: r["profit"])
            best_value.configure(text=signed(best["profit"]), fg=C["green"])
            best_sub.configure(text=best["name"])
            margins = [r["margin"] for r in profitable if r["margin"] is not None]
            margin_value.configure(text=f"{statistics.median(margins):.0f}%" if margins else "-")
            margin_sub.configure(text="ueber alle profitablen Items")
        else:
            best_value.configure(text="-", fg=C["white"])
            best_sub.configure(text="Gerade kein profitabler Flip" if state["rows"] else " ")
            margin_value.configure(text="-")
            margin_sub.configure(text=" ")
        count_value.configure(text=f"{len(profitable)} / {len(rows)}")
        count_sub.configure(text="Items mit Gewinn")

    def update_auto_label(*_):
        time_sub.configure(text="Auto-Refresh alle 5 min" if auto_var.get() else "Auto-Refresh aus")

    def set_loading(loading):
        state["loading"] = loading
        button.set_enabled(not loading)
        if loading:
            status_label.configure(text="Lade Live-Daten...", fg=C["muted"])
            loader.start()
        else:
            loader.stop()

    def done(result, error):
        set_loading(False)
        if error:
            status_label.configure(text=f"Fehler: {error}", fg=C["red"])
            return
        state["rows"] = result[0]
        render()
        time_value.configure(text=time.strftime("%H:%M:%S"))
        status_label.configure(text=f"{len(state['rows'])} craftbare Items im AH", fg=C["muted"])

    def refresh(_event=None):
        if state["loading"]:
            return
        set_loading(True)
        job = {}

        def work():
            try:
                job["result"] = analyze()
            except Exception as e:
                job["error"] = e

        def poll():  # tkinter darf nur aus dem Haupt-Thread angefasst werden
            if thread.is_alive():
                root.after(200, poll)
            else:
                done(job.get("result"), job.get("error"))

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        poll()

    def auto_tick():
        if auto_var.get():
            refresh()
        root.after(300_000, auto_tick)

    search_var.trace_add("write", lambda *_: render())
    tree.bind("<<TreeviewSelect>>", show_details)
    auto_var.trace_add("write", update_auto_label)
    update_auto_label()
    render()
    root.after(100, refresh)
    root.after(300_000, auto_tick)
    root.mainloop()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--cli":
        sys.stdout.reconfigure(errors="replace")  # Windows-Konsole kann nicht jedes Zeichen
        run_cli([a.upper() for a in args[1:]] or None)
    else:
        run_gui()
