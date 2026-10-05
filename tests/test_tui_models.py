from de_dolby.tui_models import QueueModel, QueueStatus, TuiView


def test_queue_add_preserves_spelling_and_rejects_duplicate_identity(tmp_path):
    model = QueueModel()
    path = str(tmp_path / "Movie.DV.mkv")
    item = model.add(path)
    assert item.input_path == path
    assert item.status is QueueStatus.PENDING
    assert model.selected is item
    assert model.add(path) is None


def test_queue_remove_and_reorder_keep_selection_stable():
    model = QueueModel()
    first = model.add("first.mkv")
    second = model.add("second.mkv")
    model.select(1)
    assert model.move_selected(-1)
    assert model.items == [second, first]
    assert model.selected is second
    assert model.remove_selected() is second
    assert model.selected is first


def test_queue_boundary_moves_and_empty_removal_are_safe():
    model = QueueModel()
    assert model.remove_selected() is None
    model.add("only.mkv")
    assert not model.move_selected(-1)
    assert not model.move_selected(1)


def test_view_model_exposes_all_required_navigation_states():
    assert {view.value for view in TuiView} == {
        "queue", "browser", "details", "logs", "progress", "help"
    }


def test_queue_item_supports_resume_identity_and_live_eta_fields():
    item = QueueModel().add("movie.mkv")
    item.input_identity = {"path": "movie.mkv", "size": 1, "modified_ns": 2}
    item.plan_fingerprint = "abc"
    item.output_identity = {"path": "out.mkv", "size": 3, "modified_ns": 4}
    item.progress_percent = 50
    item.eta_seconds = 60
    assert item.plan_fingerprint == "abc"
