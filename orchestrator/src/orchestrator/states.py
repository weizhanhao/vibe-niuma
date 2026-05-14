from enum import Enum


class State(str, Enum):
    CREATED = "created"
    CLARIFYING = "clarifying"
    LOCATED = "located"
    CODING = "coding"
    BUILDING = "building"
    PREVIEW_READY = "preview-ready"
    MERGED = "merged"
    FAILED = "failed"
    EXPIRED = "expired"
    DISCARDED = "discarded"


TERMINAL: set[State] = {State.MERGED, State.FAILED, State.EXPIRED, State.DISCARDED}

# 主流水线推进 + 用户动作（merge/discard）+ 系统动作（expire/fail）的合法转换
_TRANSITIONS: dict[State, set[State]] = {
    State.CREATED: {State.CLARIFYING, State.FAILED, State.DISCARDED},
    State.CLARIFYING: {State.LOCATED, State.FAILED, State.DISCARDED},
    State.LOCATED: {State.CODING, State.FAILED, State.DISCARDED},
    State.CODING: {State.BUILDING, State.FAILED, State.DISCARDED},
    State.BUILDING: {State.PREVIEW_READY, State.FAILED, State.DISCARDED},
    State.PREVIEW_READY: {State.MERGED, State.EXPIRED, State.DISCARDED, State.FAILED},
    State.MERGED: set(),
    State.FAILED: set(),
    State.EXPIRED: set(),
    State.DISCARDED: set(),
}


def is_valid_transition(src: State, dst: State) -> bool:
    return dst in _TRANSITIONS.get(src, set())
