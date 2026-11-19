"""A job's pictures (#121): what each `render` event shows, kept with the job.

    data/projects/<id>/jobs/<job>/renders/<seq>-<view>.jpg

The renderer draws into the working copy's `scene/renders/<view>.jpg`, which every later render
replaces and a restore deletes. Each render event's pictures are hard links to those files (a
copy where the file system has none): a later render writes a new file over the name
(`write_replacing`), so the job's link keeps the picture that step made. They go with the
project's folder when it is deleted.

Events from before kept nothing: their URLs name the working copy's files, `?t=` their mtime then.
`readable` shows such a picture from the version it was saved as when it is one, from the working
copy while that file is still the one rendered, and otherwise lists the view as unavailable.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from housegen.projects.storage import ProjectStorage

logger = logging.getLogger(__name__)

Event = dict[str, Any]

_WORKING_COPY = re.compile(r"^/scenes/[^/]+/scene/renders/(?P<name>[^/?]+\.jpg)\?t=(?P<t>\d+)$")
# the render that follows it is a copy of that version's pictures (`_Run.before_renders`)
_VERSION_PICTURES = re.compile(r"^The current state: version (\d+)'s pictures$")
# a builder pass: a render followed by one of these is the builder's own, not a version's pictures
_BUILDER = frozenset({"builder_text", "builder_step", "builder_done"})


def keep(
    storage: ProjectStorage, job_id: str, seq: int, images: dict[str, Path]
) -> tuple[dict[str, str], list[str]]:
    """Keep the pictures of the job's render event `seq`: (URL by view, views that could not be
    kept)."""
    folder = storage.job_renders_dir(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    urls: dict[str, str] = {}
    lost: list[str] = []
    for view, src in images.items():
        name = f"{seq}-{src.name}"
        dst = folder / name
        try:
            # an interrupted run may have kept this seq before its event was stored
            dst.unlink(missing_ok=True)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
        except OSError as exc:
            logger.warning(
                "job.picture_not_kept",
                extra={"job_id": job_id, "view": view, "error": str(exc)[:200]},
            )
            lost.append(view)
            continue
        urls[view] = storage.job_render_url(job_id, name)
    return urls, lost


def readable(storage: ProjectStorage, events: list[Event]) -> list[Event]:
    """The job's events as the timeline shows them, the pictures of the render events from before
    #121 pointed at what is left of them."""
    versions = _legacy_versions(events)
    out: list[Event] = []
    for i, ev in enumerate(events):
        if ev["type"] == "render":
            ev = {**ev, "payload": _legacy_payload(storage, ev["payload"], versions.get(i))}
        out.append(ev)
    return out


def _legacy_versions(events: list[Event]) -> dict[int, int]:
    """Render event index → the version whose pictures it shows: the render a version was saved
    with (nothing of a builder pass between it and the `version` event; a restore is a version
    of another job's pictures), and the copy of a version's pictures a modification starts from."""
    found: dict[int, int] = {}
    candidate: int | None = None
    for i, ev in enumerate(events):
        kind, payload = ev["type"], ev["payload"]
        if kind == "render":
            before = events[i - 1] if i else None
            m = (
                _VERSION_PICTURES.match(str(before["payload"].get("message") or ""))
                if before is not None and before["type"] == "phase"
                else None
            )
            if m:
                found[i] = int(m.group(1))
            candidate = i
        elif kind == "version":
            if (
                candidate is not None
                and candidate not in found
                and payload.get("kind") != "restore"
            ):
                found[candidate] = int(payload.get("number") or 0)
            candidate = None
        elif kind in _BUILDER:
            candidate = None
    return found


def _legacy_payload(
    storage: ProjectStorage, payload: dict[str, Any], version: int | None
) -> dict[str, Any]:
    renders: dict[str, str] = {}
    unavailable = list(payload.get("unavailable") or [])
    changed = False
    for view, url in (payload.get("renders") or {}).items():
        m = _WORKING_COPY.match(str(url))
        if m is None:
            renders[view] = url
            continue
        changed = True
        name = m["name"]
        if (
            version is not None
            and (storage.versions_dir / str(version) / "renders" / name).exists()
        ):
            renders[view] = f"{storage.version_url(version)}/renders/{name}"
        elif _unchanged(storage.scene_dir / "renders" / name, int(m["t"])):
            renders[view] = url
        else:
            unavailable.append(view)
    if not changed:
        return payload
    out = {**payload, "renders": renders}
    if unavailable:
        out["unavailable"] = unavailable
    return out


def _unchanged(path: Path, mtime: int) -> bool:
    try:
        return int(path.stat().st_mtime) == mtime
    except OSError:
        return False
