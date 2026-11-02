"""POS log profile, layout detection and the rule capabilities that follow from what a POS sends."""

import asyncio
from pathlib import Path

import pytest
from apps.ingest.contracts import PosLogRequest
from apps.ingest.pos_formats import profile, rtlog
from apps.ingest.routes import profile_pos_log
from fastapi import HTTPException

SAMPLES = sorted((Path(__file__).resolve().parents[4] / "TLOG").glob("RTLOG_*.DAT"))
NUMERIC = {
    "line_id", "transaction_number", "original_transaction_number", "quantity", "unit_retail",
    "original_unit_retail", "unit_discount", "amount", "rate", "record_count", "value", "department",
    "class", "subclass",
}  # fmt: skip


def render(kind: str, layout: str = "v14", **values) -> str:
    out = []
    for name, width in rtlog.LAYOUT_VARIANTS[layout][kind]:
        value = values.get(name, "")
        if name == "descriptor":
            value = kind
        out.append(
            str(value).rjust(width, "0")
            if name in NUMERIC and value != ""
            else str(value).ljust(width)
        )
    return "".join(out)


def file_of(*transactions, layout="v14", reference="V.14.0:BID 20150507064836") -> str:
    """Each transaction is (head values, [(kind, values)...]); trailers carry true counts."""
    lines = [
        render(
            "FHEAD",
            layout,
            line_id=1,
            file_type="RTLG",
            created_at="20150507064837",
            business_date="20140717",
            location="4241",
            reference=reference,
            origin_system="POS",
        )
    ]
    n = 1
    for head, body in transactions:
        n += 1
        lines.append(
            render(
                "THEAD",
                layout,
                line_id=n,
                register="001",
                event_time="20140717101500",
                transaction_number=n,
                **head,
            )
        )
        for kind, values in body:
            n += 1
            lines.append(render(kind, layout, line_id=n, **values))
        n += 1
        lines.append(render("TTAIL", layout, line_id=n, record_count=len(body)))
    lines.append(render("FTAIL", layout, line_id=n + 1, record_count=len(lines) - 1))
    return "\n".join(lines) + "\n"


SALE = (
    {"type": "SALE", "sub_type": "SALE"},
    [
        ("TITEM", dict(status="S", item_type="ITEM", item="SKU1", quantity_sign="P", quantity="10000", unit_retail="100000", taxable="Y")),
        ("IGTAX", dict(tax_code="DSTRCT", rate="52500", amount_sign="P", amount="5250")),
        ("TTEND", dict(group="CASH", type_id="1000", sign="P", amount="105250")),
    ],
)  # fmt: skip


def rules(text) -> dict[str, tuple[str, str]]:
    return {r["code"]: (r["status"], r["why"]) for r in profile.profile_rtlog(text)["rules"]}


def test_a_v14_file_is_detected_and_its_version_is_recorded():
    log = rtlog.parse_rtlog(file_of(SALE))
    assert (log.layout, log.version, log.problems) == ("v14", "V.14.0", [])


def test_a_10x_file_with_a_narrower_voucher_field_is_detected_and_read_correctly():
    text = file_of(SALE, layout="v10", reference="V.10.1")
    log = rtlog.parse_rtlog(text)
    assert (log.layout, log.problems) == ("v10", [])
    [line] = log.transactions[0]["lines"]
    assert (line["item_code"], line["quantity"], line["unit_price"], line["tax_amount"]) == (
        "SKU1",
        "1",
        "10",
        "0.525",
    )
    # the same text read as v14 would misplace every later item column
    assert (
        rtlog.fields("TITEM", text.splitlines()[2], rtlog.LAYOUT_VARIANTS["v14"])["quantity_sign"]
        != "P"
    )


def test_a_file_whose_items_fit_no_layout_is_refused_with_a_clear_message():
    broken = file_of(SALE).replace("P000000010000", "?????????????")
    with pytest.raises(ValueError, match="fit none of the known layouts"):
        rtlog.parse_rtlog(broken)


def test_a_garbled_amount_is_refused_not_crashed_on():
    garbled = file_of(SALE).replace("00000000000000105250", "0000000000000010525x")
    with pytest.raises(ValueError, match="expected digits"):
        rtlog.parse_rtlog(garbled)


def test_a_file_without_items_still_parses_with_the_default_layout():
    empty = ({"type": "NOSALE", "sub_type": "TCHG"}, [])
    assert rtlog.parse_rtlog(file_of(empty)).layout == "v14"


def test_the_profile_lists_what_is_sent_and_what_is_not():
    result = profile.profile_rtlog(file_of(SALE, ({"type": "NOSALE"}, [])))
    assert result["transaction_types"] == {"sale": 1, "no_sale": 1}
    assert result["tender_groups"] == {"CASH": 1} and result["item_types"] == {"ITEM": 1}
    assert result["records_not_sent"] == ["TCUST", "CATT", "TTAX"]
    assert (
        "item" in result["fields"]["TITEM"]["always"]
        and "pump" in result["fields"]["TITEM"]["never"]
    )
    assert "original_transaction_number" in result["fields"]["THEAD"]["never"]


def test_rules_follow_what_the_pos_sends():
    state = rules(file_of(SALE, ({"type": "NOSALE"}, [])))
    assert state["TRAN_OUT_BAL"][0] == profile.AVAILABLE
    assert (
        state["SKU_NOT_FOUND"][0] == profile.AVAILABLE
        and state["TAX_MISMATCH"][0] == profile.AVAILABLE
    )
    assert state["NOSALE_EXCESS"][0] == profile.AVAILABLE
    assert state["DISC_REASON_REQ"][0] == profile.NOTHING
    assert (
        state["VOID_NO_ORIG"][0] == profile.NOTHING
        and state["PAIDOUT_REASON_REQ"][0] == profile.NOTHING
    )


def test_returns_cannot_be_checked_against_their_sale_in_this_format():
    refund = (
        {"type": "RETURN", "sub_type": "RETURN"},
        [("TITEM", dict(status="R", item_type="ITEM", item="A", quantity_sign="N", quantity="10000", unit_retail="100000", taxable="Y"))],
    )  # fmt: skip
    status, why = rules(file_of(refund))["RETURN_OVER_SOLD"]
    assert status == profile.NOT_AVAILABLE and "original transaction" in why


def test_a_post_void_rule_needs_the_original_to_be_sent():
    without = ({"type": "PVOID"}, [])
    with_original = (
        {"type": "PVOID", "original_transaction_number": 5, "original_register": "001"},
        [],
    )
    assert rules(file_of(without))["VOID_NO_ORIG"][0] == profile.NOT_AVAILABLE
    assert rules(file_of(with_original))["VOID_NO_ORIG"][0] == profile.AVAILABLE


def test_paid_out_without_reason_codes_warns_that_everything_would_be_flagged():
    paid_out = (
        {"type": "PAIDOU", "sub_type": "MV"},
        [("TTEND", dict(group="CASH", type_id="1000", sign="P", amount="250000"))],
    )
    with_reason = ({"type": "PAIDOU", "sub_type": "MV", "reason_code": "SUPPLY"}, [])
    status, why = rules(file_of(paid_out))["PAIDOUT_REASON_REQ"]
    assert status == profile.AVAILABLE and "all would be flagged" in why
    assert "reason codes." in rules(file_of(with_reason))["PAIDOUT_REASON_REQ"][1]


def test_the_profile_endpoint_describes_a_file_and_refuses_a_bad_one():
    ok = PosLogRequest(format="rtlog", delivery_id="p1", content=file_of(SALE))
    result = asyncio.run(profile_pos_log(ok, {"role": "analyst"}))
    assert result["layout"] == "v14" and result["transactions"] == 1
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            profile_pos_log(
                PosLogRequest(format="rtlog", delivery_id="p2", content="nope"), {"role": "analyst"}
            )
        )
    assert caught.value.status_code == 422


@pytest.mark.skipif(not SAMPLES, reason="sample RTLOG files are not in TLOG/")
def test_the_sample_pos_profile_matches_what_we_found_in_its_files():
    result = profile.profile_rtlog(SAMPLES[-1].read_text(encoding="ascii"))
    assert (result["layout"], result["version"], result["origin_system"]) == (
        "v14",
        "V.14.0",
        "POS",
    )
    assert {"sale", "return", "void", "no_sale", "open", "close", "total", "day_close"} == set(
        result["transaction_types"]
    )
    assert result["records_not_sent"] == ["TCUST", "CATT", "TTAX"]
    state = {r["code"]: r["status"] for r in result["rules"]}
    assert (
        state["RETURN_OVER_SOLD"] == profile.NOT_AVAILABLE
        and state["TRAN_OUT_BAL"] == profile.AVAILABLE
    )
    assert (
        state["VOID_NO_ORIG"] == profile.NOTHING and state["PAIDOUT_REASON_REQ"] == profile.NOTHING
    )
