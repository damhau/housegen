"""Listing photo sets (#91): the shot list, the photo service client, the job and its routes; the exteriors (#93)."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from housegen.core import db
from housegen.core.config import Settings, get_settings
from housegen.core.db import session_factory
from housegen.core.exceptions import LLMError
from housegen.geo import context as geo_context
from housegen.listing import job as listing_job
from housegen.listing.client import PhotoClient, PhotoServiceError
from housegen.listing.pick import parse_choice, parse_exteriors
from housegen.listing.shots import exterior_caption, language, pick, room_name_parts, unit_of
from housegen.llm import Completion, ImagePart, Message, Usage
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage

CAM = "1,1.3,1>2,1,2@58"
VIEWS = [CAM, "3,1.3,1>2,1,2@58", "1,1.3,3>2,1,2@58"]
PIECE = {
    "id": "vase-1",
    "name": "vase",
    "share": 0.02,
    "x": [0.1, 0.2],
    "y": [0.5, 0.7],
    "cut_by": ["left"],
    "distance_m": 1.2,
}

# TestVillaGille's habitable rooms as its scene page reports them (use, name, m²)
GILLE = [
    ("kitchen", "Cuisine / Séjour — Appartement 1", 27.0),
    ("living", "Salon — Appartement 1", 19.1),
    ("wc", "WC — Appartement 1", 2.2),
    ("bath", "SDB — Appartement 1", 4.5),
    ("bedroom", "Chambre 1 — Appartement 1", 14.8),
    ("hall", "Hall — Appartement 1", 5.9),
    ("kitchen", "Cuisine — Appartement 2", 13.6),
    ("dining", "Séjour — Appartement 2", 7.2),
    ("living", "Salon — Appartement 2", 15.4),
    ("bedroom", "Chambre 2 — Appartement 1", 10.2),
    ("bedroom", "Chambre 3 — Appartement 1", 9.3),
    ("bedroom", "Chambre 4 — Appartement 1", 13.2),
    ("bath", "SDB étage — Appartement 1", 4.7),
    ("bedroom", "Chambre 1 — Appartement 2", 15.3),
    ("bedroom", "Chambre 2 — Appartement 2", 20.7),
    ("bedroom", "Chambre 3 — Appartement 2", 15.5),
    ("bedroom", "Chambre 4 — Appartement 2", 15.7),
    ("bath", "SDB ouest et rangement sous pente — Appartement 2", 6.0),
    ("bath", "SDB est — Appartement 2", 5.5),
    ("storage", "Garage — Appartement 1", 14.0),
    ("storage", "Local technique PAC — Commun sous-sol", 12.8),
]
MONTELLY = [
    ("office", "Garage (transformation) / Atelier", 26.5),
    ("living", "Véranda existante", 11.2),
    ("kitchen", "Cuisine", 8.7),
    ("bath", "Douche", 3.4),
    ("dining", "Salle à Manger", 22.3),
    ("living", "Salon", 21.1),
    ("bedroom", "Ch.3", 8.1),
    ("bath", "Bain", 7.6),
    ("bedroom", "Ch.1", 23.1),
]


def _rooms(rows: list[tuple[str, str, float]]) -> list[dict[str, Any]]:
    return [{"use": u, "name": n, "area": a, "camera": CAM} for u, n, a in rows]


def test_room_names_split_like_the_viewer() -> None:
    assert room_name_parts("Chambre 2 — Appartement 1, étage") == (
        "Chambre 2",
        "Appartement 1, étage",
    )
    assert room_name_parts("App. 1 — Chambre 2") == ("Chambre 2", "App. 1")
    assert room_name_parts("Salon") == ("Salon", None)
    assert unit_of("Chambre 2 — Appartement 1, étage") == "Appartement 1"
    assert unit_of("Local technique PAC — Commun sous-sol") is None
    assert unit_of("Salon") is None


def test_gille_set_is_each_flat_in_listing_order() -> None:
    shots = pick(_rooms(GILLE))
    assert [(s.unit, s.title) for s in shots] == [
        ("Appartement 1", "Salon"),
        ("Appartement 1", "Cuisine / Séjour"),
        ("Appartement 1", "Chambre 1"),
        ("Appartement 1", "SDB étage"),
        ("Appartement 1", "Chambre 2"),
        ("Appartement 1", "Chambre 3"),
        ("Appartement 1", "Chambre 4"),
        ("Appartement 2", "Salon"),
        ("Appartement 2", "Séjour"),
        ("Appartement 2", "Chambre 2"),
        ("Appartement 2", "SDB ouest et rangement sous pente"),
        ("Appartement 2", "Chambre 1"),
        ("Appartement 2", "Chambre 3"),
        ("Appartement 2", "Chambre 4"),
    ]
    assert [s.key for s in shots[:3]] == ["01", "02", "03"]
    assert shots[0].caption == "Salon, 19 m²"


def test_a_bathroom_whose_best_camera_faces_a_wall_gives_way_to_the_next() -> None:
    rooms = _rooms(GILLE)
    for r in rooms:
        r["score"] = -0.48 if r["name"].startswith("SDB étage") else 0.05
    titles = [s.title for s in pick(rooms) if s.unit == "Appartement 1"]
    assert "SDB étage" not in titles
    assert titles[3] == "SDB"


def test_montelly_one_house_skips_small_and_unlisted_rooms() -> None:
    shots = pick(_rooms(MONTELLY))
    assert [s.title for s in shots] == ["Salon", "Salle à Manger", "Ch.1", "Bain", "Ch.3"]
    assert {s.unit for s in shots} == {None}
    small = [{"use": "bedroom", "name": "Ch.4", "area": 6.0, "camera": CAM}]
    assert pick(small) == []
    assert pick([{"use": "bedroom", "name": "Ch.5", "area": 12.0, "camera": None}]) == []


def test_the_photo_service_sits_next_to_the_render_service() -> None:
    s = Settings(
        RENDER_SERVICE_URL="https://ws--housegen-render-web.modal.run/", RENDER_SERVICE_TOKEN="t"
    )
    assert s.photo_service_url == "https://ws--housegen-photo-web.modal.run"
    assert s.photo_service_token == "t"
    assert Settings(RENDER_SERVICE_URL="http://gpu:8000").photo_service_url == ""
    assert (
        Settings(PHOTO_SERVICE_URL="http://photo/", PHOTO_SERVICE_TOKEN="p").photo_service_url
        == "http://photo"
    )


class FakeService:
    """deploy/modal_photo.py's web API: calls finish on their second poll."""

    def __init__(
        self,
        rooms: list[dict[str, Any]],
        fail: set[str] | None = None,
        exteriors: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rooms = rooms
        self.exteriors = exteriors or []
        self.fail = fail or set()
        self.calls: dict[str, dict[str, Any]] = {}
        self.polls: dict[str, int] = {}
        self.prepared: list[str] = []
        self.auth: set[str] = set()
        self.cancelled: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.auth.add(request.headers.get("authorization", ""))
        path = request.url.path
        if path.endswith("/cancel"):
            self.cancelled.append(path.split("/")[2])
            return httpx.Response(200, json={"cancelled": path.split("/")[2]})
        if request.method == "POST":
            body = json.loads(request.content)
            cid = f"c{len(self.calls)}"
            self.calls[cid] = {"path": path, **body}
            if path == "/prepare":
                self.prepared.append(body["key"])
            return httpx.Response(200, json={"call_id": cid})
        cid = path.rsplit("/", 1)[1]
        self.polls[cid] = self.polls.get(cid, 0) + 1
        if self.polls[cid] < 2:
            return httpx.Response(200, json={"status": "running"})
        call = self.calls[cid]
        if call["path"] == "/prepare":
            result = {"rooms": self.rooms, "exteriors": self.exteriors, "seconds": 60}
            return httpx.Response(200, json={"status": "done", "result": result})
        if call["camera"] in self.fail:
            return httpx.Response(200, json={"status": "failed", "error": "CUDA out of memory"})
        jpeg = base64.b64encode(f"jpeg {call['camera']}".encode()).decode()
        meta = {
            "camera": call["camera"] + "!",
            "sun": {"azimuth": 200},
            "sun_deg": [200.0, 30.0],
            "room_lamps": False,
            "pieces": [PIECE],
            "removed": call.get("remove") or [],
        }
        return httpx.Response(
            200,
            json={
                "status": "done",
                "result": {"jpeg": jpeg, "meta": meta, "gpu": "RTX-PRO-6000", "seconds": 100},
            },
        )


async def test_client_polls_until_done_and_reports_failures() -> None:
    svc = FakeService(_rooms(MONTELLY), fail={"bad"})
    s = Settings(PHOTO_SERVICE_URL="http://photo", PHOTO_SERVICE_TOKEN="tok")
    c = PhotoClient(s, transport=httpx.MockTransport(svc.handler))
    c.poll_s = 0
    prepared = await c.prepare("http://app/scenes/p/versions/1/index.html", "k")
    assert len(prepared["rooms"]) == len(MONTELLY)
    jpeg, res = await c.shoot("k", CAM, 640, 360, 16, timeout_s=10)
    assert jpeg == f"jpeg {CAM}".encode()
    assert res["meta"]["camera"] == CAM + "!"
    with pytest.raises(PhotoServiceError, match="out of memory"):
        await c.shoot("k", "bad", 640, 360, 16, timeout_s=10)
    assert svc.cancelled == []
    with pytest.raises(PhotoServiceError, match="more than"):
        await c.shoot("k", CAM, 640, 360, 16, timeout_s=-1)
    assert svc.cancelled == ["c3"]
    await c.close()
    assert svc.auth == {"Bearer tok"}


@pytest.fixture
async def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTO_SERVICE_URL", "http://photo")
    monkeypatch.setenv("LISTING_PARALLEL", "2")
    get_settings.cache_clear()
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


def _fake_service(monkeypatch: pytest.MonkeyPatch, svc: FakeService) -> None:
    def client(settings: Settings | None = None) -> PhotoClient:
        c = PhotoClient(settings, transport=httpx.MockTransport(svc.handler))
        c.poll_s = 0
        return c

    monkeypatch.setattr(listing_job, "PhotoClient", client)


async def _furnished_project(versions: int = 1) -> str:
    async with session_factory()() as s, s.begin():
        project = await crud.create_project(s, "Montelly")
        st = ProjectStorage(project.id)
        st.ensure()
        for n in range(1, versions + 1):
            await crud.add_version(s, project.id, n, "generation", f"v{n}", "built")
            src = st.versions_dir / str(n) / "src"
            src.mkdir(parents=True)
            (src / "scene.js").write_text("import { floorPlan } from 'housekit/interior';\n")
        project.current_version = versions
        return project.id


async def _wait_job(job_id: str) -> str:
    for _ in range(500):
        async with session_factory()() as s:
            j = await crud.get_job(s, job_id)
        if j.status in ("done", "failed"):
            return j.status
        await asyncio.sleep(0.01)
    raise AssertionError("the listing job never ended")


async def test_listing_job_shoots_the_set_and_serves_it(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = FakeService(_rooms(MONTELLY))
    _fake_service(monkeypatch, svc)
    pid = await _furnished_project(versions=2)
    r = await app_client.post(f"/api/v1/projects/{pid}/listing", json={"version": 1})
    assert r.status_code == 202, r.text
    assert await _wait_job(r.json()["id"]) == "done"
    assert svc.prepared == [f"{pid}-v1-{listing_job.viewer_kit()}-{listing_job.PREPARE_REV}"]

    got = (await app_client.get(f"/api/v1/projects/{pid}/listing")).json()
    assert got["available"] is True
    assert got["sets"] == [1]
    ls = got["listing"]
    assert ls["status"] == "done"
    assert ls["out_of_date"] is True  # version 2 is newer
    assert [p["caption"] for p in ls["photos"]][:2] == ["Salon, 21 m²", "Salle à Manger, 22 m²"]
    assert all(p["url"] for p in ls["photos"])
    assert ls["cost_usd"] > 0
    doc = json.loads((ProjectStorage(pid).versions_dir / "1" / "listing" / "set.json").read_text())
    assert doc["photos"]["01"]["camera"] == CAM + "!"

    z = await app_client.get(f"/api/v1/projects/{pid}/listing/1/zip")
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert names[0] == "01 Salon, 21 m².jpg"
    assert len(names) == 5


async def test_a_failed_photo_leaves_the_rest_of_the_set(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rooms = _rooms(MONTELLY)
    rooms[5]["camera"] = "bad"  # Salon
    _fake_service(monkeypatch, FakeService(rooms, fail={"bad"}))
    pid = await _furnished_project()
    r = await app_client.post(f"/api/v1/projects/{pid}/listing")
    assert await _wait_job(r.json()["id"]) == "done"
    ls = (await app_client.get(f"/api/v1/projects/{pid}/listing")).json()["listing"]
    assert ls["status"] == "partial"
    salon = ls["photos"][0]
    assert salon["url"] is None
    assert "out of memory" in salon["error"]
    assert sum(1 for p in ls["photos"] if p["url"]) == 4


async def test_shooting_again_reuses_the_cameras(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = FakeService(_rooms(MONTELLY))
    _fake_service(monkeypatch, svc)
    pid = await _furnished_project()
    r = await app_client.post(f"/api/v1/projects/{pid}/listing")
    assert await _wait_job(r.json()["id"]) == "done"
    svc.rooms = [{**x, "camera": "9,1.3,9>8,1,8@58"} for x in svc.rooms]
    r = await app_client.post(f"/api/v1/projects/{pid}/listing", json={"reuse": True})
    assert await _wait_job(r.json()["id"]) == "done"
    shot_cameras = [c["camera"] for c in svc.calls.values() if c["path"] == "/shoot"]
    assert shot_cameras == [CAM] * 10


async def test_a_staging_is_kept_when_the_set_is_shot_again(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = FakeService(_rooms(MONTELLY))
    _fake_service(monkeypatch, svc)
    pid = await _furnished_project()

    async def shoot(body: dict[str, Any]) -> list[str | None]:
        before = len(svc.calls)
        r = await app_client.post(f"/api/v1/projects/{pid}/listing", json=body)
        assert await _wait_job(r.json()["id"]) == "done"
        calls = list(svc.calls.values())[before:]
        return [c.get("staging") for c in calls if c["path"] == "/shoot"]

    assert set(await shoot({})) == {None}  # furnished: nothing to ask
    listing = (await app_client.get(f"/api/v1/projects/{pid}/listing")).json()["listing"]
    assert listing["staging"] == "furnished"
    assert set(await shoot({"reuse": True, "staging": "empty"})) == {"empty"}
    assert set(await shoot({"reuse": True})) == {"empty"}
    listing = (await app_client.get(f"/api/v1/projects/{pid}/listing")).json()["listing"]
    assert listing["staging"] == "empty"
    assert set(await shoot({"reuse": True, "staging": "light"})) == {"light"}
    r = await app_client.post(f"/api/v1/projects/{pid}/listing", json={"staging": "bare"})
    assert r.status_code == 422


async def test_listing_needs_an_interior_and_a_service(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = await _furnished_project()
    (ProjectStorage(pid).versions_dir / "1" / "src" / "scene.js").write_text("export default 1;\n")
    r = await app_client.post(f"/api/v1/projects/{pid}/listing")
    assert r.status_code == 422
    monkeypatch.setenv("PHOTO_SERVICE_URL", "")
    get_settings.cache_clear()
    assert (await app_client.get(f"/api/v1/projects/{pid}/listing")).json()["available"] is False
    assert (await app_client.post(f"/api/v1/projects/{pid}/listing")).status_code == 409


class FakeEditor:
    """The picture editor's provider: one answer (or exception) for every call."""

    def __init__(self, answer: str | Exception) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kw: Any) -> Completion:
        self.calls.append(kw)
        if isinstance(self.answer, Exception):
            raise self.answer
        return Completion(
            message=Message.assistant(self.answer),
            stop_reason="end_turn",
            usage=Usage(input_tokens=6000, output_tokens=400),
            model="claude-opus-5",
        )


def _salon_with_views() -> list[dict[str, Any]]:
    rooms = _rooms(MONTELLY)
    rooms[5]["views"] = VIEWS  # Salon, the set's first photo
    return rooms


def _set_doc(pid: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(
        (ProjectStorage(pid).versions_dir / "1" / "listing" / "set.json").read_text()
    )
    return doc


async def test_the_editor_picks_a_previewed_viewpoint_and_a_piece_to_remove(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = FakeService(_salon_with_views())
    _fake_service(monkeypatch, svc)
    answer = {"best": 2, "remove": "vase-1", "problem": "a bare wall", "why": "the long view"}
    editor = FakeEditor(json.dumps(answer))
    monkeypatch.setattr(listing_job, "get_provider", lambda name=None: editor)
    pid = await _furnished_project()
    r = await app_client.post(f"/api/v1/projects/{pid}/listing")
    assert await _wait_job(r.json()["id"]) == "done"

    shoots = [c for c in svc.calls.values() if c["path"] == "/shoot"]
    previews = [c for c in shoots if c["width"] == 1280]
    assert sorted(c["camera"] for c in previews) == sorted(VIEWS)
    assert {c["samples"] for c in previews} == {64}
    finals = [c for c in shoots if c["width"] == 3840]
    assert len(finals) == 5  # one room had viewpoints to choose from; the others shoot their camera
    salon = next(c for c in finals if c["camera"] == VIEWS[1])
    assert (salon["sun"], salon["room_lamps"], salon["remove"]) == (
        [200.0, 30.0],
        False,
        ["vase-1"],
    )
    assert all("sun" not in c for c in finals if c is not salon)

    assert len(editor.calls) == 1
    shown = editor.calls[0]["messages"][0].content
    assert sum(isinstance(p, ImagePart) for p in shown) == 3
    doc = _set_doc(pid)
    assert doc["choices"]["01"]["view"] == 2
    assert doc["choices"]["01"]["by"] == "editor"
    assert doc["photos"]["01"]["removed"] == ["vase-1"]
    assert doc["cost"]["previews_usd"] > 0
    assert doc["cost"]["editor_usd"] > 0
    ls = (await app_client.get(f"/api/v1/projects/{pid}/listing")).json()["listing"]
    assert (ls["previews_done"], ls["previews_total"]) == (3, 3)
    assert ls["photos"][0]["why"] == "the long view"


async def test_without_an_answer_the_best_scored_viewpoint_is_shot(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = FakeService(_salon_with_views())
    _fake_service(monkeypatch, svc)
    monkeypatch.setattr(
        listing_job, "get_provider", lambda name=None: FakeEditor(RuntimeError("no API key"))
    )
    pid = await _furnished_project()
    r = await app_client.post(f"/api/v1/projects/{pid}/listing")
    assert await _wait_job(r.json()["id"]) == "done"
    finals = [c for c in svc.calls.values() if c["path"] == "/shoot" and c["width"] == 3840]
    salon = next(c for c in finals if c["camera"] == VIEWS[0] and "sun" in c)
    assert "remove" not in salon
    choice = _set_doc(pid)["choices"]["01"]
    assert (choice["view"], choice["by"]) == (1, "score")
    assert "no API key" in choice["error"]


async def test_without_the_editor_no_viewpoint_is_previewed(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LISTING_PICK", "false")
    get_settings.cache_clear()
    svc = FakeService(_salon_with_views())
    _fake_service(monkeypatch, svc)
    editor = FakeEditor("{}")
    monkeypatch.setattr(listing_job, "get_provider", lambda name=None: editor)
    pid = await _furnished_project()
    r = await app_client.post(f"/api/v1/projects/{pid}/listing")
    assert await _wait_job(r.json()["id"]) == "done"
    widths = [c["width"] for c in svc.calls.values() if c["path"] == "/shoot"]
    assert widths == [3840] * 5
    assert editor.calls == []


def test_shots_carry_their_viewpoints_best_first() -> None:
    shots = pick(_salon_with_views())
    assert shots[0].views == VIEWS
    assert shots[1].views == [CAM]  # a room prepared before #92: its one camera


def test_the_editors_answer_is_checked() -> None:
    previews = [(Path("a.jpg"), [PIECE]), (Path("b.jpg"), [])]
    c = parse_choice('{"best": 1, "remove": "vase-1", "problem": "p", "why": "w"}', 2, previews)
    assert (c.best, c.remove) == (1, "vase-1")
    # a piece the chosen frame does not show is not removed
    c = parse_choice('{"best": 2, "remove": "vase-1", "problem": "p", "why": "w"}', 2, previews)
    assert (c.best, c.remove) == (2, None)
    with pytest.raises(LLMError, match="viewpoint 3 of 2"):
        parse_choice('{"best": 3, "remove": null, "problem": "p", "why": "w"}', 2, previews)
    with pytest.raises(LLMError, match="invalid JSON"):
        parse_choice("the second one", 2, previews)


# outside.viewpoints' records, best-scored first
EXTERIORS = [
    {"camera": f"{x},4.5,20>0,4,0@46", "side": side, "sun": [az, 35.0], "when": "2026-09-10 16:00"}
    for x, side, az in ((-14, "south-west", 250.0), (0, "south", 200.0), (14, "south-east", 150.0))
]


async def test_the_building_from_outside_leads_the_set(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = FakeService(_rooms(MONTELLY), exteriors=EXTERIORS)
    _fake_service(monkeypatch, svc)
    editor = FakeEditor(json.dumps({"keep": [3, 1], "why": "the garden side first"}))
    monkeypatch.setattr(listing_job, "get_provider", lambda name=None: editor)
    pid = await _furnished_project()
    st = ProjectStorage(pid)
    geo_context.context_dir(st.root).mkdir(parents=True)
    geo_context.save(st.root, {"version": 1, "place": {"e": 2537559, "n": 1157053}})
    r = await app_client.post(f"/api/v1/projects/{pid}/listing")
    assert await _wait_job(r.json()["id"]) == "done"

    prep = next(c for c in svc.calls.values() if c["path"] == "/prepare")
    assert prep["context_url"].endswith(f"/scenes/{pid}/context/")
    assert prep["site"] == pytest.approx([46.5618, 6.6243], abs=1e-3)
    shoots = [c for c in svc.calls.values() if c["path"] == "/shoot"]
    previews = [c for c in shoots if c["width"] == 1280]
    assert sorted(c["camera"] for c in previews) == sorted(v["camera"] for v in EXTERIORS)
    assert {c["kind"] for c in previews} == {"exterior"}
    outside = [c for c in shoots if c["width"] == 3840 and c.get("kind") == "exterior"]
    assert sorted((c["camera"], tuple(c["sun"])) for c in outside) == sorted(
        (EXTERIORS[i]["camera"], tuple(EXTERIORS[i]["sun"])) for i in (2, 0)
    )
    assert (
        len(editor.calls) == 1
    )  # the rooms have one viewpoint each: only the exteriors are chosen

    ls = (await app_client.get(f"/api/v1/projects/{pid}/listing")).json()["listing"]
    assert [p["caption"] for p in ls["photos"]][:3] == [
        "Vue extérieure, côté sud-est",
        "Vue extérieure, côté sud-ouest",
        "Salon, 21 m²",
    ]
    assert ls["photos"][0]["why"] == "the garden side first"
    assert all(p["url"] for p in ls["photos"])
    assert (ls["previews_done"], ls["previews_total"]) == (3, 3)
    doc = _set_doc(pid)
    assert doc["choices"]["E1"]["view"] == 3
    assert doc["cost"]["editor_usd"] > 0
    z = await app_client.get(f"/api/v1/projects/{pid}/listing/1/zip")
    assert zipfile.ZipFile(io.BytesIO(z.content)).namelist()[0] == (
        "E1 Vue extérieure, côté sud-est.jpg"
    )

    # shot again: the same exterior cameras, no new previews and no editor
    r = await app_client.post(f"/api/v1/projects/{pid}/listing", json={"reuse": True})
    assert await _wait_job(r.json()["id"]) == "done"
    assert len(editor.calls) == 1
    again = [c for c in list(svc.calls.values())[len(shoots) + 1 :] if c["path"] == "/shoot"]
    assert [c["camera"] for c in again if c.get("kind") == "exterior"] == [
        EXTERIORS[2]["camera"],
        EXTERIORS[0]["camera"],
    ]
    assert {c["width"] for c in again} == {3840}


async def test_without_the_editor_the_best_scored_exteriors_lead(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LISTING_PICK", "false")
    get_settings.cache_clear()
    four = [*EXTERIORS, {**EXTERIORS[0], "camera": "0,4.5,-20>0,4,0@46", "side": "north"}]
    svc = FakeService(_rooms(MONTELLY), exteriors=four)
    _fake_service(monkeypatch, svc)
    pid = await _furnished_project()
    r = await app_client.post(f"/api/v1/projects/{pid}/listing")
    assert await _wait_job(r.json()["id"]) == "done"
    shoots = [c for c in svc.calls.values() if c["path"] == "/shoot"]
    assert {c["width"] for c in shoots} == {3840}
    assert sorted(c["camera"] for c in shoots if c.get("kind") == "exterior") == sorted(
        v["camera"] for v in EXTERIORS
    )
    assert "context_url" not in next(c for c in svc.calls.values() if c["path"] == "/prepare")


def test_the_editors_exterior_answer_is_checked() -> None:
    assert parse_exteriors('{"keep": [2, 2, 9, 1], "why": "w"}', 3, 2, 4).keep == [2, 1]
    assert parse_exteriors('{"keep": [3], "why": "w"}', 3, 2, 4).keep == [3, 1]  # padded by score
    assert parse_exteriors('{"keep": [5, 4, 3, 2, 1], "why": "w"}', 5, 2, 4).keep == [5, 4, 3, 2]
    with pytest.raises(LLMError, match="none of the 3"):
        parse_exteriors('{"keep": [7], "why": "w"}', 3, 2, 4)


def test_exterior_captions_follow_the_rooms_language() -> None:
    assert language([n for _, n, _ in GILLE]) == "fr"
    assert language(["Wohnzimmer", "Küche", "Bad"]) == "de"
    assert language(["Living room", "Bedroom 1"]) == "en"
    assert exterior_caption("south-west", "fr") == "Vue extérieure, côté sud-ouest"
    assert exterior_caption("north", "de") == "Aussenansicht von Norden"
    assert exterior_caption("east", "en") == "View from the east"
