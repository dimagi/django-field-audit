from contextlib import contextmanager


class NoopAtomicTransaction:
    @contextmanager
    def atomic(self, *args, **kwargs):
        yield
