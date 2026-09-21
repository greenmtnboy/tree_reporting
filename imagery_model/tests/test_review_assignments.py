import hashlib
import json

from urban_tree_ml.review_assignments import review_assignments


def test_scoped_completed_integrity_checked_queue(tmp_path):
    folder = tmp_path / "benchmarks" / "v1"
    folder.mkdir(parents=True)
    content = (
        b"city,chip_id,queue,reason\nussfo,a,diagnostic,missed\n"
        b"usbos,b,representative audit,random\n"
    )
    path = folder / "validation-targets.csv"
    path.write_bytes(content)
    (folder / "manifest.json").write_text(
        json.dumps(
            {"new_run": "joint", "output_sha256": {path.name: hashlib.sha256(content).hexdigest()}}
        )
    )
    assert not review_assignments(tmp_path, "joint", "ussfo")["chips"]
    (folder / "COMPLETE").touch()
    assert list(review_assignments(tmp_path, "joint", "ussfo")["chips"]) == ["a"]
    assert not review_assignments(tmp_path, "old", "ussfo")["chips"]
    path.write_bytes(content + b"bad")
    assert not review_assignments(tmp_path, "joint", "ussfo")["chips"]
