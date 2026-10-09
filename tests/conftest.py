"""pytest: the Qt objects tests left behind deleted while Qt still runs
(_paths._drain_qt) - otherwise a passing run could crash on its way out."""

import _paths


def pytest_sessionfinish(session, exitstatus):
    _paths._drain_qt()
