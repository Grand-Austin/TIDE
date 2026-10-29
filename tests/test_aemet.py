import json
from pathlib import Path
import urllib.error

import pytest

from game.scenario import utc
from pipeline import aemet


def bulletin():
    return """<?xml version="1.0" encoding="ISO-8859-15"?><root><origen><productor>AEMET</productor><copyright>© AEMET</copyright><elaborado>2026-09-09T16:00:00</elaborado><inicio>2026-09-09T16:00:00</inicio><fin>2026-09-10T16:00:00</fin></origen><aviso><texto>No hay avisos</texto></aviso><prediccion><zona id="8040740" nombre="Canal de Menorca"><subzona id="8040740"><texto>Algún aguacero</texto></subzona></zona></prediccion></root>""".encode(
        "iso-8859-15"
    )


def test_bulletin_encoding_summer_timezone_and_absent_warning():
    d = aemet.parse_bulletin(bulletin())
    assert d["issued_at"] == "2026-09-09T14:00:00+00:00"
    assert d["credit"] == "© AEMET"
    assert "Algún" in d["zones"][0]["text"]
    assert "No hay avisos" in d["warnings"]
    assert aemet.local_time("2026-12-01T16:00:00") == "2026-12-01T15:00:00+00:00"


def test_archive_deduplicates_but_retains_revision(tmp_path):
    raw = bulletin()
    a = aemet.archive(raw, aemet.parse_bulletin(raw), tmp_path, ".xml")
    assert a == aemet.archive(raw, aemet.parse_bulletin(raw), tmp_path, ".xml")
    changed = raw.replace(b"aguacero", b"viento")
    b = aemet.archive(changed, aemet.parse_bulletin(changed), tmp_path, ".xml")
    assert a != b and len(list(tmp_path.glob("*.xml"))) == 2
    assert (
        json.loads(a.with_suffix(".json").read_text())["source_timezone"]
        == "Europe/Madrid"
    )


def cap(area="Menorca", onset="2026-09-09T12:00:00Z"):
    return f"""<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>x</identifier><sent>2026-09-09T10:00:00Z</sent><info><onset>{onset}</onset><expires>2026-09-10T00:00:00Z</expires><area><areaDesc>{area}</areaDesc></area><event>Oleaje</event></info></alert>""".encode()


def test_cap_filters_time_and_area():
    start, end = utc("2026-09-09T15:00:00Z"), utc("2026-09-09T18:00:00Z")
    assert len(aemet.parse_cap(cap(), start, end)) == 1
    assert aemet.parse_cap(cap("Galicia"), start, end) == []
    assert aemet.parse_cap(cap(onset="2026-09-09T20:00:00Z"), start, end) == []


def test_missing_key_and_http_failure_are_explicit(monkeypatch):
    monkeypatch.delenv("AEMET_API_KEY", raising=False)
    with pytest.raises(ValueError, match="AEMET_API_KEY"):
        aemet.warnings(
            utc("2026-09-09T00:00:00Z"), utc("2026-09-10T00:00:00Z"), Path("/tmp")
        )

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://example.invalid/?secret=secret", 403, "secret", None, None
        )

    monkeypatch.setattr(aemet.urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError) as e:
        aemet.retrieve("https://example.invalid", "secret")
    assert "secret" not in str(e.value)


def test_xml_rejects_external_entities():
    with pytest.raises(ValueError):
        aemet.xml(b"<!DOCTYPE a><a/>")


def test_actual_official_fixture():
    raw = Path("tests/fixtures/aemet/balearic.xml").read_bytes()
    d = aemet.parse_bulletin(raw)
    assert any(z["id"] == "8040740" for z in d["zones"])
    assert utc(d["valid_to"]) > utc(d["valid_from"])
    assert d["producer"] and d["credit"]
