"""Archive AEMET official bulletins and historical CAP warnings.

python -m pipeline.aemet bulletin
AEMET_API_KEY=... python -m pipeline.aemet warnings --start 2026-09-09T00:00:00Z --end 2026-09-10T00:00:00Z
"""

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import urllib.request
import urllib.parse
from xml.etree import ElementTree as ET
from zipfile import ZipFile, is_zipfile
from zoneinfo import ZoneInfo

from game.scenario import utc
from pipeline.downloads import retry, save_json

BULLETIN = "https://www.aemet.es/xml/maritima/FQXX44MM.xml"
OUT = Path("data/raw/aemet")


def retrieve(url, key=None):
    headers = {"User-Agent": "TIDE educational scenario collector"}
    if key:
        headers["api_key"] = key

    def read():
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=30
        ) as response:
            return response.read()

    try:
        return retry(read)
    except Exception as error:
        # HTTP exceptions can contain credential-bearing URLs. Do not propagate their text.
        raise RuntimeError(f"AEMET request failed ({type(error).__name__})") from None


def xml(raw):
    declaration = re.search(rb"encoding=[\"\x27]([^\"\x27]+)", raw[:200])
    encoding = declaration[1].decode("ascii") if declaration else "utf-8"
    text = raw.decode(encoding)
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("External XML declarations are not accepted")
    return ET.fromstring(text)


def local_time(value):
    t = datetime.fromisoformat(value)
    if t.tzinfo is None:
        t = t.replace(tzinfo=ZoneInfo("Europe/Madrid"))
    return t.astimezone(timezone.utc).isoformat()


def parse_bulletin(raw):
    root = xml(raw)
    origin = root.find("origen")
    if origin is None or root.find("prediccion") is None:
        raise ValueError("Missing bulletin origin or forecast")
    times = {k: origin.findtext(k) for k in ("elaborado", "inicio", "fin")}
    normalized = {k: local_time(v) for k, v in times.items()}
    if utc(normalized["fin"]) <= utc(normalized["inicio"]):
        raise ValueError("Invalid bulletin validity")
    zones = [
        {
            "id": z.get("id"),
            "name": z.get("nombre"),
            "text": " ".join(z.itertext()).strip(),
        }
        for z in root.findall(".//zona") + root.findall(".//subzona")
    ]
    return {
        "kind": "official_forecast",
        "source_url": BULLETIN,
        "producer": origin.findtext("productor"),
        "credit": origin.findtext("copyright"),
        "source_timezone": "Europe/Madrid",
        "original_times": times,
        "issued_at": normalized["elaborado"],
        "valid_from": normalized["inicio"],
        "valid_to": normalized["fin"],
        "zones": zones,
        "warnings": " ".join(root.find("aviso").itertext()).strip()
        if root.find("aviso") is not None
        else None,
        "sections": {
            name: " ".join(root.find(name).itertext()).strip()
            for name in ("situacion", "tendencia")
            if root.find(name) is not None
        },
    }


def archive(raw, record, out, suffix):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    checksum = hashlib.sha256(raw).hexdigest()
    stamp = record.get("issued_at") or "historical"
    stem = re.sub(r"[^0-9A-Za-z-]", "", stamp) + "-" + checksum
    path = out / (stem + suffix)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise ValueError("Corrupt immutable AEMET snapshot")
    if not path.exists():
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(raw)
        temp.replace(path)
    if not (out / (stem + ".json")).exists():
        save_json(
            out / (stem + ".json"),
            dict(
                record,
                sha256=checksum,
                raw_file=path.name,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
            ),
        )
    return path


def parse_cap(raw, start, end):
    root = xml(raw)
    ns = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}
    if root.tag != "{urn:oasis:names:tc:emergency:cap:1.2}alert":
        raise ValueError("Unexpected CAP document")
    result = []
    for info in root.findall("c:info", ns):
        begin = (
            info.findtext("c:onset", namespaces=ns)
            or info.findtext("c:effective", namespaces=ns)
            or root.findtext("c:sent", namespaces=ns)
        )
        finish = info.findtext("c:expires", namespaces=ns)
        areas = [
            {
                "description": a.findtext("c:areaDesc", namespaces=ns),
                "geocodes": [v.text for v in a.findall("c:geocode/c:value", ns)],
                "polygon": a.findtext("c:polygon", namespaces=ns),
            }
            for a in info.findall("c:area", ns)
        ]
        applicable = any(
            "bale" in (a["description"] or "").lower()
            or any(
                x in (a["description"] or "").lower()
                for x in ("mallorca", "menorca", "ibiza", "formentera")
            )
            or any((v or "").startswith(("807", "8040")) for v in a["geocodes"])
            for a in areas
        )
        if applicable and begin and finish and utc(begin) < end and utc(finish) > start:
            result.append(
                {
                    "identifier": root.findtext("c:identifier", namespaces=ns),
                    "sender": root.findtext("c:sender", namespaces=ns),
                    "issued_at": utc(root.findtext("c:sent", namespaces=ns)).isoformat()
                    if root.findtext("c:sent", namespaces=ns)
                    else None,
                    "valid_from": utc(begin).isoformat(),
                    "valid_to": utc(finish).isoformat(),
                    "areas": areas,
                    "event": info.findtext("c:event", namespaces=ns),
                    "severity": info.findtext("c:severity", namespaces=ns),
                    "text": " ".join(info.itertext()).strip(),
                }
            )
    return result


def warnings(start, end, out):
    if end <= start:
        raise ValueError("Warning end must follow start")
    key = os.environ.get("AEMET_API_KEY")
    if not key:
        raise ValueError("Set AEMET_API_KEY to fetch historical warnings")
    fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SUTC")
    url = f"https://opendata.aemet.es/opendata/api/avisos_cap/archivo/fechaini/{fmt(start)}/fechafin/{fmt(end)}"
    response = json.loads(retrieve(url, key))
    status = response.get("estado")
    if status == 404:
        record = {
            "kind": "official_warning_archive",
            "status": "no_warnings",
            "warnings": [],
            "source_url": url,
        }
        record.update(query_start=start.isoformat(), query_end=end.isoformat())
        archive(json.dumps(response).encode(), record, out, ".response")
        return record
    if status != 200 or not response.get("datos"):
        raise RuntimeError("AEMET warning archive returned an unsuccessful response")
    data_url = response["datos"]
    if urllib.parse.urlparse(data_url).hostname not in (
        "opendata.aemet.es",
        "www.aemet.es",
    ):
        raise ValueError("Unexpected AEMET data host")
    raw = retrieve(data_url)
    if is_zipfile(BytesIO(raw)):
        with ZipFile(BytesIO(raw)) as z:
            if sum(i.file_size for i in z.infolist()) > 50_000_000:
                raise ValueError("CAP archive exceeds processing size limit")
            documents = [
                z.read(i)
                for i in z.infolist()
                if i.filename.lower().endswith((".xml", ".cap"))
            ]
    else:
        documents = [raw]
    alerts = [a for document in documents for a in parse_cap(document, start, end)]
    record = {
        "kind": "official_warning_archive",
        "status": "warnings" if alerts else "no_matching_warnings",
        "source_url": url,
        "query_start": start.isoformat(),
        "query_end": end.isoformat(),
        "warnings": alerts,
    }
    archive(raw, record, out, ".zip" if is_zipfile(BytesIO(raw)) else ".xml")
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["bulletin", "warnings"])
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--start", type=utc)
    p.add_argument("--end", type=utc)
    a = p.parse_args()
    try:
        if a.command == "bulletin":
            raw = retrieve(BULLETIN)
            path = archive(raw, parse_bulletin(raw), a.out, ".xml")
            print(path)
        else:
            if a.start is None or a.end is None:
                p.error("warnings requires --start and --end")
            record = warnings(a.start, a.end, a.out)
            print(record["status"])
        save_json(
            a.out / "status.json",
            {
                "status": "success",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as error:
        save_json(a.out / "status.json", {"status": "failed", "error": str(error)})
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
