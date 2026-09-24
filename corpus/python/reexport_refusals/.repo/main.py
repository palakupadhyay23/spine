"""Calls through both unresolvable bindings."""

from lib import Lazy, compute


def run_compute():
    return compute()


def run_lazy():
    return Lazy()
