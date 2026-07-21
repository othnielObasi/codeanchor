from billing.refunds import calculate_refund


def test_calculate_refund_rounds():
    assert calculate_refund(10.005) == 10.0 or calculate_refund(10.005) == 10.01
