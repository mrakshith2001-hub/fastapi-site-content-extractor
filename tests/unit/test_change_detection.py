from datetime import datetime, timedelta, timezone

from app.db.models import Operation
from app.services.change_detection import KnownObject, asset_type_for, detect_changes
from app.services.storage import RemoteObject

NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(hours=24)


def remote(key, etag="a", age_hours=1, version=None):
    return RemoteObject(key=key, etag=etag, size=10, last_modified=NOW - timedelta(hours=age_hours), version_id=version)


def known(key, etag="a", version=None, deleted=False):
    return KnownObject(key=key, etag=etag, version_id=version, is_deleted=deleted)


def ops(changes):
    return {c.key: c.operation for c in changes}


def test_new_object_is_created():
    assert ops(detect_changes([remote("site/a.html")], {}, CUTOFF)) == {"site/a.html": Operation.CREATED}


def test_same_etag_is_unchanged_so_reruns_are_noops():
    inventory = {"site/a.html": known("site/a.html")}
    assert detect_changes([remote("site/a.html")], inventory, CUTOFF) == []


def test_etag_change_is_update():
    inventory = {"site/a.css": known("site/a.css", etag="old")}
    assert ops(detect_changes([remote("site/a.css", etag="new")], inventory, CUTOFF)) == {
        "site/a.css": Operation.UPDATED
    }


def test_version_id_takes_precedence_over_etag():
    inventory = {"k": known("k", etag="same", version="v1")}
    assert ops(detect_changes([remote("k", etag="same", version="v2")], inventory, CUTOFF)) == {
        "k": Operation.UPDATED
    }


def test_missing_object_is_deleted_with_marker_time():
    marker_time = NOW - timedelta(hours=2)
    changes = detect_changes([], {"k": known("k")}, CUTOFF, delete_markers={"k": marker_time})
    assert ops(changes) == {"k": Operation.DELETED}
    assert changes[0].last_modified == marker_time


def test_already_deleted_object_is_not_deleted_twice():
    assert detect_changes([], {"k": known("k", deleted=True)}, CUTOFF) == []


def test_recreated_object_counts_as_created():
    inventory = {"k": known("k", deleted=True)}
    assert ops(detect_changes([remote("k")], inventory, CUTOFF)) == {"k": Operation.CREATED}


def test_strict_window_ignores_old_changes():
    old = remote("old.html", age_hours=48)
    assert detect_changes([old], {}, CUTOFF, strict_window=True) == []
    assert ops(detect_changes([old], {}, CUTOFF, strict_window=False)) == {"old.html": Operation.CREATED}


def test_asset_types():
    assert asset_type_for("a/b.HTML") == "html"
    assert asset_type_for("fonts/x.woff2") == "font"
    assert asset_type_for("img/x.svg") == "image"
    assert asset_type_for("README") == "other"
