from app.db.models import ApplyStatus, JobStatus


class IllegalTransition(ValueError):
    pass


class NonMonotonicVersion(ValueError):
    pass


LEGAL_APPLY: dict[ApplyStatus, frozenset[ApplyStatus]] = {
    ApplyStatus.pending: frozenset({ApplyStatus.pending, ApplyStatus.queued}),
    ApplyStatus.queued: frozenset({ApplyStatus.pending, ApplyStatus.applying}),
    ApplyStatus.applying: frozenset({ApplyStatus.pending, ApplyStatus.active, ApplyStatus.failed}),
    ApplyStatus.active: frozenset({ApplyStatus.pending}),
    ApplyStatus.failed: frozenset({ApplyStatus.pending, ApplyStatus.queued}),
}


def assert_transition(current: ApplyStatus, target: ApplyStatus) -> None:
    if target not in LEGAL_APPLY[current]:
        raise IllegalTransition(f"Illegal apply transition: {current.value} -> {target.value}")


def assert_active_version_advances(active_version: int | None, new: int) -> None:
    previous = active_version if active_version is not None else -1
    if new <= previous:
        raise NonMonotonicVersion(f"active_version must advance from {previous} to a greater value")


def is_terminal(job_status: JobStatus) -> bool:
    return job_status in {JobStatus.succeeded, JobStatus.failed, JobStatus.superseded}
