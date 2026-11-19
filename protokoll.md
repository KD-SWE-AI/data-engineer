# Nachweis & Laufprotokoll (A6)

**Projekt:** Automatisierte Datenstrecke dm-„Neu“-Sortiment  
**Datum:** 19.11.2026  
**Stichtag (`snapshot_date`):** `<YYYY-MM-DD>`  
**Quelle:** `data/raw/dm_neu_<YYYY-MM-DD>.json`

---

## 1. Kontrollzahlen je Datenqualitätsprüfung (A2)

| Prüfung | Geprüft | Bestanden | Abgelehnt | Regel / Maßnahme bei Abweichung |
|---|---:|---:|---:|---|
| `1_pflichtfelder` | `<N>` | `<N>` | `<N>` | `gtin`, `title`, `brand` und `price` müssen vorhanden sein; Ablehnung mit Grund in `dq_reject_log` |
| `2_format_gtin_dan` | `<N>` | `<N>` | `<N>` | GTIN aus 8–14 Ziffern erforderlich; ungültige Formate werden in `dq_reject_log` protokolliert |
| `3_wertebereich_preis` | `<N>` | `<N>` | `<N>` | Bruttopreis muss `0.00 < price_eur <= 500.00` erfüllen; Ausreißer in `dq_reject_log` |
| `4_duplikat_gtin` | `<N>` | `<N>` | `<N>` | Doppelte GTINs innerhalb desselben Rohdaten-Batches werden verworfen und protokolliert |

**Regelbasierte Korrekturen (ohne Handarbeit):** Fehlende Kategorien werden automatisch als `"Unbekannt"` gesetzt, fehlende Nettopreise über `price_eur / 1.19` abgeleitet, Mehrfachleerzeichen normalisiert und `price_tier` (`Günstig (< 5 €)`, `Mittel (5–15 €)`, `Premium (> 15 €)`) regelbasiert bestimmt.

---

## 2. Zeilenzahlen je Ladeschritt (A3 / A5)

| Schritt / Tabelle | Zeilenzahl / Ergebnis | Verhalten bei Wiederholung (`python main.py`) |
|---|---:|---|
| Rohdatensätze gelesen (`products`) | `<N>` | Identisch bei gleicher Rohdatei |
| Valide Datensätze nach DQ | `<N>` | Identisch bei gleicher Rohdatei |
| Abgelehnte Datensätze (`dq_reject_log`) | `<N>` | Einträge derselben `run_id` werden ersetzt (idempotent) |
| `dim_brand` (SCD Typ 2 — neue/geänderte Versionen) | `<N>` | Neue Versionen nur bei Attributänderung (sonst `0` neue Zeilen) |
| `dim_category` (SCD Typ 1 — Inserts/Updates) | `<N>` | Überschreibt Änderungen direkt; keine doppelten `category_key` |
| `fact_product_listings` (Gesamtbestand) | `<N>` | `DELETE` + `INSERT` je `snapshot_date` (`PK: gtin, snapshot_date`) verhindert Duplikate |

---

## 3. Erzeugte Diagramme (A5)

- **`outputs/chart1_top_brands_avg_price.png`** — Top-10 Marken im dm-„Neu“-Sortiment nach Produktanzahl inklusive Durchschnittspreis (`€`).
- **`outputs/chart2_category_distribution.png`** — Verteilung der Neuheiten nach Hauptkategorie (Top 10).
- **`outputs/chart3_price_tiers.png`** — Vergleich der Preisklassen (`Günstig (< 5 €)`, `Mittel (5–15 €)`, `Premium (> 15 €)`) zwischen `dm-Eigenmarke` und `Herstellermarke`.

---

## 4. „Was würde passieren, wenn meine Quelle plötzlich zweimal so groß wäre?“

Wenn meine Quelle plötzlich zweimal so groß wäre (z. B. rund 2.000 statt 1.000 Produkte bzw. einige Megabyte statt wenige Megabyte JSON), würde die Pipeline auf der Kurs-VM (4 vCPU, 16 GB RAM) mit Python, DuckDB und Matplotlib weiterhin in rund ein bis zwei Sekunden durchlaufen. DuckDB arbeitet spaltenorientiert und nutzt den verfügbaren Speicher effizient, sodass die doppelte Datenmenge weder RAM noch CPU spürbar auslastet. **Lokal / mit Standardbibliothek und DuckDB** reicht es immer dann, wenn die Daten im Bereich von Megabyte bis in den niedrigen einstelligen Gigabyte-Bereich liegen, ein einzelner Rechner ausreicht und der Lauf in Sekunden bis wenigen Minuten abgeschlossen ist. **Eine größere Plattform (z. B. Airflow für Abhängigkeiten, Wiederanlauf und SLAs, PostgreSQL für Mehrbenutzer-/Transaktionsbetrieb oder PySpark für verteilte Verarbeitung)** ist erst dann gerechtfertigt, wenn die Datenmenge deutlich wächst (z. B. mehrjährige tägliche Historien, große Rohdatenbestände im zweistelligen Gigabyte-Bereich oder mehr), viele parallele/abhängige Tasks koordiniert werden müssen oder Berechnungen auf mehrere Knoten verteilt werden müssen.
