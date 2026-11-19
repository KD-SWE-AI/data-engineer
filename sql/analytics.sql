-- 1. Join über Faktentabelle und beide Dimensionen (Marke + Kategorie) für den aktuellen Stichtag
-- A4: Verbindet Fakten mit beiden Dimensionen und aggregiert Preis-Kennzahlen.
SELECT
    b.brand_name,
    -- Zeigt den Markennamen aus der Dimension dim_brand (statt nur eines technischen Surrogate Keys).
    b.brand_segment,
    -- Zeigt die Markenzuordnung (dm-Eigenmarke vs. Herstellermarke) für den Segmentvergleich.
    c.top_category,
    -- Zeigt die Hauptkategorie aus der Dimension dim_category.
    COUNT(*) AS produkt_anzahl,
    -- Zählt alle gültigen Neuheiten-Einträge pro Marke/Segment/Hauptkategorie-Kombination.
    ROUND(AVG(f.price_eur), 2) AS durchschnittspreis_eur,
    -- Berechnet den Durchschnittspreis in EUR und rundet kaufmännisch auf 2 Nachkommastellen.
    ROUND(MIN(f.price_eur), 2) AS min_preis_eur,
    -- Zeigt den günstigsten Preis in der jeweiligen Gruppe als Spannweiten-Indikator.
    ROUND(MAX(f.price_eur), 2) AS max_preis_eur
    -- Zeigt den höchsten Preis in der Gruppe, um Ausreißer/Premium-Artikel sichtbar zu machen.
FROM fact_product_listings f
-- Startet bei der Faktentabelle mit Alias 'f' (trägt Keys und numerische Messwerte).
JOIN dim_brand b ON f.brand_sk = b.brand_sk
-- Verknüpft die Fakten über den Surrogate Key brand_sk mit der Markendimension 'b'.
JOIN dim_category c ON f.category_sk = c.category_sk
-- Verknüpft die Fakten über den Surrogate Key category_sk mit der Kategoriedimension 'c'.
WHERE f.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_product_listings)
-- Filtert auf den neuesten geladenen Stichtag, damit ältere Snapshots nicht vermischt werden.
GROUP BY b.brand_name, b.brand_segment, c.top_category
-- Gruppiert nach allen nicht-aggregierten Ausgabespalten (Pflicht bei SQL-Aggregationen).
ORDER BY produkt_anzahl DESC, durchschnittspreis_eur DESC;
-- Sortiert nach höchster Produktanzahl zuerst, bei Gleichstand nach höherem Durchschnittspreis.

-- 2. Aggregation mit Filter (HAVING): Marken mit mindestens 3 Neuheiten und deren Preisniveau
-- A4: Demonstriert den Unterschied zwischen WHERE (vor Aggregation) und HAVING (nach Aggregation).
SELECT
    b.brand_name,
    -- Markennamen aus dim_brand ausgeben.
    b.brand_segment,
    -- Markensegment (Eigenmarke vs. Herstellermarke) mitsamt ausgeben.
    COUNT(*) AS anzahl_neuheiten,
    -- Ermittelt die Anzahl der Neuheiten je Marke am aktuellen Stichtag.
    ROUND(AVG(f.price_eur), 2) AS avg_preis_eur
    -- Berechnet den durchschnittlichen Bruttopreis der Marke in EUR.
FROM fact_product_listings f
-- Faktentabelle als Basis für die Preis- und Stückzahlmessung.
JOIN dim_brand b ON f.brand_sk = b.brand_sk
-- Join auf dim_brand für Namen und Segment.
WHERE f.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_product_listings)
-- Beschränkt die Rohzeilen vor der Gruppierung auf den aktuellsten Stichtag.
GROUP BY b.brand_name, b.brand_segment
-- Gruppiert pro Marke (und Segment), um Kennzahlen je Marke zu berechnen.
HAVING COUNT(*) >= 3
-- Filtert *nach* der Aggregation: Nur Marken mit mindestens 3 Produkten bleiben erhalten (verhindert Zufallswerte bei Einzelprodukten).
ORDER BY avg_preis_eur DESC;
-- Sortiert die verbleibenden Marken absteigend nach Durchschnittspreis (teuerste Marken oben).

-- 3. Rangbasierte Auswertung (Top-10 teuerste Neuheiten mit ORDER BY und LIMIT)
-- A4: Klassische Top-N-Auswertung über Sortierung und Begrenzung.
SELECT
    f.gtin,
    -- Eindeutige Produktkennung (GTIN) zur Nachvollziehbarkeit des Artikels.
    f.product_title,
    -- Produktbezeichnung aus der Faktentabelle.
    b.brand_name,
    -- Zugehörige Marke aus dim_brand.
    c.top_category,
    -- Zugehörige Hauptkategorie aus dim_category.
    f.price_eur
    -- Bruttopreis in EUR als Sortierkriterium.
FROM fact_product_listings f
-- Faktentabelle mit Produktzeilen zum Stichtag.
JOIN dim_brand b ON f.brand_sk = b.brand_sk
-- Join auf dim_brand für den Markennamen.
JOIN dim_category c ON f.category_sk = c.category_sk
-- Join auf dim_category für die Hauptkategorie.
WHERE f.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_product_listings)
-- Nur Datensätze des aktuellen Stichtags betrachten.
ORDER BY f.price_eur DESC, f.product_title ASC
-- Sortiert zuerst nach höchstem Preis absteigend, bei Preisgleichstand alphabetisch nach Titel.
LIMIT 10;
-- Begrenzt das Ergebnis auf die Top 10 Einträge.

-- 4. Segmentvergleich: dm-Eigenmarken vs. Herstellermarken über Preisklassen hinweg
-- Zusatzabfrage für eine zweite fachliche Perspektive (Preisklassen-Struktur nach Segment).
SELECT
    b.brand_segment,
    -- Unterscheidet dm-Eigenmarken von externen Herstellermarken.
    f.price_tier,
    -- Regelbasiert abgeleitete Preisklasse ('Günstig (< 5 €)', 'Mittel (5–15 €)', 'Premium (> 15 €)').
    COUNT(*) AS anzahl_produkte,
    -- Zählt Produkte je Segment und Preisklasse.
    ROUND(AVG(f.price_eur), 2) AS avg_preis_eur
    -- Zeigt den durchschnittlichen Preis innerhalb der jeweiligen Segment-Preisklassen-Kombination.
FROM fact_product_listings f
-- Faktentabelle mit price_tier und price_eur.
JOIN dim_brand b ON f.brand_sk = b.brand_sk
-- Join auf dim_brand für brand_segment.
WHERE f.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_product_listings)
-- Filter auf den letzten Stichtag.
GROUP BY b.brand_segment, f.price_tier
-- Gruppiert nach Segment und Preisklasse.
ORDER BY b.brand_segment, avg_preis_eur;
-- Sortiert sauber nach Segment und aufsteigendem Durchschnittspreis.

-- 5. Bonus mit Fensterfunktion (DENSE_RANK & Anteil je Hauptkategorie)
-- A4: Ranglogik über eine Fensterfunktion statt nur LIMIT.
WITH ranked_products AS (
    -- CTE (Common Table Expression) berechnet zuerst den Rang innerhalb jeder Hauptkategorie.
    SELECT
        c.top_category,
        -- Hauptkategorie als Partitionierungsrahmen (Fenster).
        b.brand_name,
        -- Markenname des Produkts.
        f.product_title,
        -- Titel des Produkts.
        f.price_eur,
        -- Preis des Produkts als Ranking-Kriterium.
        DENSE_RANK() OVER (
            -- Fensterfunktion: vergibt lückenlose Ränge (1, 2, 3...) bei Preisgleichstand.
            PARTITION BY c.top_category
            -- Startet die Rangzählung für jede Hauptkategorie neu (pro Kategorie Top-Preise).
            ORDER BY f.price_eur DESC
            -- Höchster Preis innerhalb der Kategorie erhält Rang 1.
        ) AS preis_rang_in_kategorie,
        COUNT(*) OVER (PARTITION BY c.top_category) AS produkte_in_kategorie
        -- Fenster-Aggregat: Gesamtzahl der Produkte in dieser Hauptkategorie, ohne Zeilen zu verdichten.
    FROM fact_product_listings f
    -- Faktentabelle als Datenbasis.
    JOIN dim_brand b ON f.brand_sk = b.brand_sk
    -- Join auf dim_brand für den Markennamen.
    JOIN dim_category c ON f.category_sk = c.category_sk
    -- Join auf dim_category für die Hauptkategorie.
    WHERE f.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_product_listings)
    -- Filter auf den aktuellsten Stichtag.
)
SELECT
    top_category,
    -- Gibt die Hauptkategorie aus.
    preis_rang_in_kategorie,
    -- Gibt den berechneten Preisrang innerhalb der Kategorie aus.
    brand_name,
    -- Gibt den Markennamen aus.
    product_title,
    -- Gibt den Produkttitel aus.
    price_eur,
    -- Gibt den Preis in EUR aus.
    produkte_in_kategorie
    -- Zeigt zum Vergleich, wie viele Produkte insgesamt in dieser Kategorie liegen.
FROM ranked_products
-- Liest aus der zuvor definierten CTE.
WHERE preis_rang_in_kategorie <= 3
-- Filtert auf die Top-3-Preisränge je Hauptkategorie (nachdem die Fensterfunktion berechnet wurde).
ORDER BY top_category, preis_rang_in_kategorie, price_eur DESC;
-- Sortiert übersichtlich nach Kategorie, Rang und Preis.
