import pytest

from app.core.applystate import (
    IllegalTransition,
    NonMonotonicVersion,
    assert_active_version_advances,
    assert_transition,
    is_terminal,
)
from app.db.models import ApplyStatus, JobStatus

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ApplyStatus.pending, ApplyStatus.queued),
        (ApplyStatus.queued, ApplyStatus.applying),
        (ApplyStatus.applying, ApplyStatus.active),
        (ApplyStatus.applying, ApplyStatus.failed),
        (ApplyStatus.queued, ApplyStatus.pending),
        (ApplyStatus.applying, ApplyStatus.pending),
        (ApplyStatus.active, ApplyStatus.pending),
        (ApplyStatus.failed, ApplyStatus.pending),
        (ApplyStatus.failed, ApplyStatus.queued),
    ],
)
def test_legal_transitions_pass(current: ApplyStatus, target: ApplyStatus) -> None:
    assert_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ApplyStatus.pending, ApplyStatus.active),
        (ApplyStatus.queued, ApplyStatus.active),
        (ApplyStatus.failed, ApplyStatus.active),
        (ApplyStatus.active, ApplyStatus.applying),
    ],
)
def test_illegal_transitions_raise(current: ApplyStatus, target: ApplyStatus) -> None:
    with pytest.raises(IllegalTransition):
        assert_transition(current, target)


def test_active_version_can_start_from_none_and_advance() -> None:
    assert_active_version_advances(None, 1)
    assert_active_version_advances(1, 2)


@pytest.mark.parametrize(("active_version", "new"), [(1, 1), (2, 1)])
def test_active_version_must_advance(active_version: int, new: int) -> None:
    with pytest.raises(NonMonotonicVersion):
        assert_active_version_advances(active_version, new)


@pytest.mark.parametrize("status", [JobStatus.succeeded, JobStatus.failed, JobStatus.superseded])
def test_terminal_job_statuses(status: JobStatus) -> None:
    assert is_terminal(status) is True


@pytest.mark.parametrize("status", [JobStatus.queued, JobStatus.applying])
def test_non_terminal_job_statuses(status: JobStatus) -> None:
    assert is_terminal(status) is False
