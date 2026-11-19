# Projektsteckbrief — Analyse des dm-„Neu“-Sortiments (A1)

- **Name:** KD 
- **Datum:** <19.11.2026>
- **Datenquelle (URL):** `https://product-search.services.dmtech.com/de/search/crawl?isNew=true&popularFacet=Neu&pageSize=1000&searchType=editorial-search&sort=new&type=search-static¤tPage=0&enablePharmacy=true`
- **Download-Datum & Umfang:** Abruf am 19.11.2026, Teil-Extrakt der ersten Seite (`pageSize=1000`, `currentPage=0`, Sortiment „Neu“ inkl. Apotheken-Sortiment).
- **Datenschutz / DSGVO:** Der Endpunkt liefert ausschließlich öffentliche Produkt- und Stammdaten ohne Personenbezug. Lokale Rohdateien (`data/raw/*.json`) und Protokolle (`dq_reject_log`) dienen ausschließlich der Nachvollziehbarkeit. 

## Ziel & Leitfrage
- **Ziel:** Aufbau einer automatisierten, idempotenten Datenstrecke von der JSON-Schnittstelle bis in ein DuckDB-Sternschema inklusive automatisierter Qualitätsprüfungen, SQL-Auswertungen und Matplotlib-Visualisierungen.
- **Leitfrage:** *Wie verteilen sich Produktanzahl und Preisniveau im aktuellen dm-„Neu“-Sortiment über die wichtigsten Marken und Hauptkategorien, und wie unterscheiden sich dm-Eigenmarken preislich von Herstellermarken?*

-Welche Kategorien (dim_category) haben die meisten Neuheiten?
-Welche Marken (dim_brand) sind im Schnitt teurer oder günstiger – und wie verhalten sich dm-Eigenmarken im Vergleich zu Herstellermarken?

## Sternschema & SCD-Entscheidungen (A3)

```text
                 +---------------------------+
                 |       dim_brand (SCD2)    |
                 +---------------------------+
                 | PK brand_sk               |
                 |    brand_key              |
                 |    brand_name             |
                 |    brand_segment          |
                 |    valid_from / valid_to  |
                 |    is_current             |
                 +-------------+-------------+
                               | 1
                               |
                               | n
+------------------------+     |     +---------------------------+
|  dim_category (SCD1)   |1   n|     |  fact_product_listings    |
+------------------------+-----+-----+---------------------------+
| PK category_sk         |           | PK (gtin, snapshot_date)  |
|    category_key        |           | FK brand_sk               |
|    top_category        |           | FK category_sk            |
|    sub_category        |           |    product_title          |
|    updated_at          |           |    price_eur              |
+------------------------+           |    price_tier             |
                                     |    is_new / rating_value  |
                                     +---------------------------+
