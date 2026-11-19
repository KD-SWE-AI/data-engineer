#!/usr/bin/env python3
# Shebang: Ermöglicht direktes Ausführen als Skript unter Linux/WSL2 (./main.py) mit dem System-/venv-Python.

from __future__ import annotations
# Sorgt dafür, dass Python Typ-Annotationen beim Laden einer Datei nicht sofort auswertet, sondern intern als einfache Strings speichert.
import argparse
# Standardbibliothek: Ermöglicht CLI-Argumente wie --input-file und --snapshot-date für Tests und Wiederanläufe.
import hashlib
# Standardbibliothek: Erzeugt reproduzierbare Hashes (z. B. run_id aus Dateiname + Stichtag für Idempotenz).
import json
# Standardbibliothek: Parst die JSON-Antwort der dm-API bzw. gespeicherte Rohdaten-Snapshots.
import re
# Standardbibliothek: Reguläre Ausdrücke für Formatprüfungen (GTIN-Ziffern) und Preis-Strings.
import sys
# Standardbibliothek: Für sauberen Programmabbruch mit Exit-Code über sys.exit(...) (wichtig für geplante Läufe).
from datetime import datetime, timezone
# Standardbibliothek: Zeitstempel in UTC für konsistente Datums-/Laufprotokolle ohne Zeitzonen-Chaos.
from pathlib import Path
# Standardbibliothek: Betriebssystemunabhängige Pfadverarbeitung (Windows/WSL2/Linux).
from typing import Any
# Standardbibliothek: Typisierung für verschachtelte JSON-Dictionaries und Listen.
from urllib.error import URLError
# Standardbibliothek: Fängt Netzwerk-/HTTP-Fehler beim API-Abruf gezielt ab.
from urllib.request import Request, urlopen
# Standardbibliothek: HTTP-Client ohne Zusatzpakete (spart Abhängigkeiten auf der VM).
import duckdb
# Eingebettete analytische SQL-Datenbank: Ideal für Sternschema, Joins, Fensterfunktionen und schnelle lokale Läufe.
import matplotlib.pyplot as plt
# Standardbibliothek/Visualisierung: Erzeugt die geforderten PNG-Diagramme (>= 3 Charts) für A5/A6.

DEFAULT_URL = (
    # Basis-Endpunkt der dm-Produktsuche im Crawl-Modus:
    "https://product-search.services.dmtech.com/de/search/crawl"
    # Filtert auf Neuheiten (isNew=true), Facette "Neu", bis zu 1000 Einträge, Seite 0, inkl. Apotheke:
    "?isNew=true&popularFacet=Neu&pageSize=1000&searchType=editorial-search&sort=new&type=search-static&currentPage=0&enablePharmacy=true"
)

BASE_DIR = Path(__file__).resolve().parent
# Basisverzeichnis des Projekts, unabhängig davon, aus welchem Arbeitsordner python main.py gestartet wird.

RAW_DIR = BASE_DIR / "data" / "raw"
# Ordner für unveränderte JSON-Rohsnapshots (Nachvollziehbarkeit, Offline-Fallback und Reproduzierbarkeit).

DB_PATH = BASE_DIR / "data" / "dm_warehouse.duckdb"
# Pfad zur DuckDB-Datei, in der Dimensionen, Fakten und Protokolltabellen persistiert werden.

SQL_PATH = BASE_DIR / "sql" / "analytics.sql"
# Pfad zur externen SQL-Datei mit den Auswertungsabfragen aus Anforderung A4.

OUT_DIR = BASE_DIR / "outputs"
# Zielordner für die generierten Matplotlib-Diagramme.


def ensure_dirs() -> None:
    # Legt benötigte Verzeichnisse beim Start an, falls sie noch nicht existieren (verhindert FileNotFoundError).
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    # parents=True erstellt auch Zwischenebenen (z. B. data), exist_ok=True macht den Aufruf idempotent.
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Stellt sicher, dass outputs/ für die PNG-Exporte bereitsteht.


# 1. EXTRACT
def fetch_or_load_raw(input_file: Path | None, url: str) -> tuple[list[dict[str, Any]], Path]:
    # Entweder lokale Datei laden (für Tests/neue Batches) oder live von der URL ziehen.
    if input_file is not None:
        # Prüft explizit, ob eine übergebene Datei existiert, bevor json.loads() fehlschlägt.
        if not input_file.exists():
            raise FileNotFoundError(f"Eingabedatei nicht gefunden: {input_file}")
        raw_path = input_file
        # Merkt sich den genutzten Quellpfad für das Protokoll.
    else:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Tagesstempel im ISO-Format, damit pro Tag ein sauber benannter Snapshot entsteht.
        raw_path = RAW_DIR / f"dm_neu_{today_str}.json"
        # Zielpfad für den heutigen Rohdaten-Snapshot.
        req = Request(
            url,
            headers={
                # Setzt einen klaren User-Agent und JSON-Accept-Header, damit der Endpunkt sauber antwortet.
                "User-Agent": "Mozilla/5.0 (DataEngineering-Kursprojekt)",
                "Accept": "application/json",
            },
        )
        try:
            # Timeout von 30s verhindert, dass ein hängender Netzwerkaufruf den ganzen Job blockiert.
            with urlopen(req, timeout=30) as resp:
                payload = resp.read().decode("utf-8")
                # Dekodiert die Rohbytes explizit als UTF-8 (wichtig für Umlaute und das €-Zeichen).
            raw_path.write_text(payload, encoding="utf-8")
            # Speichert die Rohdaten unverändert ab (ELT/Reproduzierbarkeit/Auditierbarkeit).
        except URLError as exc:
            # Fallback bei Netzwerk-/API-Ausfall: Suche nach bereits vorhandenen Snapshots.
            existing = sorted(RAW_DIR.glob("dm_neu_*.json"))
            if existing:
                # Nimmt den neuesten vorhandenen Snapshot, damit der Lauf lokal trotzdem testbar bleibt.
                raw_path = existing[-1]
                print(f"[WARNUNG] API nicht erreichbar ({exc}). Nutze letzten Snapshot: {raw_path.name}")
            else:
                # Kein Fallback vorhanden -> geordneter Abbruch mit aussagekräftiger Fehlermeldung.
                raise RuntimeError(
                    f"API nicht erreichbar ({exc}) und kein lokaler Snapshot unter {RAW_DIR} vorhanden."
                ) from exc

    data = json.loads(raw_path.read_text(encoding="utf-8"))
    # Parst die JSON-Datei in Python-Strukturen.
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        # Frühzeitige Strukturprüfung (Schema-Guard), falls sich das API-Format unerwartet ändert.
        raise ValueError("Unerwartete JSON-Struktur: Top-Level-Key 'products' (Liste) fehlt.")
    return data["products"], raw_path
    # Gibt die Liste der Produkt-Dictionaries und den genutzten Dateipfad zurück.


# 2. DATA QUALITY & RULE-BASED CLEANSING (A2)
def parse_euro_string(val: Any) -> float | None:
    # Vereinheitlicht Preise, die mal als float (12.95) und mal als String ("10,88 €") vorliegen.
    if val is None:
        return None
        # Fehlende Werte bleiben None und werden in der DQ-Prüfung gezielt erkannt.
    if isinstance(val, (int, float)):
        return float(val)
        # Wandelt int/float direkt in float um.
    if isinstance(val, str):
        # Entfernt Währungszeichen, Leerzeichen und geschützte Leerzeichen (\xa0), wandelt Komma in Punkt.
        cleaned = re.sub(r"[^\d,.\-]", "", val).replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            # Nicht parsebare Strings liefern None statt das gesamte Skript abstürzen zu lassen.
            return None
    return None
    # Alle anderen Datentypen werden als ungültig/nicht parsebar behandelt.


def extract_fields(item: dict[str, Any]) -> dict[str, Any]:
    # Kapselt den Zugriff auf verschachtelte Felder des dm-JSONs an einer einzigen Stelle.
    tile = item.get("tileData") or {}
    # Sicherer Zugriff auf tileData (falls null/fehlend, leeres Dict statt AttributeError).
    tracking = tile.get("trackingData") or {}
    # Sicherer Zugriff auf trackingData (enthält u. a. numerischen Preis, Kategorien und isPharmacy).
    rating_obj = (item.get("context") or {}).get("rating") or {}
    # Sicherer Zugriff auf Bewertungsdaten (ratingValue, ratingCount).

    gtin_raw = item.get("gtin") or tile.get("gtin")
    # Nutzt primär top-level gtin, mit Fallback auf tileData.gtin.
    gtin = str(gtin_raw).strip() if gtin_raw is not None else ""
    # Wandelt GTIN in bereinigten String um (verhindert führende-/Nachlauf-Leerzeichen).

    dan_raw = item.get("dan") or tile.get("dan")
    # Extrahiert die dm-Artikelnummer (DAN) als zusätzliches Identifikationsmerkmal.
    dan = str(dan_raw).strip() if dan_raw is not None else ""

    title = str(item.get("title") or (tile.get("title") or {}).get("tileHeadline") or "").strip()
    # Holt den Produkttitel mit Fallback auf tileHeadline.
    brand = str(item.get("brandName") or tracking.get("brand") or (tile.get("brand") or {}).get("name") or "").strip()
    # Holt den Markennamen über mehrere Fallbacks, um Varianten im Payload abzufangen.

    price_eur = parse_euro_string(tracking.get("price"))
    # Bevorzugt den bereits numerischen Preis aus trackingData.price.
    if price_eur is None:
        # Fallback auf den formatierten Preis-String unter tileData.price.price.current.value.
        price_str = (((tile.get("price") or {}).get("price") or {}).get("current") or {}).get("value")
        price_eur = parse_euro_string(price_str)

    net_str = (((tile.get("netPrice") or {}).get("price") or {}).get("current") or {}).get("value")
    # Liest den Nettopreis aus tileData.netPrice aus.
    net_price_eur = parse_euro_string(net_str)

    raw_cats = tracking.get("categories") or []
    # Liest die Kategorienliste aus trackingData.categories.
    cats = [str(c).strip() for c in raw_cats if isinstance(c, str) and c.strip()]
    # Filtert leere oder nicht-string Einträge aus der Kategorienliste.

    is_pharmacy = bool(tracking.get("isPharmacy", tile.get("isPharmacy", False)))
    # Wandelt das Apotheken-Flag sauber in einen booleschen Wert um.
    is_limited = any(
        # Prüft Eyecatcher-Texte auf "limitiert", um limitierte Editionen als Merkmal zu erfassen.
        "limitiert" in str(ec.get("alt", "")).lower()
        for ec in (tile.get("eyecatchers") or [])
        if isinstance(ec, dict)
    )

    rating_val = parse_euro_string(rating_obj.get("ratingValue"))
    # Extrahiert die Durchschnittsbewertung (falls vorhanden).
    rating_cnt_raw = rating_obj.get("ratingCount")
    rating_count = int(rating_cnt_raw) if isinstance(rating_cnt_raw, (int, float)) else 0
    # Stellt sicher, dass rating_count immer eine Ganzzahl (Standard 0) ist.

    return {
        "gtin": gtin,
        "dan": dan,
        "title": title,
        "brand": brand,
        "price_eur": price_eur,
        "net_price_eur": net_price_eur,
        "cats": cats,
        "is_pharmacy": is_pharmacy,
        "is_limited": is_limited,
        "rating_value": rating_val,
        "rating_count": rating_count,
    }


def derive_price_tier(price_eur: float) -> str:
    # Regelbasierte Klassifikation des Preises in drei verständliche Preissegmente für Auswertungen.
    if price_eur < 5.0:
        return "Günstig (< 5 €)"
    if price_eur <= 15.0:
        return "Mittel (5–15 €)"
    return "Premium (> 15 €)"


def run_data_quality(
    raw_products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    # Zählt für jede Prüfung geprüft, bestanden und abgelehnt (Kontrollzahlen nach A2).
    checks = {
        "1_pflichtfelder": {"checked": 0, "passed": 0, "rejected": 0},
        "2_format_gtin_dan": {"checked": 0, "passed": 0, "rejected": 0},
        "3_wertebereich_preis": {"checked": 0, "passed": 0, "rejected": 0},
        "4_duplikat_gtin": {"checked": 0, "passed": 0, "rejected": 0},
    }
    rejects: list[dict[str, str]] = []
    # Sammelt abgelehnte Zeilen inkl. Schlüssel und Grund für dq_reject_log.
    valid_rows: list[dict[str, Any]] = []
    # Sammelt bereinigte, valide Datensätze für das Sternschema.
    seen_gtins: set[str] = set()
    # Set für O(1)-Duplikaterkennung innerhalb des aktuellen Batches.

    for idx, raw_item in enumerate(raw_products):
        f = extract_fields(raw_item)
        record_key = f["gtin"] if f["gtin"] else f"ROW_{idx}"
        # Nutzt GTIN als Schlüssel im Reject-Protokoll oder ROW_<idx>, falls GTIN fehlt.

        # Prüfung 1: Pflichtfelder dürfen nicht fehlen/leer sein.
        checks["1_pflichtfelder"]["checked"] += 1
        if not f["gtin"] or not f["title"] or not f["brand"] or f["price_eur"] is None:
            checks["1_pflichtfelder"]["rejected"] += 1
            rejects.append(
                {
                    "record_key": record_key,
                    "check_name": "1_pflichtfelder",
                    "reason": "Fehlendes Pflichtfeld (gtin/title/brand/price)",
                }
            )
            continue
            # Überspringt die Zeile, damit sie nicht in spätere Prüfungen oder Fakten gelangt.
        checks["1_pflichtfelder"]["passed"] += 1

        # Prüfung 2: GTIN muss aus 8 bis 14 Ziffern bestehen.
        checks["2_format_gtin_dan"]["checked"] += 1
        if not re.fullmatch(r"\d{8,14}", f["gtin"]):
            checks["2_format_gtin_dan"]["rejected"] += 1
            rejects.append(
                {
                    "record_key": record_key,
                    "check_name": "2_format_gtin_dan",
                    "reason": f"Ungültiges GTIN-Format: '{f['gtin']}'",
                }
            )
            continue
        checks["2_format_gtin_dan"]["passed"] += 1

        # Prüfung 3: Preis muss positiv und plausibel sein (schließt 0 €, negative Werte und Ausreißer aus).
        checks["3_wertebereich_preis"]["checked"] += 1
        if not (0.0 < f["price_eur"] <= 500.0):
            checks["3_wertebereich_preis"]["rejected"] += 1
            rejects.append(
                {
                    "record_key": record_key,
                    "check_name": "3_wertebereich_preis",
                    "reason": f"Preis außerhalb Wertebereich (0..500): {f['price_eur']}",
                }
            )
            continue
        checks["3_wertebereich_preis"]["passed"] += 1

        # Prüfung 4: Doppelte GTINs innerhalb desselben Rohdaten-Batches abweisen.
        checks["4_duplikat_gtin"]["checked"] += 1
        if f["gtin"] in seen_gtins:
            checks["4_duplikat_gtin"]["rejected"] += 1
            rejects.append(
                {
                    "record_key": record_key,
                    "check_name": "4_duplikat_gtin",
                    "reason": "Doppelte GTIN innerhalb der Rohdatei",
                }
            )
            continue
        seen_gtins.add(f["gtin"])
        # Merkt die gültige GTIN für zukünftige Duplikatprüfungen im selben Lauf.
        checks["4_duplikat_gtin"]["passed"] += 1

        # Regelbasierte Ableitung fehlender Kategorien ohne manuelle Eingriffe.
        top_cat = f["cats"][0] if f["cats"] else "Unbekannt"
        sub_cat = f["cats"][-1] if f["cats"] else "Unbekannt"
        # Falls kein Nettopreis geliefert wurde, wird er regelbasiert aus dem Bruttopreis abgeleitet (19 % MwSt.).
        net_price = f["net_price_eur"] if f["net_price_eur"] is not None else round(f["price_eur"] / 1.19, 2)

        valid_rows.append(
            {
                "gtin": f["gtin"],
                "dan": f["dan"],
                "title": " ".join(f["title"].split()),
                # Normalisiert Mehrfachleerzeichen/Zeilenumbrüche im Produkttitel.
                "brand_name": " ".join(f["brand"].split()),
                # Normalisiert Mehrfachleerzeichen im Markennamen.
                "top_category": top_cat,
                "sub_category": sub_cat,
                "price_eur": round(f["price_eur"], 2),
                # Rundet den Preis sauber auf 2 Nachkommastellen (Cent-Genauigkeit).
                "net_price_eur": round(net_price, 2),
                "price_tier": derive_price_tier(f["price_eur"]),
                "is_pharmacy": f["is_pharmacy"],
                "is_limited": f["is_limited"],
                "rating_value": f["rating_value"],
                "rating_count": f["rating_count"],
            }
        )

    dq_metrics = [
        # Wandelt das Checks-Dictionary in eine strukturierte Liste für Ausgabe und Protokoll um.
        {
            "check_name": name,
            "checked": counts["checked"],
            "passed": counts["passed"],
            "rejected": counts["rejected"],
        }
        for name, counts in checks.items()
    ]
    return valid_rows, dq_metrics, rejects


# 3. STAR SCHEMA & IDEMPOTENT LOAD (A3, A5)
def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    # Erstellt Tabellen und Sequenzen nur falls noch nicht vorhanden (idempotente Schema-Initialisierung).
    con.execute(
        """
        CREATE SEQUENCE IF NOT EXISTS seq_brand_sk START 1;
        -- Surrogate-Key-Sequenz für dim_brand (da bei SCD2 mehrere Versionen derselben Marke existieren können).
        CREATE SEQUENCE IF NOT EXISTS seq_category_sk START 1;
        -- Surrogate-Key-Sequenz für dim_category.

        CREATE TABLE IF NOT EXISTS dim_brand (
            brand_sk INTEGER PRIMARY KEY DEFAULT nextval('seq_brand_sk'),
            -- Technischer Primärschlüssel (Surrogate Key) der Markenversion.
            brand_key VARCHAR NOT NULL,
            -- Fachlicher Schlüssel (normalisierter Markenname) zum Wiederfinden der Marke.
            brand_name VARCHAR NOT NULL,
            brand_segment VARCHAR NOT NULL,
            valid_from TIMESTAMP NOT NULL,
            -- Beginn der Gültigkeit dieser Attributversion.
            valid_to TIMESTAMP,
            -- Ende der Gültigkeit (NULL = aktuell gültige Version).
            is_current BOOLEAN NOT NULL
            -- Schneller Filter auf den aktuellen Stand (TRUE = aktiv).
        );

        CREATE TABLE IF NOT EXISTS dim_category (
            category_sk INTEGER PRIMARY KEY DEFAULT nextval('seq_category_sk'),
            category_key VARCHAR NOT NULL UNIQUE,
            -- Eindeutiger Schlüssel aus Haupt- und Unterkategorie für SCD Typ 1.
            top_category VARCHAR NOT NULL,
            sub_category VARCHAR NOT NULL,
            updated_at TIMESTAMP NOT NULL
            -- Zeitstempel der letzten Überschreibung (SCD Typ 1).
        );

        CREATE TABLE IF NOT EXISTS fact_product_listings (
            gtin VARCHAR NOT NULL,
            dan VARCHAR,
            snapshot_date DATE NOT NULL,
            brand_sk INTEGER NOT NULL,
            -- Fremdschlüssel auf dim_brand.
            category_sk INTEGER NOT NULL,
            -- Fremdschlüssel auf dim_category.
            product_title VARCHAR NOT NULL,
            price_eur DOUBLE NOT NULL,
            -- Zentraler numerischer Messwert (Bruttopreis in EUR).
            net_price_eur DOUBLE NOT NULL,
            price_tier VARCHAR NOT NULL,
            is_pharmacy BOOLEAN NOT NULL,
            is_limited BOOLEAN NOT NULL,
            rating_value DOUBLE,
            rating_count INTEGER NOT NULL,
            loaded_at TIMESTAMP NOT NULL,
            PRIMARY KEY (gtin, snapshot_date)
            -- Verhindert doppelte Produktzeilen am selben Stichtag -> garantiert Idempotenz.
        );

        CREATE TABLE IF NOT EXISTS dq_reject_log (
            run_id VARCHAR NOT NULL,
            record_key VARCHAR NOT NULL,
            check_name VARCHAR NOT NULL,
            reason VARCHAR NOT NULL,
            logged_at TIMESTAMP NOT NULL
        );

        CREATE TABLE IF NOT EXISTS etl_run_log (
            run_id VARCHAR PRIMARY KEY,
            source_file VARCHAR NOT NULL,
            snapshot_date DATE NOT NULL,
            raw_count INTEGER NOT NULL,
            valid_count INTEGER NOT NULL,
            rejected_count INTEGER NOT NULL,
            dim_brand_inserts INTEGER NOT NULL,
            dim_category_upserts INTEGER NOT NULL,
            fact_rows_total INTEGER NOT NULL,
            executed_at TIMESTAMP NOT NULL
        );
        """
    )


def classify_brand_segment(brand_name: str) -> str:
    # Fachliche Regel: Unterscheidet bekannte dm-Eigenmarken von externen Herstellermarken.
    dm_own_brands = {
        "balea",
        "alverde",
        "alverde naturkosmetik",
        "denkmit",
        "babylove",
        "mivolis",
        "dontodent",
        "ebelin",
        "profissimo",
        "sundance",
        "trend !t up",
        "trend it up",
        "dmbio",
        "saubär",
        "seinz.",
        "langhaarmädchen",
        "jessa",
        "fashy",
    }
    return "dm-Eigenmarke" if brand_name.lower() in dm_own_brands else "Herstellermarke"


def load_star_schema(
    con: duckdb.DuckDBPyConnection,
    valid_rows: list[dict[str, Any]],
    rejects: list[dict[str, str]],
    source_file: Path,
    snapshot_date: str,
) -> dict[str, int]:
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    # Einheitlicher Laufzeitstempel für alle Tabellenänderungen dieses Runs.
    run_id = hashlib.sha256(f"{source_file.name}:{snapshot_date}".encode()).hexdigest()[:12]
    # Deterministische run_id: Bei erneutem Lauf derselben Datei am selben Stichtag wird dieselbe run_id erzeugt.

    con.execute("BEGIN TRANSACTION;")
    # Startet eine atomare Transaktion: Entweder gelingt der gesamte Lade-Schritt oder nichts wird geändert.
    try:
        con.execute("DELETE FROM dq_reject_log WHERE run_id = ?;", [run_id])
        # Löscht alte Reject-Einträge genau dieses run_id vor dem Neu-Einfügen (idempotent bei Wiederholung).
        for rej in rejects:
            con.execute(
                "INSERT INTO dq_reject_log VALUES (?, ?, ?, ?, ?);",
                [run_id, rej["record_key"], rej["check_name"], rej["reason"], now_ts],
            )

        # 1. dim_category pflegen (SCD Typ 1: direkte Überschreibung ohne Historie)
        unique_categories = {(r["top_category"], r["sub_category"]) for r in valid_rows}
        cat_upserts = 0
        for top_cat, sub_cat in sorted(unique_categories):
            # Sortiert für deterministische Verarbeitungsreihenfolge.
            cat_key = f"{top_cat.lower()}::{sub_cat.lower()}"
            existing = con.execute(
                "SELECT category_sk, top_category, sub_category FROM dim_category WHERE category_key = ?;",
                [cat_key],
            ).fetchone()
            if existing is None:
                # Neue Kategorie anlegen.
                con.execute(
                    """
                    INSERT INTO dim_category (category_key, top_category, sub_category, updated_at)
                    VALUES (?, ?, ?, ?);
                    """,
                    [cat_key, top_cat, sub_cat, now_ts],
                )
                cat_upserts += 1
            elif existing[1] != top_cat or existing[2] != sub_cat:
                # Vorhandene Kategorie bei Änderung überschreiben (SCD Typ 1).
                con.execute(
                    """
                    UPDATE dim_category
                    SET top_category = ?, sub_category = ?, updated_at = ?
                    WHERE category_key = ?;
                    """,
                    [top_cat, sub_cat, now_ts, cat_key],
                )
                cat_upserts += 1

        # 2. dim_brand pflegen (SCD Typ 2: alte Version schließen, neue Version einfügen)
        unique_brands = {r["brand_name"] for r in valid_rows}
        brand_inserts = 0
        for brand_name in sorted(unique_brands):
            brand_key = brand_name.lower()
            brand_segment = classify_brand_segment(brand_name)
            current = con.execute(
                """
                SELECT brand_sk, brand_name, brand_segment
                FROM dim_brand
                WHERE brand_key = ? AND is_current = TRUE;
                """,
                [brand_key],
            ).fetchone()

            if current is None:
                # Marke erstmals gesehen -> erste aktive Version anlegen.
                con.execute(
                    """
                    INSERT INTO dim_brand (brand_key, brand_name, brand_segment, valid_from, valid_to, is_current)
                    VALUES (?, ?, ?, ?, NULL, TRUE);
                    """,
                    [brand_key, brand_name, brand_segment, now_ts],
                )
                brand_inserts += 1
            elif current[1] != brand_name or current[2] != brand_segment:
                # Attribut hat sich geändert -> aktuelle Version beenden (valid_to setzen, is_current = FALSE)...
                con.execute(
                    "UPDATE dim_brand SET valid_to = ?, is_current = FALSE WHERE brand_sk = ?;",
                    [now_ts, current[0]],
                )
                # ...und neue aktive Version mit aktualisierten Attributen einfügen.
                con.execute(
                    """
                    INSERT INTO dim_brand (brand_key, brand_name, brand_segment, valid_from, valid_to, is_current)
                    VALUES (?, ?, ?, ?, NULL, TRUE);
                    """,
                    [brand_key, brand_name, brand_segment, now_ts],
                )
                brand_inserts += 1

        brand_map = dict(
            # Lädt die aktuell gültigen brand_sk-Mappings in den Speicher für schnelles Joinen in Python.
            con.execute("SELECT brand_key, brand_sk FROM dim_brand WHERE is_current = TRUE;").fetchall()
        )
        cat_map = dict(con.execute("SELECT category_key, category_sk FROM dim_category;").fetchall())
        # Lädt die category_sk-Mappings in den Speicher.

        # 3. Faktentabelle idempotent für snapshot_date laden (Delete-Insert-Muster je Stichtag)
        con.execute("DELETE FROM fact_product_listings WHERE snapshot_date = CAST(? AS DATE);", [snapshot_date])

        for r in valid_rows:
            b_sk = brand_map[r["brand_name"].lower()]
            c_sk = cat_map[f"{r['top_category'].lower()}::{r['sub_category'].lower()}"]
            con.execute(
                """
                INSERT INTO fact_product_listings (
                    gtin, dan, snapshot_date, brand_sk, category_sk, product_title,
                    price_eur, net_price_eur, price_tier, is_pharmacy, is_limited,
                    rating_value, rating_count, loaded_at
                )
                VALUES (?, ?, CAST(? AS DATE), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                [
                    r["gtin"],
                    r["dan"],
                    snapshot_date,
                    b_sk,
                    c_sk,
                    r["title"],
                    r["price_eur"],
                    r["net_price_eur"],
                    r["price_tier"],
                    r["is_pharmacy"],
                    r["is_limited"],
                    r["rating_value"],
                    r["rating_count"],
                    now_ts,
                ],
            )

        fact_total = con.execute("SELECT COUNT(*) FROM fact_product_listings;").fetchone()[0]
        # Ermittelt die Gesamtzeilenzahl in der Faktentabelle nach dem Laden.

        con.execute("DELETE FROM etl_run_log WHERE run_id = ?;", [run_id])
        # Ersetzt einen etwaigen früheren Log-Eintrag derselben run_id (Idempotenz).
        con.execute(
            "INSERT INTO etl_run_log VALUES (?, ?, CAST(? AS DATE), ?, ?, ?, ?, ?, ?, ?);",
            [
                run_id,
                source_file.name,
                snapshot_date,
                len(valid_rows) + len(rejects),
                len(valid_rows),
                len(rejects),
                brand_inserts,
                cat_upserts,
                fact_total,
                now_ts,
            ],
        )

        con.execute("COMMIT;")
        # Schreibt alle Änderungen dauerhaft in die Datenbank.
        return {
            "dim_brand_inserts": brand_inserts,
            "dim_category_upserts": cat_upserts,
            "fact_rows_total": fact_total,
        }
    except Exception:
        con.execute("ROLLBACK;")
        # Macht bei jedem Fehler alle Teiländerungen rückgängig (sauberes Ende ohne inkonsistenten Zustand).
        raise
        # Reicht die Exception nach oben weiter, damit main() einen Fehler-Exit-Code zurückgibt.


# 4. SQL ANALYTICS & 5. MATPLOTLIB CHARTS (A4, A5)
def run_sql_analytics(con: duckdb.DuckDBPyConnection, sql_path: Path) -> None:
    sql_text = sql_path.read_text(encoding="utf-8")
    statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]

    print("\n=== SQL-AUSWERTUNGEN (sql/analytics.sql) ===")
    for idx, stmt in enumerate(statements, start=1):
        lines = stmt.splitlines()
        first_comment = next(
            (line.lstrip("- ").strip() for line in lines if line.strip().startswith("--")),
            f"Abfrage {idx}",
        )
        # Nur nicht-kommentierte SQL-Zeilen prüfen, damit reine Kommentarblöcke übersprungen werden
        sql_only = "\n".join(
            line for line in lines if line.strip() and not line.strip().startswith("--")
        ).strip()
        if not sql_only:
            continue

        cursor = con.execute(stmt)
        if cursor is None or cursor.description is None:
            continue

        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        print(f"\n[{idx}] {first_comment} (Zeilen: {len(rows)})")
        print(" | ".join(cols))
        for row in rows[:5]:
            print(" | ".join(str(v) for v in row))


def generate_charts(con: duckdb.DuckDBPyConnection) -> list[Path]:
    plt.style.use("seaborn-v0_8-whitegrid")
    # Sauberes, gut lesbares Raster-Design für die Diagramme.
    created_files: list[Path] = []

    # Diagramm 1: Top-10 Marken nach Anzahl + Durchschnittspreis
    top_brands = con.execute(
        """
        SELECT b.brand_name, COUNT(*) AS anzahl, ROUND(AVG(f.price_eur), 2) AS avg_preis
        FROM fact_product_listings f
        JOIN dim_brand b ON f.brand_sk = b.brand_sk
        WHERE f.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_product_listings)
        GROUP BY b.brand_name
        ORDER BY anzahl DESC, avg_preis DESC
        LIMIT 10;
        """
    ).fetchall()

    if top_brands:
        brands = [r[0] for r in reversed(top_brands)]
        # reversed(), damit beim horizontalen Balkendiagramm der höchste Wert oben steht.
        counts = [r[1] for r in reversed(top_brands)]
        avg_prices = [r[2] for r in reversed(top_brands)]

        fig, ax = plt.subplots(figsize=(10, 5.5))
        bars = ax.barh(brands, counts, color="#2b5c8f")
        ax.set_title("Top-10 Marken im dm-„Neu“-Sortiment (Anzahl & Ø-Preis)", fontsize=12, fontweight="bold")
        ax.set_xlabel("Anzahl neuer Produkte")
        for bar, price in zip(bars, avg_prices):
            # Beschriftet jeden Balken direkt mit Stückzahl und Durchschnittspreis.
            ax.text(
                bar.get_width() + 0.2,
                bar.get_y() + bar.get_height() / 2,
                f"{int(bar.get_width())} (Ø {price:.2f} €)",
                va="center",
                fontsize=9,
            )
        ax.set_xlim(0, max(counts) * 1.28)
        # Gibt rechts Platz für die Textbeschriftung, damit nichts abgeschnitten wird.
        plt.tight_layout()
        p1 = OUT_DIR / "chart1_top_brands_avg_price.png"
        fig.savefig(p1, dpi=150)
        # Speichert das Diagramm mit 150 DPI in outputs/.
        plt.close(fig)
        # Schließt die Figure, um Arbeitsspeicher freizugeben.
        created_files.append(p1)

    # Diagramm 2: Top-10 Kategorien nach Produktanzahl
    cat_rows = con.execute(
        """
        SELECT c.top_category, COUNT(*) AS anzahl
        FROM fact_product_listings f
        JOIN dim_category c ON f.category_sk = c.category_sk
        WHERE f.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_product_listings)
        GROUP BY c.top_category
        ORDER BY anzahl DESC
        LIMIT 10;
        """
    ).fetchall()

    if cat_rows:
        cats = [r[0] for r in reversed(cat_rows)]
        cat_counts = [r[1] for r in reversed(cat_rows)]

        fig, ax = plt.subplots(figsize=(10, 5.5))
        bars = ax.barh(cats, cat_counts, color="#3a7d6b")
        ax.set_title("Top-10 Kategorien im dm-„Neu“-Sortiment", fontsize=12, fontweight="bold")
        ax.set_xlabel("Anzahl Produkte")
        for bar in bars:
            ax.text(
                bar.get_width() + 0.2,
                bar.get_y() + bar.get_height() / 2,
                f"{int(bar.get_width())}",
                va="center",
                fontsize=9,
            )
        ax.set_xlim(0, max(cat_counts) * 1.15)
        plt.tight_layout()
        p2 = OUT_DIR / "chart2_category_distribution.png"
        fig.savefig(p2, dpi=150)
        plt.close(fig)
        created_files.append(p2)

    # Diagramm 3: Preisklassen im Vergleich (Eigenmarke vs. Herstellermarke)
    tier_rows = con.execute(
        """
        SELECT f.price_tier, b.brand_segment, COUNT(*) AS anzahl
        FROM fact_product_listings f
        JOIN dim_brand b ON f.brand_sk = b.brand_sk
        WHERE f.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_product_listings)
        GROUP BY f.price_tier, b.brand_segment;
        """
    ).fetchall()

    if tier_rows:
        tiers = ["Günstig (< 5 €)", "Mittel (5–15 €)", "Premium (> 15 €)"]
        lookup = {(r[0], r[1]): r[2] for r in tier_rows}
        # Schneller Lookup nach (Preisklasse, Segment) ohne verschachtelte Schleifen.
        eigen = [lookup.get((t, "dm-Eigenmarke"), 0) for t in tiers]
        hersteller = [lookup.get((t, "Herstellermarke"), 0) for t in tiers]

        x = range(len(tiers))
        width = 0.36
        # Balkenbreite so gewählt, dass zwei Balken nebeneinander in jede Gruppe passen.
        fig, ax = plt.subplots(figsize=(8.5, 5))
        ax.bar([i - width / 2 for i in x], eigen, width, label="dm-Eigenmarke", color="#4c72b0")
        ax.bar([i + width / 2 for i in x], hersteller, width, label="Herstellermarke", color="#dd8452")
        ax.set_xticks(list(x))
        ax.set_xticklabels(tiers)
        ax.set_ylabel("Anzahl Produkte")
        ax.set_title("Preisklassen im Vergleich: dm-Eigenmarke vs. Herstellermarke", fontsize=12, fontweight="bold")
        ax.legend()
        plt.tight_layout()
        p3 = OUT_DIR / "chart3_price_tiers.png"
        fig.savefig(p3, dpi=150)
        plt.close(fig)
        created_files.append(p3)

    return created_files


# MAIN ENTRYPOINT
def main() -> int:
    parser = argparse.ArgumentParser(description="dm Data Engineering Pipeline")
    # Ermöglicht das Übergeben einer lokalen Datei statt des Live-API-Abrufs.
    parser.add_argument(
        "--input-file",
        type=Path,
        default=None,
        help="Optionaler Pfad zu einer lokalen JSON-Rohdatei.",
    )
    # Ermöglicht das Setzen eines beliebigen Stichtags (z. B. zum Simulieren eines neuen Tages).
    parser.add_argument(
        "--snapshot-date",
        type=str,
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Stichtag im Format YYYY-MM-DD (Standard: heute).",
    )
    args = parser.parse_args()

    ensure_dirs()
    raw_products, source_path = fetch_or_load_raw(args.input_file, DEFAULT_URL)
    valid_rows, dq_metrics, rejects = run_data_quality(raw_products)

    con = duckdb.connect(str(DB_PATH))
    # Öffnet/erstellt die lokale DuckDB-Datenbankdatei.
    try:
        init_schema(con)
        load_stats = load_star_schema(con, valid_rows, rejects, source_path, args.snapshot_date)
        run_sql_analytics(con, SQL_PATH)
        chart_paths = generate_charts(con)
    finally:
        # Schließt die Datenbankverbindung zuverlässig, egal ob der Lauf Erfolg hatte oder fehlschlug.
        con.close()

    # Gibt am Ende des Laufs das vollständige Kontroll- und Ladeprotokoll aus (A2, A5, A6).
    print("\n================ PROTOKOLL & KONTROLLZAHLEN ================")
    print(f"Quelle:              {source_path}")
    print(f"Stichtag:            {args.snapshot_date}")
    print(f"Rohzeilen gelesen:   {len(raw_products)}")
    print(f"Valide Zeilen:       {len(valid_rows)}")
    print(f"Abgelehnte Zeilen:   {len(rejects)}")
    print("\n--- Kontrollzahlen je DQ-Prüfung ---")
    for m in dq_metrics:
        print(
            f"  {m['check_name']:<24} | geprüft: {m['checked']:>4} | "
            f"ok: {m['passed']:>4} | abgelehnt: {m['rejected']:>4}"
        )
    print("\n--- Zeilenzahlen je Ladeschritt ---")
    print(f"  dim_brand (SCD2 neue Versionen): {load_stats['dim_brand_inserts']}")
    print(f"  dim_category (SCD1 Upserts):     {load_stats['dim_category_upserts']}")
    print(f"  fact_product_listings (Gesamt):  {load_stats['fact_rows_total']}")
    print("\n--- Erzeugte Diagramme ---")
    for cp in chart_paths:
        print(f"  {cp}")
    print("============================================================")
    return 0
    # Exit-Code 0 signalisiert einen erfolgreichen Lauf.


if __name__ == "__main__":
    # Führt main() nur aus, wenn main.py direkt gestartet wird (nicht bei einem Import).
    sys.exit(main())
