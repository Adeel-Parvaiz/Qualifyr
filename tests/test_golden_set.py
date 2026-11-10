"""The golden set is the guardrail for the BUYER/VENDOR/UNKNOWN gate: a change that quietly
starts pitching web agencies, or rejecting real retailers, fails here. Pure and offline."""

from gtm_engine.eval.golden import evaluate, format_report, load_golden

# The CEO's success metric. Kept below the current 100% so a single new hard row doesn't red
# the build the day it is added - raise it as the set proves out.
ACCURACY_FLOOR = 0.8


def test_golden_accuracy_meets_floor():
    report = evaluate(load_golden())
    assert report.total >= 15, "golden set is too small to be meaningful"
    assert report.accuracy >= ACCURACY_FLOOR, "\n" + format_report(report)


def test_web_and_software_agencies_are_never_buyers():
    """The worst failure available: pitching what we sell to a company that sells it too."""
    report = evaluate([r for r in load_golden() if r.expected_type == "vendor"])
    vendor_correct, vendor_total = report.per_label().get("vendor", (0, 0))
    assert vendor_correct == vendor_total, "a vendor slipped through as a buyer:\n" + format_report(report)


def test_clear_retailers_are_buyers():
    buyers = [r for r in load_golden() if r.expected_type == "buyer"]
    report = evaluate(buyers)
    correct, total = report.per_label().get("buyer", (0, 0))
    # Allow one edge case, but the bulk of obvious retailers must qualify.
    assert correct >= total - 1, "real retailers are being rejected:\n" + format_report(report)
