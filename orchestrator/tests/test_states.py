from orchestrator.states import State, TERMINAL, is_valid_transition


def test_states_enum_values():
    assert State.CREATED.value == "created"
    assert State.PREVIEW_READY.value == "preview-ready"
    assert State.MERGED.value == "merged"
    assert State.DISCARDED.value == "discarded"


def test_terminal_states():
    assert TERMINAL == {State.MERGED, State.FAILED, State.EXPIRED, State.DISCARDED}


def test_valid_forward_transitions():
    assert is_valid_transition(State.CREATED, State.CLARIFYING)
    assert is_valid_transition(State.CLARIFYING, State.LOCATED)
    assert is_valid_transition(State.LOCATED, State.CODING)
    assert is_valid_transition(State.CODING, State.BUILDING)
    assert is_valid_transition(State.BUILDING, State.PREVIEW_READY)
    assert is_valid_transition(State.PREVIEW_READY, State.MERGED)


def test_any_active_state_can_fail():
    for s in (State.CREATED, State.CLARIFYING, State.LOCATED, State.CODING, State.BUILDING):
        assert is_valid_transition(s, State.FAILED)


def test_preview_ready_can_expire_or_discard():
    assert is_valid_transition(State.PREVIEW_READY, State.EXPIRED)
    assert is_valid_transition(State.PREVIEW_READY, State.DISCARDED)


def test_active_states_can_discard():
    assert is_valid_transition(State.CLARIFYING, State.DISCARDED)


def test_invalid_transitions_rejected():
    assert not is_valid_transition(State.MERGED, State.CODING)
    assert not is_valid_transition(State.CREATED, State.PREVIEW_READY)
    assert not is_valid_transition(State.FAILED, State.CLARIFYING)
