from main import app


def test_manual_reconciliation_requires_a_bounded_business_date():
    operation = app.openapi()["paths"]["/api/reconcile"]["post"]
    business_date = next(
        parameter for parameter in operation["parameters"] if parameter["name"] == "business_date"
    )

    assert business_date["required"] is True
    assert business_date["schema"]["minLength"] == 10
    assert business_date["schema"]["maxLength"] == 10
