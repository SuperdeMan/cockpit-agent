from agents._sdk.location import current_location_from_meta


def test_current_location_parses_valid_session_coordinates():
    point = current_location_from_meta({"current_lat": "39.92", "current_lng": "116.41"})
    assert point is not None
    assert point.lat == 39.92 and point.lng == 116.41


def test_current_location_rejects_invalid_or_out_of_range_coordinates():
    assert current_location_from_meta({"current_lat": "x", "current_lng": "116.41"}) is None
    assert current_location_from_meta({"current_lat": "91", "current_lng": "116.41"}) is None
