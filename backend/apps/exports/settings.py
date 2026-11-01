"""Runtime settings for export delivery and the development-only test receiver."""

import os


def test_sink_enabled() -> bool:
    environment = os.getenv("ENVIRONMENT", "production").strip().lower()
    explicit_setting = os.getenv("NIMBUS_ENABLE_TEST_SINK", "true").strip().lower()
    return environment in {"development", "test"} and explicit_setting in {
        "1",
        "true",
        "yes",
        "on",
    }


def sink_secret() -> str:
    configured = os.getenv("NIMBUS_SINK_SECRET")
    if configured:
        return configured
    return "nimbus-local-test-sink-secret-32chars" if test_sink_enabled() else ""


SINK_SECRET = sink_secret()
