import pytest

from sap_cli.planning.variables import (
    bind_variables,
    contains_variable_tokens,
    redact_variables,
)


def test_strict_binding_resolves_nested_data_and_preserves_exact_token_type():
    template = {
        "tcode": "${tcode}",
        "args": {"row": "${row}", "label": "Material ${material}"},
        "guards": ["client=${client}"],
    }
    result = bind_variables(
        template,
        {"tcode": "MM03", "row": 4, "material": "MAT-1", "client": "100"},
    )

    assert result.require_complete() == {
        "tcode": "MM03",
        "args": {"row": 4, "label": "Material MAT-1"},
        "guards": ["client=100"],
    }
    assert result.used == ("client", "material", "row", "tcode")
    assert not contains_variable_tokens(result.value)


def test_missing_variable_is_reported_and_never_silently_left_executable():
    result = bind_variables({"tcode": "${tcode}", "value": "${missing}"}, {"tcode": "SM30"})
    assert result.missing == ("missing",)
    assert contains_variable_tokens(result.value)
    with pytest.raises(ValueError, match="missing"):
        result.require_complete()


def test_resolution_happens_before_dangerous_tcode_policy_can_inspect_value():
    result = bind_variables({"command": "run", "tcode": "${tcode}"}, {"tcode": "SM30"})
    assert result.require_complete()["tcode"] == "SM30"


def test_redaction_does_not_mutate_values_used_for_digest_or_authorization():
    values = {"material": "MAT-1", "password": "SECRET"}
    preview = redact_variables(values, {"password"})
    assert preview == {"material": "MAT-1", "password": "<redacted>"}
    assert values["password"] == "SECRET"
