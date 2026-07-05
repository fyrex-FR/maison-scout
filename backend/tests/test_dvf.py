"""Unit tests for app.enrichment.dvf (pure CSV parsing, no network)."""

from app.enrichment.dvf import median_house_price_per_m2

DVF_HEADER = (
    "id_mutation,date_mutation,numero_disposition,nature_mutation,valeur_fonciere,"
    "code_type_local,type_local,surface_reelle_bati,nombre_pieces_principales,"
    "surface_terrain,longitude,latitude"
)


def _row(
    id_mutation="1",
    date_mutation="2024-01-15",
    numero_disposition="1",
    nature_mutation="Vente",
    valeur_fonciere="300000",
    code_type_local="1",
    type_local="Maison",
    surface_reelle_bati="100",
    nombre_pieces_principales="4",
    surface_terrain="500",
    longitude="6.7",
    latitude="43.4",
):
    return ",".join(
        [
            id_mutation,
            date_mutation,
            numero_disposition,
            nature_mutation,
            valeur_fonciere,
            code_type_local,
            type_local,
            surface_reelle_bati,
            nombre_pieces_principales,
            surface_terrain,
            longitude,
            latitude,
        ]
    )


def _csv(*rows):
    return "\n".join([DVF_HEADER, *rows])


def test_simple_vente_maison_included():
    csv_text = _csv(_row(id_mutation="1", valeur_fonciere="300000", surface_reelle_bati="100"))
    median, count = median_house_price_per_m2([csv_text])
    assert count == 1
    assert median == 3000.0


def test_mutation_with_two_maisons_excluded():
    csv_text = _csv(
        _row(id_mutation="2", numero_disposition="1", type_local="Maison", valeur_fonciere="600000", surface_reelle_bati="100"),
        _row(id_mutation="2", numero_disposition="2", type_local="Maison", valeur_fonciere="600000", surface_reelle_bati="120"),
    )
    median, count = median_house_price_per_m2([csv_text])
    assert median is None
    assert count == 0


def test_dependance_alone_excluded():
    csv_text = _csv(
        _row(id_mutation="3", type_local="Dependance", valeur_fonciere="50000", surface_reelle_bati="")
    )
    median, count = median_house_price_per_m2([csv_text])
    assert median is None
    assert count == 0


def test_maison_plus_dependance_tolerated():
    csv_text = _csv(
        _row(id_mutation="4", numero_disposition="1", type_local="Maison", valeur_fonciere="400000", surface_reelle_bati="100"),
        _row(id_mutation="4", numero_disposition="2", type_local="Dependance", valeur_fonciere="400000", surface_reelle_bati=""),
    )
    median, count = median_house_price_per_m2([csv_text])
    assert count == 1
    assert median == 4000.0


def test_zero_valeur_fonciere_excluded():
    csv_text = _csv(_row(id_mutation="5", valeur_fonciere="0", surface_reelle_bati="100"))
    median, count = median_house_price_per_m2([csv_text])
    assert median is None
    assert count == 0


def test_zero_surface_excluded():
    csv_text = _csv(_row(id_mutation="5b", valeur_fonciere="300000", surface_reelle_bati="0"))
    median, count = median_house_price_per_m2([csv_text])
    assert median is None
    assert count == 0


def test_aberrant_price_per_m2_excluded():
    # 3,000,000 / 100 = 30,000 EUR/m2 -> above the 20,000 ceiling
    csv_text = _csv(_row(id_mutation="6", valeur_fonciere="3000000", surface_reelle_bati="100"))
    median, count = median_house_price_per_m2([csv_text])
    assert median is None
    assert count == 0


def test_non_vente_nature_mutation_excluded():
    csv_text = _csv(_row(id_mutation="7", nature_mutation="Donation", valeur_fonciere="300000", surface_reelle_bati="100"))
    median, count = median_house_price_per_m2([csv_text])
    assert median is None
    assert count == 0


def test_median_across_multiple_valid_mutations():
    csv_text = _csv(
        _row(id_mutation="8", valeur_fonciere="200000", surface_reelle_bati="100"),  # 2000 EUR/m2
        _row(id_mutation="9", valeur_fonciere="300000", surface_reelle_bati="100"),  # 3000 EUR/m2
        _row(id_mutation="10", valeur_fonciere="400000", surface_reelle_bati="100"),  # 4000 EUR/m2
    )
    median, count = median_house_price_per_m2([csv_text])
    assert count == 3
    assert median == 3000.0


def test_empty_input_returns_none():
    median, count = median_house_price_per_m2([])
    assert median is None
    assert count == 0


def test_multiple_csv_texts_pooled():
    csv_2024 = _csv(_row(id_mutation="1", valeur_fonciere="200000", surface_reelle_bati="100"))
    csv_2025 = _csv(_row(id_mutation="1", valeur_fonciere="300000", surface_reelle_bati="100"))
    median, count = median_house_price_per_m2([csv_2024, csv_2025])
    # Same id_mutation="1" in both files must be treated as two distinct
    # mutations (ids are only unique within a single year's file).
    assert count == 2
