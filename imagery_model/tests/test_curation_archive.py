import hashlib
import json
import zipfile

import pytest

from urban_tree_ml.curation_archive import acquisition, capture, restore, status
from urban_tree_ml.feedback import _json_sha256, _review_state_sha256


def fixture(root):
    raster = root / "source.tif"
    raster.write_bytes(b"original multiband pixels")
    acquisition(root, raster, register=True)
    review = root / "review"
    review.mkdir()
    (review / "scene.png").write_bytes(b"review pixels")
    bundle = root / "annotations"
    bundle.mkdir()
    manifest = {"scenes": [{"image": "scene.png"}]}
    reviews = {"reviews": {}, "scene_reviews": {}, "mask_regions": []}
    for name, data in [("manifest.json", manifest), ("reviews.json", reviews)]:
        (bundle / name).write_text(json.dumps(data))
    metadata = {
        "city": "USSFO",
        "review_id": "scene-v1",
        "feedback_current": False,
        "source_manifest_sha256": _json_sha256(manifest),
        "review_state_sha256": _review_state_sha256({}, {}, []),
        "files": {
            name: {"sha256": hashlib.sha256((bundle / name).read_bytes()).hexdigest()}
            for name in ["manifest.json", "reviews.json"]
        },
    }
    (bundle / "bundle.json").write_text(json.dumps(metadata))
    return raster, review, bundle


def test_immutable_snapshot_and_inconsistent_bundle(tmp_path):
    raster, review, bundle = fixture(tmp_path)
    first = capture(tmp_path, bundle, review, raster)
    assert capture(tmp_path, bundle, review, raster) == first
    packet = tmp_path / "curation-archive/revisions" / (first["revision"] + ".zip")
    with zipfile.ZipFile(packet) as archive:
        assert json.loads(archive.read("archive.json"))["acquisition_id"]
        assert archive.read("reviews.json") == (bundle / "reviews.json").read_bytes()
    (bundle / "reviews.json").write_text("{}")
    with pytest.raises(ValueError, match="changing"):
        capture(tmp_path, bundle, review, raster)
    assert packet.exists()


def test_changed_source_needs_new_review_identity(tmp_path):
    raster, review, bundle = fixture(tmp_path)
    capture(tmp_path, bundle, review, raster)
    raster.write_bytes(b"different acquisition")
    with pytest.raises(ValueError, match="changed"):
        capture(tmp_path, bundle, review, raster)
    acquisition(tmp_path, raster, register=True)
    with pytest.raises(ValueError, match="different imagery"):
        capture(tmp_path, bundle, review, raster)


def test_status_does_not_mistake_old_backup_for_current(tmp_path):
    folder = tmp_path / "curation-archive/remote-status"
    folder.mkdir(parents=True)
    (folder / "ussfo.json").write_text(
        json.dumps({"review_state_sha256": "old", "manifest_sha256": "m"})
    )
    assert not status(tmp_path, "ussfo", "new", False, "m")["remote"]["current"]
    assert not status(tmp_path, "ussfo", "old", False, "new-manifest")["remote"]["current"]
    assert status(tmp_path, "ussfo", "old", False, "m")["remote"]["current"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_restore_verifies_remote_pixels_and_never_overwrites(tmp_path, monkeypatch, corrupt):
    from pathlib import Path

    raster, review, bundle = fixture(tmp_path)
    info = capture(tmp_path, bundle, review, raster)
    root = tmp_path / "curation-archive"
    asset = acquisition(tmp_path, raster)["assets"][0]

    def transfer(*args):
        source, target = str(args[3]), Path(args[4])
        key = source.removeprefix("gs://test/")
        if args[2] == "rsync":
            for path in (root / "images").iterdir():
                (target / path.name).write_bytes(b"bad pixels" if corrupt else path.read_bytes())
        elif key.startswith("objects/"):
            assert key == "objects/" + asset["sha256"]
            target.write_bytes(raster.read_bytes())
        else:
            target.write_bytes((root / key).read_bytes())
        return ""

    monkeypatch.setattr("urban_tree_ml.curation_archive.command", transfer)
    destination = tmp_path / "restored"
    if corrupt:
        with pytest.raises(ValueError, match="image checksum"):
            restore(tmp_path, info["revision"], destination, "gs://test", True)
        assert not (destination / "RESTORE_VERIFIED.json").exists()
    else:
        restore(tmp_path, info["revision"], destination, "gs://test", True)
        assert (destination / "RESTORE_VERIFIED.json").exists()
        assert (destination / "source-imagery/source.tif").read_bytes() == raster.read_bytes()
    with pytest.raises(FileExistsError):
        restore(tmp_path, info["revision"], destination, "gs://test", True)
