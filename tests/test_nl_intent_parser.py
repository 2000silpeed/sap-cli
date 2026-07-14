import pytest

from sap_cli.planning.intent import parse_intent


def test_korean_display_request_extracts_tcode_inputs_and_goal_terms():
    parsed = parse_intent("티코드 MM03에서 자재번호 'MAT-100'의 기본 데이터를 조회해줘")
    assert parsed.tcode == "MM03"
    assert parsed.operation == "display"
    assert parsed.risk == "low"
    assert parsed.inputs == {"material": "MAT-100"}
    assert "기본" in parsed.goal_terms
    assert parsed.ambiguities == ()


def test_command_and_namespace_tcodes_are_supported():
    assert parse_intent("/nVA03 실행하고 문서번호='5001' 조회").tcode == "VA03"
    assert parse_intent("tcode /ACME/REPORT 실행").tcode == "/ACME/REPORT"


def test_high_risk_intents_are_not_misclassified_as_display():
    assert parse_intent("티코드 VA01에서 주문 생성").risk == "high"
    assert parse_intent("티코드 ZDOC에서 문서를 삭제").risk == "critical"
    assert parse_intent("티코드 FB01에서 전기 처리").operation == "post"


def test_missing_tcode_and_ambiguous_operation_are_explicit():
    parsed = parse_intent("자재 기본 데이터 부탁해")
    assert parsed.tcode == ""
    assert parsed.operation == "unknown"
    assert parsed.ambiguities == ("tcode_missing", "operation_ambiguous")


def test_sensitive_values_are_never_in_repr_or_redacted_summary():
    parsed = parse_intent("tcode SU01 조회 password='TOP-SECRET'")
    assert parsed.inputs["password"] == "TOP-SECRET"
    assert "TOP-SECRET" not in repr(parsed)
    assert parsed.redacted_summary()["inputs"]["password"] == "<redacted>"


def test_explicit_success_criteria_are_structured_without_invention():
    parsed = parse_intent(
        "tcode MM03 조회. 성공 조건: 자재 설명이 표시됨; 오류 상태가 아님"
    )
    assert parsed.success_criteria == ("자재 설명이 표시됨", "오류 상태가 아님")
    assert parse_intent("tcode MM03 조회").success_criteria == ()


@pytest.mark.parametrize(
    ("request_text", "expected_operation", "expected_terms"),
    [
        ("tcode ZREP 자재 설명 내보내기", "export", ("자재", "설명")),
        ("tcode ZREL 구매 문서 승인", "release", ("구매", "문서")),
        ("tcode ZSAVE sales order save", "save", ("sales", "order")),
        ("tcode ZDEL document delete", "delete", ("document",)),
    ],
)
def test_operation_words_do_not_pollute_goal_terms(
    request_text, expected_operation, expected_terms
):
    parsed = parse_intent(request_text)
    assert parsed.operation == expected_operation
    assert parsed.goal_terms == expected_terms
