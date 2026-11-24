"""QA5 (M3): area names and coordinates (PO decision Q28; docs/M3-DESIGN.md §8 "Feed": area seed,
no outbound geocoding).

- The approved ward dataset (OpenCity "GBA Final Wards Map", DATA1) follows the display-name
  rules, and its source notes name the licence and the attribution.
- The seeded ``areas`` table is that dataset, not the placeholder.
- Creating a deed and reading it through the feed never opens a connection to any host other
  than the local database and storage: no external geocoder ever sees coordinates.
"""

import csv
import json
import re
import socket
from pathlib import Path
from typing import Any, ClassVar

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds.models import Area
from tests.conftest import BACKEND_DIR
from tests.deeds.helpers import deed_body, location, members, token_for

DATA = BACKEND_DIR / "app" / "modules" / "deeds" / "data"
CANDIDATE = DATA / "ward_candidate"


def _features(path: Path) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["features"]
    return features


def test_approved_ward_dataset_follows_the_display_name_rules() -> None:
    features = _features(CANDIDATE / "bangalore_areas.geojson")
    assert len(features) == 369  # the 369 GBA wards (DATA1)
    codes = {f["properties"]["ward_code"] for f in features}
    assert len(codes) == len(features)
    for f in features:
        props = f["properties"]
        name = props["name"]
        assert 1 <= len(name) <= 40, name
        assert not re.search(r"\bward\b", name, re.IGNORECASE), name
        assert not re.search(r"\d", name), name
        assert props["ward_code"] not in name
        assert props["source"].strip()
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_area_source_notes_name_the_licence_and_attribution() -> None:
    notes = (CANDIDATE / "AREAS_SOURCE.md").read_text(encoding="utf-8")
    assert re.search(r"licen[cs]e", notes, re.IGNORECASE)
    assert "Public Domain" in notes
    assert "OpenCity" in notes and "Greater Bengaluru Authority" in notes


async def test_seeded_areas_are_the_approved_ward_dataset(session: AsyncSession) -> None:
    rows = list((await session.execute(select(Area).where(Area.id < 30000))).scalars())
    assert rows
    for area in rows:
        assert area.source.strip()
        assert "placeholder" not in area.source.lower(), area.source
    assert len(rows) == len(_features(CANDIDATE / "bangalore_areas.geojson"))


def test_seed_file_is_the_approved_candidate_with_the_approved_names() -> None:
    """PO 2026-11-21: display names exactly as proposed in names.csv, all 369 rows."""
    seed = _features(DATA / "bangalore_areas.geojson")
    assert seed == _features(CANDIDATE / "bangalore_areas.geojson")
    with (CANDIDATE / "names.csv").open(encoding="utf-8", newline="") as f:
        proposed = {r["ward_code"]: r["proposed_display_name"] for r in csv.DictReader(f)}
    assert len(proposed) == 369
    assert {f["properties"]["ward_code"]: f["properties"]["name"] for f in seed} == proposed


class _NoOutbound:
    """Fails any socket connection or DNS lookup to a host that isn't local."""

    LOCAL: ClassVar[frozenset[str]] = frozenset(
        {"localhost", "127.0.0.1", "::1", "0.0.0.0", "test", "host.docker.internal"}
    )

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.attempts: list[str] = []
        real_getaddrinfo = socket.getaddrinfo
        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex
        outer = self

        def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
            outer._check(host)
            return real_getaddrinfo(host, *args, **kwargs)

        def connect(sock: socket.socket, address: Any) -> Any:
            outer._check(address[0] if isinstance(address, tuple) else address)
            return real_connect(sock, address)

        def connect_ex(sock: socket.socket, address: Any) -> Any:
            outer._check(address[0] if isinstance(address, tuple) else address)
            return real_connect_ex(sock, address)

        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
        monkeypatch.setattr(socket.socket, "connect", connect)
        monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)

    def _check(self, host: Any) -> None:
        name = host.decode() if isinstance(host, bytes) else str(host)
        if name not in self.LOCAL and not name.startswith("127."):
            self.attempts.append(name)
            raise OSError(f"outbound connection to {name} attempted in a test")


async def test_creating_and_reading_a_deed_calls_no_outside_host(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    witness, doer = await members(session, 2)
    guard = _NoOutbound(monkeypatch)
    with pytest.raises(OSError):  # the guard really is in place
        socket.getaddrinfo("geocoder.example.com", 443)
    guard.attempts.clear()
    scan = await client.post(
        "/api/scans",
        json={"token": await token_for(session, doer), "location": location(east_m=30)},
        headers=witness.headers,
    )
    assert scan.status_code == 201, scan.text
    created = await client.post(
        "/api/deeds", json=deed_body(scan.json()["scan_id"]), headers=witness.headers
    )
    assert created.status_code == 201, created.text
    deed_id = created.json()["id"]
    assert isinstance(created.json()["area_name"], str) and created.json()["area_name"]
    for path, who in (
        (f"/api/deeds/{deed_id}", witness),
        (f"/api/deeds/{deed_id}", doer),
        ("/api/me/deeds?role=reporter", witness),
    ):
        r = await client.get(path, headers=who.headers)
        assert r.status_code == 200, (path, r.text)
        assert '"lat"' not in r.text and '"lng"' not in r.text and '"location"' not in r.text
    assert guard.attempts == []
