#!/usr/bin/env python3
"""
Bazoš figyelő 2.0 – Komárno + 60 km

Hogyan dönt?
  1. Átnézi a környék legfrissebb hirdetéseit (a fizetett TOP-hirdetéseket kihagyja).
  2. Minden hirdetésből kiolvassa a pontos terméket (pl. "iPhone 13 Pro 256GB",
     "Makita DHP486 csak gép").
  3. Megnézi, mennyiért árulják UGYANEZT a terméket egész Szlovákiában
     (medián kérőár), és ezt elmenti 3 napra.
  4. Ha a hirdetés legalább MIN_KEDVEZMENY-nyivel olcsóbb a mediánnál, és a
     várható haszon legalább MIN_HASZON euró, azonnal e-mailt küld.
  5. Naponta egyszer (este) összefoglalót küld a nap legjobb jelöltjeiről.

Kiszűri: kereskedőket (sok hirdetés egy eladótól), újrafeltöltött hirdetéseket,
tartozékokat, hibás / iCloud-zárolt készülékeket.

Futtatás:
  python bazos_figyelo.py              normál futás
  python bazos_figyelo.py --proba      kiírja, mit küldene, de nem küld e-mailt
  python bazos_figyelo.py --jelentes   azonnal elküldi a napi összefoglalót
  python bazos_figyelo.py --teszt-email
"""

import json
import os
import random
import re
import smtplib
import statistics
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

# =====================================================================
#  BEÁLLÍTÁSOK
# =====================================================================

EMAIL = {
    "kuldo": os.environ.get("GMAIL_USER", ""),
    "jelszo": os.environ.get("GMAIL_APP_PASSWORD", ""),
    "cimzett": os.environ.get("EMAIL_TO") or os.environ.get("GMAIL_USER", ""),
    "smtp_szerver": "smtp.gmail.com",
    "smtp_port": 465,
}

PSC = "94501"          # Komárno
KORZET_KM = 60         # ekkora körzetben keres vételt (Nové Zámky, Dunajská Streda, Štúrovo is benne van)

MIN_KEDVEZMENY = 0.18  # legalább ennyivel olcsóbb legyen a szlovák mediánnál (0.18 = 18%)
MIN_HASZON = 30        # és legalább ennyi € legyen a különbség
GYANUS_ARANY = 0.45    # a medián 45%-a alatt: gyanúsan olcsó (csalás/hibás) – jelöljük
KERESKEDO_HIRDETES = 4 # ha egy eladónak ennyi vagy több hirdetése van, kereskedőnek tekintjük
NAPI_JELENTES_ORA = 19 # ennyi óra után küldi a napi összefoglalót
REFERENCIA_NAP = 3     # ennyi napig használja a kiszámolt piaci árat, utána újraszámolja
AKKUCSERE = 40         # ennyit vonunk le a haszonból, ha az iPhone akkuja 80% alatt van

# Mit figyeljen?
#   tipus "iphone": modell + tárhely felismerése
#   tipus "kod":    gépkód felismerése (pl. Makita DHP486, DeWalt DCD796, Milwaukee M18 FPD2)
#   tipus "minta":  a "minta" reguláris kifejezés első találata adja a terméket
FIGYELT = [
    {"nev": "iPhone",          "kereses": "iphone",          "tipus": "iphone", "oldalak": 2, "rovatok": ["mobil"], "min_ar": 60},
    {"nev": "AirPods",         "kereses": "airpods",         "tipus": "minta",  "oldalak": 1, "rovatok": ["mobil", "elektro"], "min_ar": 25,
     "minta": r"airpods\s*(pro\s*2|pro|max|4|3|2)?",
     "tilt": r"\b(lav[ey]|prav[ey]|jedno)\b.{0,12}sluch|\b(len|iba)\s+(puzdro|nabijacie|lave|prave)"},
    {"nev": "PlayStation 5",   "kereses": "ps5",             "tipus": "minta",  "oldalak": 1, "rovatok": ["pc", "elektro"], "min_ar": 200,
     "minta": r"\b(?:ps\s*5|playstation\s*5)\s*(pro|slim|digital)?", "kell": r"konzol|ps\s*5|playstation",
     "tilt": r"^\W*(?:\w+\s)?(gamepad|dualsense|controller|volant|kamera|headset|chladi\w*)\b"},
    {"nev": "Nintendo Switch", "kereses": "nintendo switch", "tipus": "minta",  "oldalak": 1, "rovatok": ["pc", "elektro"], "min_ar": 90,
     "minta": r"switch\s*(oled|lite|2)?",
     "tilt": r"^\W*(?:\w+\s)?(cartridge|karta|pamatova)\b"},
    {"nev": "Makita",          "kereses": "makita",          "tipus": "kod",    "oldalak": 1, "rovatok": ["dom", "stroje"], "marka": "makita", "min_ar": 25},
    {"nev": "DeWalt",          "kereses": "dewalt",          "tipus": "kod",    "oldalak": 1, "rovatok": ["dom", "stroje"], "marka": "dewalt", "min_ar": 25},
    {"nev": "Milwaukee",       "kereses": "milwaukee",       "tipus": "kod",    "oldalak": 1, "rovatok": ["dom", "stroje"], "marka": "milwaukee", "min_ar": 25},
]

# Tartozék-hirdetés: a cím ilyen szóval KEZDŐDIK, vagy "<szó> na/pre/do ..." szerepel benne
# (így a "Kryt na iPhone 15" kiesik, de az "iPhone 13 + obal 128GB" vagy "AirPods Pro 2 s puzdrom" marad)
TARTOZEK = (r"(kryt\w*|obal\w*|puzdr\w*|sklo|folia|nabijac\w*|kabel\w*|drziak\w*|adapter\w*|remienok|stojan\w*|"
            r"dock|case|bateri\w*|akumulator|kufor|taska|nahradn\w*|displej\w*|hra|hry|ovladac\w*|joy\s*-?con\w*)")
# Ha a CÍMBEN szerepel: kihagyjuk
KIZARO_CIM = ["kupim", "hladam", "vymenim", "na diely", "na nahradne", "nefunkc", "nefunguje", "rozbit",
              "prasknut", "icloud", "blokovan", "zamknut", "prenajom", "pozicovna", "pozicam", "oprava", "servis"]
# Ha a LEÍRÁSBAN szerepel: kihagyjuk (óvatosabb lista, mert pl. "odhlásený z iCloudu" jó jel)
KIZARO_LEIRAS = ["na diely", "na nahradne diely", "nefunkcn", "blokovany na", "zablokovan", "icloud lock",
                 "icloud zamk", "zamknuty na", "face id nefunguje", "face id nejde", "nejde zapnut", "nezapne"]
# Sürgős, alkuképes eladóra utaló szavak (csak kiemeljük)
SURGOS_SZAVAK = ["surne", "rychly predaj", "stahujem", "lacno", "dohoda", "nutne", "do konca tyzdna"]

# =====================================================================
#  INNEN LEFELÉ NEM KELL HOZZÁNYÚLNI
# =====================================================================

MAPPA = Path(__file__).resolve().parent
ALLAPOT_FAJL = MAPPA / "allapot.json"
ARNAPLO_FAJL = MAPPA / "piaci_naplo.csv"
NAPLO_FAJL = MAPPA / "naplo.txt"

FEJLEC = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "sk-SK,sk;q=0.9,hu;q=0.8",
}
SZUNET = (1.0, 2.0)
ID_MINTA = re.compile(r"/inzerat/(\d+)/")
ELADO_MINTA = re.compile(r"'rating'\s*,\s*'\d+'\s*,\s*'(\d+)'")


def naplo(uzenet):
    sor = f"[{datetime.now():%Y-%m-%d %H:%M}] {uzenet}"
    print(sor, flush=True)
    try:
        with open(NAPLO_FAJL, "a", encoding="utf-8") as f:
            f.write(sor + "\n")
    except OSError:
        pass


def norm(szoveg):
    """kisbetű, ékezetek nélkül, egyszerűsített szóközök"""
    s = unicodedata.normalize("NFKD", szoveg or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", s)


# ---------------------------------------------------------------------
#  Letöltés és feldolgozás
# ---------------------------------------------------------------------

def kereso_url(kereses, korzet_km, eltolas=0):
    p = {
        "hledat": kereses, "rubriky": "www",
        "hlokalita": PSC if korzet_km else "", "humkreis": korzet_km or "",
        "cenaod": "", "cenado": "", "Submit": "Hľadať", "kitx": "ano", "order": "",
    }
    if eltolas:
        p["crz"] = eltolas
    return "https://www.bazos.sk/search.php?" + urlencode(p)


def ar_szamma(szoveg):
    s = (szoveg or "").replace("\xa0", " ")
    m = re.search(r"(\d[\d ]*)\s*€", s)
    if not m:
        return None
    try:
        return int(m.group(1).replace(" ", ""))
    except ValueError:
        return None


def hirdetesek_kiolvasasa(html):
    leves = BeautifulSoup(html, "html.parser")
    eredmeny = []
    for blokk in leves.select("div.inzeraty"):
        link = blokk.select_one("h2.nadpis a")
        if not link:
            continue
        href = link.get("href", "")
        m = ID_MINTA.search(href)
        if not m:
            continue
        info = blokk.select_one(".velikost10")
        info_txt = info.get_text(" ", strip=True) if info else ""
        datum = re.search(r"\[(\d{1,2})\.(\d{1,2})\.\s*(\d{4})\]", info_txt)
        akce = blokk.select_one(".inzeratyakce")
        elado = ELADO_MINTA.search(str(akce)) if akce else None
        rovat = re.match(r"https?://([a-z]+)\.bazos\.sk", href)
        ar_elem = blokk.select_one(".inzeratycena")
        hely = blokk.select_one(".inzeratylok")
        popis = blokk.select_one(".popis")
        eredmeny.append({
            "id": m.group(1),
            "cim": link.get_text(" ", strip=True),
            "url": href,
            "rovat": rovat.group(1) if rovat else "",
            "ar": ar_szamma(ar_elem.get_text(" ", strip=True) if ar_elem else ""),
            "hely": hely.get_text(" ", strip=True) if hely else "",
            "leiras": popis.get_text(" ", strip=True)[:400] if popis else "",
            "top": "TOP" in info_txt,
            "datum": f"{datum.group(3)}-{int(datum.group(2)):02d}-{int(datum.group(1)):02d}" if datum else "",
            "elado": elado.group(1) if elado else "",
        })
    return eredmeny


class Bazos:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(FEJLEC)
        self.keresek = 0
        self.hibak = 0

    def keres(self, kereses, korzet_km, oldalak=1):
        osszes = []
        for i in range(oldalak):
            url = kereso_url(kereses, korzet_km, i * 40)
            try:
                v = self.s.get(url, timeout=25)
                v.raise_for_status()
            except requests.RequestException as hiba:
                self.hibak += 1
                naplo(f"HIBA a letöltésnél ({kereses}): {hiba}")
                break
            finally:
                self.keresek += 1
                time.sleep(random.uniform(*SZUNET))
            lap = hirdetesek_kiolvasasa(v.text)
            osszes.extend(lap)
            if len(lap) < 40:
                break
        return osszes


# ---------------------------------------------------------------------
#  Termékfelismerés
# ---------------------------------------------------------------------

IPHONE_MINTA = re.compile(
    r"iphone\s*(se|xr|xs|x|1[1-9])\s*(pro\s*max|promax|pro|plus|mini|max|e(?![a-z]))?")
TARHELY_MINTA = re.compile(r"\b(64|128|256|512|1|2)\s*(gb|g|tb)\b")


def tarhely(szoveg, csupasz=False):
    for m in TARHELY_MINTA.finditer(szoveg):
        szam, egyseg = int(m.group(1)), m.group(2)
        if egyseg == "tb" and szam in (1, 2):
            return f"{szam}TB"
        if egyseg in ("gb", "g") and szam in (64, 128, 256, 512):
            return f"{szam}GB"
    if csupasz:   # "iPhone 13 Pro 256" – mértékegység nélkül
        m = re.search(r"\b(64|128|256|512)\b", szoveg)
        if m:
            return f"{m.group(1)}GB"
    return None


def akku_szazalek(szoveg):
    m = re.search(r"(?:bat\w*|zdravie|kondic\w*|akku)\D{0,15}(\d{2,3})\s*%|(\d{2,3})\s*%\s*(?:bat|zdrav|kondic)", szoveg)
    if m:
        v = int(m.group(1) or m.group(2))
        if 50 <= v <= 100:
            return v
    return None


def termek_felismeres(h, figyelt):
    """Visszaad: (kulcs, megjelenítendő név, referencia-keresés, megjegyzések) vagy None."""
    cim, leiras = norm(h["cim"]), norm(h["leiras"])
    tipus = figyelt["tipus"]
    megj = []
    if figyelt.get("tilt") and re.search(figyelt["tilt"], cim):
        return None

    if tipus == "iphone":
        m = IPHONE_MINTA.search(cim)
        if not m:
            return None
        modell = m.group(1)
        valt = (m.group(2) or "").replace(" ", "")
        valt = {"promax": "pro max"}.get(valt, valt)
        tar = tarhely(cim, csupasz=True) or tarhely(leiras)
        if not tar:
            megj.append("tárhely ismeretlen")
        akku = akku_szazalek(cim + " " + leiras)
        if akku:
            megj.append(f"akku {akku}%")
            if akku < 80:
                megj.append("gyenge akku")
        nev = f"iPhone {modell.upper() if modell in ('se','xr','xs','x') else modell}" + \
              ("e" if valt == "e" else f" {valt.title()}" if valt else "") + (f" {tar}" if tar else "")
        kulcs = f"iphone|{modell}|{valt}|{tar or '?'}"
        ref_keres = " ".join(x for x in ["iphone", modell + ("e" if valt == "e" else ""), "" if valt == "e" else valt,
                                         (tar or "").replace("GB", "").replace("TB", "tb")] if x)
        return kulcs, nev, ref_keres, megj

    if tipus == "kod":
        marka = figyelt["marka"]
        if marka not in cim:
            return None
        if marka == "milwaukee":
            m = re.search(r"\bm(12|18)\s*-?\s*(f?[a-z]{2,5}\d?)\b", cim)
            kod = f"M{m.group(1)} {m.group(2).upper()}" if m and m.group(2) not in ("fuel",) else None
        else:
            m = re.search(r"\b(d[a-z]{1,3}\s?\d{3}[a-z]?|[a-z]{2}\d{4})\b", cim)
            kod = m.group(1).replace(" ", "").upper() if m else None
        if not kod:
            return None
        csak_gep = any(s in cim + " " + leiras for s in ("bez bat", "bez aku", "bez nabij", "telo", "solo", "body",
                                                          "bez prislus", "iba stroj", "len stroj"))
        allapot = "csak gép" if csak_gep else "szett/ismeretlen"
        megj.append(allapot)
        return f"{marka}|{kod}|{'gep' if csak_gep else 'x'}", f"{figyelt['nev']} {kod} ({allapot})", f"{marka} {kod}", megj

    if tipus == "minta":
        if figyelt.get("kell") and not re.search(figyelt["kell"], cim):
            return None
        m = re.search(figyelt["minta"], cim)
        if not m:
            return None
        valt = norm(" ".join(g for g in m.groups() if g)) if m.groups() else ""
        valt = re.sub(r"\s+", " ", valt).strip()
        kulcs = f"{figyelt['nev'].lower()}|{valt}"
        nev = f"{figyelt['nev']} {valt.title()}".strip()
        return kulcs, nev, f"{figyelt['kereses']} {valt}".strip(), megj

    return None


def kizarando(h):
    cim, leiras = norm(h["cim"]), norm(h["leiras"])
    if any(s in cim for s in KIZARO_CIM) or any(s in leiras for s in KIZARO_LEIRAS):
        return "kizáró szó"
    if re.match(rf"^\W*(?:\w+\s)?{TARTOZEK}\b", cim) or re.search(rf"\b{TARTOZEK}\s+(na|pre|do|k|ku)\b", cim) \
            or "krabica od" in cim or "len krabica" in cim:
        return "tartozék"
    return None


# ---------------------------------------------------------------------
#  Piaci ár (referencia)
# ---------------------------------------------------------------------

def referencia(bazos, allapot, kulcs, ref_keres, figyelt):
    tar = allapot["referencia"].get(kulcs)
    if tar and tar["datum"] >= (datetime.now() - timedelta(days=REFERENCIA_NAP)).strftime("%Y-%m-%d"):
        return tar
    talalatok = bazos.keres(ref_keres, 0, oldalak=2)
    arak, eladok = [], set()
    for h in talalatok:
        if h["top"] or h["ar"] is None or h["rovat"] not in figyelt["rovatok"]:
            continue
        if h["ar"] < figyelt.get("min_ar", 0) or kizarando(h):
            continue
        f = termek_felismeres(h, figyelt)
        if not f or f[0] != kulcs:
            continue
        if h["elado"] and h["elado"] in eladok:   # eladónként egy ár
            continue
        eladok.add(h["elado"])
        arak.append(h["ar"])
    if len(arak) >= 5:
        arak.sort()
        # a szélsőségeket (alsó/felső 10%) kihagyjuk
        vag = arak[len(arak) // 10: len(arak) - len(arak) // 10] or arak
        eredmeny = {"median": int(statistics.median(vag)), "also": arak[len(arak) // 4],
                    "felso": arak[(3 * len(arak)) // 4], "n": len(arak), "datum": datetime.now().strftime("%Y-%m-%d")}
    else:
        eredmeny = {"median": None, "n": len(arak), "datum": datetime.now().strftime("%Y-%m-%d")}
    allapot["referencia"][kulcs] = eredmeny
    return eredmeny


# ---------------------------------------------------------------------
#  Állapot
# ---------------------------------------------------------------------

def allapot_betoltese():
    if ALLAPOT_FAJL.exists():
        try:
            a = json.loads(ALLAPOT_FAJL.read_text(encoding="utf-8"))
            for k, v in {"latott": {}, "ujlap": {}, "eladok": {}, "referencia": {}, "jeloltek": [],
                         "arazatlan": [], "utolso_jelentes": ""}.items():
                a.setdefault(k, v)
            return a, False
        except (json.JSONDecodeError, OSError):
            pass
    return {"latott": {}, "ujlap": {}, "eladok": {}, "referencia": {}, "jeloltek": [], "arazatlan": [],
            "utolso_jelentes": ""}, True


def allapot_mentese(a):
    hatar = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    a["latott"] = {k: v for k, v in a["latott"].items() if v >= hatar}
    a["ujlap"] = {k: v for k, v in a["ujlap"].items() if v >= hatar}
    a["eladok"] = {k: [i for i in v if i in a["latott"]] for k, v in a["eladok"].items()}
    a["eladok"] = {k: v for k, v in a["eladok"].items() if v}
    regi = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    a["referencia"] = {k: v for k, v in a["referencia"].items() if v["datum"] >= regi}
    tegnap = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
    a["jeloltek"] = [j for j in a["jeloltek"] if j["mikor"] >= tegnap][-200:]
    a["arazatlan"] = [j for j in a["arazatlan"] if j["mikor"] >= tegnap][-100:]
    ALLAPOT_FAJL.write_text(json.dumps(a, ensure_ascii=False, indent=0), encoding="utf-8")


def arnaplo_iras(sorok):
    uj = not ARNAPLO_FAJL.exists()
    with open(ARNAPLO_FAJL, "a", encoding="utf-8-sig", newline="") as f:
        if uj:
            f.write("datum;termek;ar;piaci_median;minta_db;kedvezmeny_%;kereskedo;cim;url\n")
        for s in sorok:
            med = s.get("median") or ""
            kedv = round(100 * (1 - s["ar"] / med)) if med else ""
            cim = s["cim"].replace(";", ",")
            f.write(f"{datetime.now():%Y-%m-%d};{s['termek']};{s['ar']};{med};{s.get('n','')};{kedv};"
                    f"{'igen' if s.get('kereskedo') else ''};{cim};{s['url']}\n")


# ---------------------------------------------------------------------
#  E-mail
# ---------------------------------------------------------------------

def email_kuldes(targy, html, szoveg):
    level = MIMEMultipart("alternative")
    level["Subject"] = targy
    level["From"] = EMAIL["kuldo"]
    level["To"] = EMAIL["cimzett"]
    level.attach(MIMEText(szoveg, "plain", "utf-8"))
    level.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL(EMAIL["smtp_szerver"], EMAIL["smtp_port"], timeout=30) as smtp:
        smtp.login(EMAIL["kuldo"], EMAIL["jelszo"])
        smtp.send_message(level)


def tablazat(sorok):
    td = "padding:6px 8px;border-bottom:1px solid #e3e3e3;vertical-align:top"
    html = ("<table style='border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px'>"
            "<tr style='background:#f4f4f4'><th align='left' style='padding:6px 8px'>Hirdetés</th>"
            "<th style='padding:6px 8px'>Ár</th><th style='padding:6px 8px'>Piaci ár (SK)</th>"
            "<th style='padding:6px 8px'>Kedvezmény</th><th style='padding:6px 8px'>Várható haszon</th></tr>")
    txt = []
    for j in sorok:
        jelek = []
        if j.get("surgos"):
            jelek.append("🔥 sürgős eladó")
        if j.get("gyanus"):
            jelek.append("⚠️ gyanúsan olcsó – nézd meg alaposan")
        if j.get("kereskedo"):
            jelek.append("kereskedő")
        jelek += j.get("megj", [])
        if "gyenge akku" in j.get("megj", []):
            jelek.append(f"a haszonból {AKKUCSERE} € akkucserét levontam")
        szin = "#0a7d27" if j["kedvezmeny"] >= MIN_KEDVEZMENY * 100 else "#555"
        html += (f"<tr><td style='{td}'><b>{escape(j['termek'])}</b><br>"
                 f"<a href='{escape(j['url'])}'>{escape(j['cim'])}</a><br>"
                 f"<small style='color:#777'>{escape(j['hely'])} · {escape(j.get('datum',''))}"
                 f"{' · ' + escape(', '.join(jelek)) if jelek else ''}</small></td>"
                 f"<td style='{td};text-align:right'><b>{j['ar']} €</b></td>"
                 f"<td style='{td};text-align:right'>{j['median']} €<br><small style='color:#777'>{j['n']} hirdetés alapján</small></td>"
                 f"<td style='{td};text-align:right;color:{szin}'><b>{-j['kedvezmeny']:+d}%</b></td>"
                 f"<td style='{td};text-align:right;color:{szin}'>~{j['haszon']} €</td></tr>")
        txt.append(f"- {j['termek']}: {j['ar']} € (piaci {j['median']} €, {-j['kedvezmeny']:+d}%, ~{j['haszon']} €) "
                   f"{j['hely']} {', '.join(jelek)}\n  {j['url']}")
    html += "</table>"
    return html, "\n".join(txt)


def ajanlat_email(ajanlatok):
    html_t, txt_t = tablazat(ajanlatok)
    html = (f"<p>{len(ajanlatok)} új, a piaci árnál jóval olcsóbb hirdetés Komárno + {KORZET_KM} km körzetében:</p>"
            + html_t +
            "<p style='color:#777;font-size:12px'>A piaci ár ugyanennek a terméknek a szlovákiai medián kérőára a bazos.sk-n. "
            "A várható haszon = piaci ár − kérőár, költségek nélkül. Vásárlás előtt mindig nézd meg élőben.</p>")
    legjobb = max(ajanlatok, key=lambda a: a["haszon"])
    targy = f"Bazoš: {legjobb['termek']} {legjobb['ar']} € (-{legjobb['kedvezmeny']}%)" + \
            (f" +{len(ajanlatok) - 1} további" if len(ajanlatok) > 1 else "")
    email_kuldes(targy, html, txt_t)


def napi_jelentes(allapot, cim="Napi összefoglaló"):
    ma = datetime.now().strftime("%Y-%m-%d")
    hatar = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    friss = [j for j in allapot["jeloltek"] if j["mikor"] >= hatar]
    legjobbak = sorted([j for j in friss if not j.get("kereskedo")], key=lambda j: -j["kedvezmeny"])[:8]
    ajanlatok = [j for j in friss if j.get("ajanlat")]
    stat = allapot.get("stat", {})
    resz = (f"<p><b>Az elmúlt 24 órában:</b> {stat.get('futas', 0)} futás, {stat.get('uj', 0)} új releváns hirdetés "
            f"a körzetben, ebből {len(ajanlatok)} volt jó ajánlat (≥{int(MIN_KEDVEZMENY*100)}% és ≥{MIN_HASZON} € "
            f"a piaci ár alatt).</p>")
    if legjobbak:
        h, t = tablazat(legjobbak)
        resz += "<p><b>A legolcsóbb új hirdetések a piaci árhoz képest</b> (akkor is, ha nem érték el a határt):</p>" + h
    else:
        h, t = "", "Nem volt új, árazható hirdetés."
        resz += "<p>Nem volt új, árazható hirdetés a körzetben.</p>"
    # árazatlan, de érdekes hirdetések
    arazatlan = [j for j in allapot["arazatlan"] if j["mikor"] >= hatar][:15]
    if arazatlan:
        resz += ("<p><b>Új hirdetések, amelyeknek nem tudtam piaci árat számolni</b> – érdemes ránézni:</p>"
                 "<ul style='font-family:Arial;font-size:13px'>")
        for j in arazatlan:
            resz += (f"<li><a href='{escape(j['url'])}'>{escape(j['cim'])}</a> – <b>{j['ar']} €</b>, "
                     f"{escape(j['hely'])} <span style='color:#777'>({escape(j['ok'])})</span></li>")
        resz += "</ul>"
    # piaci árak
    refs = sorted(((k, v) for k, v in allapot["referencia"].items() if v.get("median")), key=lambda x: x[0])
    if refs:
        resz += ("<p><b>Aktuális szlovákiai piaci árak</b> (medián kérőár):</p><table style='font-family:Arial;"
                 "font-size:13px;border-collapse:collapse'>")
        for k, v in refs[:40]:
            resz += (f"<tr><td style='padding:2px 10px 2px 0'>{escape(kulcs_nev(k))}</td>"
                     f"<td style='padding:2px 10px;text-align:right'><b>{v['median']} €</b></td>"
                     f"<td style='padding:2px 10px;color:#777'>{v.get('also','')}–{v.get('felso','')} €, {v['n']} db</td></tr>")
        resz += "</table>"
    email_kuldes(f"Bazoš {cim.lower()}: {len(ajanlatok)} ajánlat, {len(legjobbak)} jelölt ({ma})", resz, t)
    allapot["utolso_jelentes"] = ma
    allapot["stat"] = {"futas": 0, "uj": 0}


def kulcs_nev(k):
    r = k.split("|")
    if r[0] == "iphone":
        return f"iPhone {r[1]} {r[2].title()} {'' if r[3] == '?' else r[3]}".replace("  ", " ").strip()
    if len(r) == 3:
        return f"{r[0].title()} {r[1]}" + (" (csak gép)" if r[2] == "gep" else "")
    return " ".join(x.title() for x in r if x)


# ---------------------------------------------------------------------
#  Fő program
# ---------------------------------------------------------------------

def main():
    if "--proba" not in sys.argv and (not EMAIL["kuldo"] or not EMAIL["jelszo"]):
        naplo("HIBA: hiányzik a GMAIL_USER vagy a GMAIL_APP_PASSWORD beállítás.")
        sys.exit(1)

    if "--teszt-email" in sys.argv:
        email_kuldes("Bazoš figyelő: teszt", "<p>Ha ezt látod, a levélküldés működik.</p>", "Teszt.")
        naplo("Teszt e-mail elküldve.")
        return

    proba = "--proba" in sys.argv
    allapot, elso = allapot_betoltese()
    allapot.setdefault("stat", {"futas": 0, "uj": 0})
    bazos = Bazos()
    most = datetime.now().strftime("%Y-%m-%d %H:%M")
    ma = datetime.now().strftime("%Y-%m-%d")
    ajanlatok, naplo_sorok, uj_db = [], [], 0

    for fig in FIGYELT:
        talalatok = bazos.keres(fig["kereses"], KORZET_KM, fig.get("oldalak", 1))
        # eladók hirdetésszáma ebben a keresésben és az elmúlt 30 napban (kereskedő-szűréshez)
        most_db = {}
        for h in talalatok:
            if h["elado"]:
                most_db[h["elado"]] = most_db.get(h["elado"], 0) + 1
        for h in talalatok:
            if h["elado"]:
                lista = allapot["eladok"].setdefault(h["elado"], [])
                if h["id"] not in lista:
                    lista.append(h["id"])
        vizsgalt = 0
        for h in talalatok:
            if h["id"] in allapot["latott"]:
                continue
            allapot["latott"][h["id"]] = ma
            if h["top"] or h["ar"] is None or h["rovat"] not in fig["rovatok"] or kizarando(h):
                continue
            if h["ar"] < fig.get("min_ar", 0):   # pl. "1 €" kereskedői ár, vagy PS5-höz játék
                continue
            f = termek_felismeres(h, fig)
            if not f:
                # márka szerepel, de a pontos típus nem olvasható ki (pl. "Makita aku náradie")
                if fig["tipus"] == "kod" and fig["marka"] in norm(h["cim"]):
                    allapot["arazatlan"].append({"termek": fig["nev"], "cim": h["cim"], "url": h["url"],
                                                 "ar": h["ar"], "hely": h["hely"], "mikor": most,
                                                 "ok": "a típus nem derül ki a címből"})
                continue
            kulcs, nev, ref_keres, megj = f
            # újrafeltöltés: ugyanaz az eladó, ugyanaz a termék, ugyanaz az ár
            ujlap = f"{h['elado']}|{kulcs}|{h['ar']}"
            ujrafeltoltes = ujlap in allapot["ujlap"]
            allapot["ujlap"][ujlap] = ma
            if ujrafeltoltes:
                continue
            vizsgalt += 1
            uj_db += 1
            ref = referencia(bazos, allapot, kulcs, ref_keres, fig)
            kereskedo = bool(h["elado"]) and (most_db.get(h["elado"], 0) >= KERESKEDO_HIRDETES or
                                              len(allapot["eladok"].get(h["elado"], [])) >= 2 * KERESKEDO_HIRDETES)
            sor = {**h, "termek": nev, "megj": megj, "kereskedo": kereskedo, "mikor": most,
                   "median": ref.get("median"), "n": ref.get("n", 0)}
            sor["ujlap"] = ujlap
            naplo_sorok.append(sor)
            if not ref.get("median"):
                allapot["arazatlan"].append({"termek": nev, "cim": h["cim"], "url": h["url"], "ar": h["ar"],
                                             "hely": h["hely"], "mikor": most,
                                             "ok": f"kevés összehasonlító hirdetés ({ref.get('n', 0)} db)"})
                continue
            kedv = round(100 * (1 - h["ar"] / ref["median"]))
            haszon = ref["median"] - h["ar"] - (AKKUCSERE if "gyenge akku" in megj else 0)
            sor.update({"kedvezmeny": kedv, "haszon": haszon,
                        "gyanus": h["ar"] < ref["median"] * GYANUS_ARANY,
                        "surgos": any(s in norm(h["cim"] + " " + h["leiras"]) for s in SURGOS_SZAVAK)})
            sor["ajanlat"] = (kedv >= MIN_KEDVEZMENY * 100 and haszon >= MIN_HASZON and not kereskedo
                              and "tárhely ismeretlen" not in megj)
            allapot["jeloltek"].append({k: sor[k] for k in ("termek", "cim", "url", "ar", "median", "n", "kedvezmeny",
                                                             "haszon", "hely", "datum", "megj", "kereskedo", "gyanus",
                                                             "surgos", "ajanlat", "mikor")})
            if sor["ajanlat"]:
                ajanlatok.append(sor)
        naplo(f"{fig['nev']}: {len(talalatok)} hirdetés, ebből {vizsgalt} új és árazható")

    if bazos.keresek and bazos.hibak == bazos.keresek:
        naplo("HIBA: a bazos.sk egyáltalán nem volt elérhető.")
        sys.exit(1)

    allapot["stat"]["futas"] = allapot["stat"].get("futas", 0) + 1
    allapot["stat"]["uj"] = allapot["stat"].get("uj", 0) + uj_db
    naplo(f"Lekérések: {bazos.keresek}, ismert piaci árak: {len(allapot['referencia'])}, jó ajánlat: {len(ajanlatok)}")

    if proba:
        print("\n=== PRÓBAFUTÁS (e-mail nem ment ki) ===")
        for j in sorted([j for j in allapot["jeloltek"] if j["mikor"] == most], key=lambda j: -j["kedvezmeny"]):
            print(f"{'AJÁNLAT ' if j['ajanlat'] else '        '}{j['termek']}: {j['ar']} € | piaci {j['median']} € "
                  f"({j['n']} db) | {-j['kedvezmeny']:+d}% | ~{j['haszon']} € | {j['hely']} {'[kereskedő]' if j['kereskedo'] else ''}"
                  f" {', '.join(j['megj'])}\n         {j['url']}")
        return

    if elso:
        # Első futás: a meglévő hirdetésekről nem küldünk azonnali riasztást,
        # de induló jelentést igen, hogy azonnal lásd a legjobb jelölteket.
        naplo("Első futás: induló jelentés küldése.")
        try:
            napi_jelentes(allapot, "Induló jelentés")
        except Exception as hiba:  # noqa: BLE001
            naplo(f"HIBA az e-mailnél: {hiba}")
    else:
        if ajanlatok:
            try:
                ajanlat_email(ajanlatok)
                naplo(f"E-mail elküldve {len(ajanlatok)} ajánlattal.")
            except Exception as hiba:  # noqa: BLE001
                naplo(f"HIBA az e-mailnél: {hiba}")
                for h in ajanlatok:          # legközelebb újra próbálja
                    allapot["latott"].pop(h["id"], None)
                    allapot["ujlap"].pop(h["ujlap"], None)
                    allapot["jeloltek"] = [j for j in allapot["jeloltek"] if j["url"] != h["url"]]
        if "--jelentes" in sys.argv or (datetime.now().hour >= NAPI_JELENTES_ORA and allapot["utolso_jelentes"] != ma):
            try:
                napi_jelentes(allapot)
                naplo("Napi összefoglaló elküldve.")
            except Exception as hiba:  # noqa: BLE001
                naplo(f"HIBA a napi összefoglalónál: {hiba}")

    arnaplo_iras(naplo_sorok)
    allapot_mentese(allapot)


if __name__ == "__main__":
    main()
