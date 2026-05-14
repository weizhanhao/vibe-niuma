from orchestrator.models import ChangeRequest


def test_change_request_has_expected_columns():
    cols = set(ChangeRequest.__table__.columns.keys())
    assert cols == {
        "id",
        "url",
        "screenshot_b64",
        "box_coords",
        "viewport",
        "request_text",
        "state",
        "fail_phase",
        "fail_reason",
        "fail_log",
        "branch",
        "preview_url",
        "preview_handle",
        "retry_of",
        "created_at",
        "updated_at",
        "last_activity_at",
    }


def test_change_request_id_is_string_primary_key():
    assert ChangeRequest.__table__.columns["id"].primary_key is True
    assert ChangeRequest.__table__.columns["state"].nullable is False
