from importlib.metadata import version

import incident_triage


def test_version_matches_distribution_metadata() -> None:
    assert incident_triage.__version__ == version("incident-triage-agent")
