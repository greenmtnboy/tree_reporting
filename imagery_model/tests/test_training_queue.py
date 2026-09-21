import pandas as pd

from urban_tree_ml.model_debug import MODEL_DEBUG_HTML
from urban_tree_ml.training_queue import TRAINING_REVIEW_SCRIPT, select


def test_assignment_excludes_holdouts_and_done():
    chips = pd.DataFrame(
        {
            "chip_id": ["a", "b", "c", "d"],
            "split": ["train", "validation", "test", "train"],
            "collision_excluded_count": [0, 0, 0, 0],
        }
    )
    labels = pd.DataFrame(
        {
            "chip_id": ["a", "b", "c", "d"],
            "tree_id": ["1", "2", "3", "4"],
            "split": chips.split,
            "dbh_log1p": [3.0, 3.0, 3.0, 3.0],
        }
    )
    result = select(chips, labels, {"4"}, set(), "ussfo")
    assert [row["chip_id"] for row in result] == ["a"]
    assert result[0]["split"] == "train"


def test_shared_template_has_single_entrypoint():
    assert MODEL_DEBUG_HTML.count("}init();") == 1
    html = MODEL_DEBUG_HTML.replace("}init();", "}" + TRAINING_REVIEW_SCRIPT)
    assert "if(trainingMode)initTraining();else init();" in html
    assert "Inference unavailable" in html
    assert "chips:items.map(i=>i.chip_id)" in html
    assert "queue:queueId" in html
