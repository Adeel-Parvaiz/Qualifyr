from types import SimpleNamespace

from gtm_engine.paypal.store import revenue_summary


def test_revenue_includes_paid_checkouts_but_not_pending_amounts():
    invoices = [{"campaign_id": "camp", "currency": "EUR", "status": "PAID",
                 "is_synthetic": False, "n": 1, "cents": 1000}]
    orders = [
        {"campaign_id": "camp", "currency": "USD", "status": "PAID", "is_synthetic": False, "n": 2, "cents": 21500},
        {"campaign_id": "camp", "currency": "USD", "status": "PENDING", "is_synthetic": False, "n": 1, "cents": 5000},
    ]
    checkouts = [{"checkout_id": "checkout", "status": "PAID"}]
    responses = iter([invoices, orders, checkouts])

    def execute(sql, params):
        # Every source must preserve both tenant and campaign scoping.
        assert "owner_id = %s" in sql
        assert "campaign_id = %s" in sql
        assert params == ("user", "camp")
        assert "status <> 'FAILED'" in sql
        rows = next(responses)
        return SimpleNamespace(fetchall=lambda: rows)

    result = revenue_summary(SimpleNamespace(_execute=execute), "user", "camp")
    assert result["paid_cents_by_currency"] == {"EUR": 1000, "USD": 21500}
    assert result["total_invoices"] == result["paid_invoices"] == 1
    assert result["total_checkouts"] == 3
    assert result["paid_checkouts"] == 2
    assert result["total_payments"] == 4
    assert result["paid_payments"] == 3
    assert result["paid_rate"] == 0.75
    assert result["checkouts"] == checkouts
    assert result["per_campaign"][0]["invoices"] == 1
    assert result["per_campaign"][0]["checkouts"] == 3


def test_empty_revenue_has_no_phantom_payments():
    db = SimpleNamespace(_execute=lambda *args: SimpleNamespace(fetchall=lambda: []))
    result = revenue_summary(db, None)
    assert result["total_payments"] == result["paid_payments"] == 0
    assert result["paid_rate"] == 0
    assert result["paid_cents_by_currency"] == {}
    assert result["checkouts"] == []
