"""SQLite transaction boundaries for project task operations."""
from contextlib import contextmanager
from functools import wraps


@contextmanager
def project_task_transaction(store, workspace, identity):
    """Commit legacy task import atomically in SQLite."""
    with store.atomic():
        yield


def project_task_mutation(method):
    @wraps(method)
    def wrapped(store, *args, **kwargs):
        with store.atomic():
            return method(store, *args, **kwargs)
    return wrapped
