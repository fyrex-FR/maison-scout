"""Unit tests for app.enrichment.georisques (pure payload summarizing, no network)."""

from app.enrichment.georisques import summarize_risks


def _risk(present: bool) -> dict:
    return {"present": present}


def test_full_payload_summarized():
    payload = {
        "risquesNaturels": {
            "inondation": _risk(True),
            "retraitGonflementArgile": _risk(True),
            "feuForet": _risk(False),
            "seisme": _risk(True),
            "radon": _risk(False),
            "risqueCotier": _risk(True),
            "mouvementTerrain": _risk(False),
        },
        "risquesTechnologiques": {
            "pollutionSols": _risk(True),
        },
    }
    summary = summarize_risks(payload)
    assert summary == {
        "inondation": True,
        "argiles": True,
        "feu_foret": False,
        "seisme": True,
        "radon": False,
        "risque_cotier": True,
        "mouvement_terrain": False,
        "pollution_sols": True,
    }


def test_empty_payload_all_false():
    summary = summarize_risks({})
    assert summary == {
        "inondation": False,
        "argiles": False,
        "feu_foret": False,
        "seisme": False,
        "radon": False,
        "risque_cotier": False,
        "mouvement_terrain": False,
        "pollution_sols": False,
    }


def test_missing_keys_default_to_false():
    payload = {
        "risquesNaturels": {"inondation": _risk(True)},
        # risquesTechnologiques entirely absent
    }
    summary = summarize_risks(payload)
    assert summary["inondation"] is True
    assert summary["argiles"] is False
    assert summary["pollution_sols"] is False


def test_malformed_risk_entry_treated_as_absent():
    payload = {
        "risquesNaturels": {"inondation": "not-a-dict", "seisme": None},
    }
    summary = summarize_risks(payload)
    assert summary["inondation"] is False
    assert summary["seisme"] is False
