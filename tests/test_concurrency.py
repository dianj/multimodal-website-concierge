"""Review T21.A7: tools read the state, change one field and save a full snapshot; two edits at the same time must not
overwrite each other."""
import json
import threading

from concierge import db, tools


def edit_many(section, field, prefix, count, errors):
    for i in range(count):
        result = json.loads(tools.update_text_content(section, field, f"{prefix} {i}"))
        if result["status"] != "success":
            errors.append(result)


def test_two_editors_at_once_lose_no_edit():
    """Probe: two threads x 40 edits of different fields -> all 40 services.heading edits were lost."""
    errors = []
    threads = [threading.Thread(target=edit_many, args=("services", "heading", "Heading", 40, errors)),
               threading.Thread(target=edit_many, args=("hero", "title", "Title", 40, errors))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    version, state = db.get_latest_state()
    assert state["sections"]["services"]["heading"] == "Heading 39"
    assert state["sections"]["hero"]["title"] == "Title 39"
    assert version == 81


def test_a_save_on_an_old_version_is_refused():
    version, state = db.get_latest_state()
    tools.update_text_content("hero", "title", "Newer")
    state["sections"]["contact"]["heading"] = "Stale"
    try:
        db.save_new_state(state, "stale write", parent=version)
        raise AssertionError("a save on an old version must be refused")
    except db.StaleVersion:
        pass
    assert db.get_latest_state()[1]["sections"]["hero"]["title"] == "Newer"


def test_two_undos_at_once_undo_two_versions():
    for title in ("A", "B", "C"):
        tools.update_text_content("hero", "title", title)
    threads = [threading.Thread(target=tools.rollback_version) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert db.get_latest_state()[1]["sections"]["hero"]["title"] == "A"
