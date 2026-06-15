from val import VAL


def test_on_change_reports_diff_batch():
    captured = []
    val = VAL(on_change=lambda changes: captured.append(changes))

    val.execute(
        {
            "domain": "car_control",
            "intent": "hvac.set",
            "data": {"object": "aircon", "operate": "set", "value": 26},
        }
    )

    assert captured, "execution should trigger one change callback"
    keys = {change["key"] for change in captured[0]}
    assert "hvac_temp" in keys and "hvac_on" in keys
    temp = next(change for change in captured[0] if change["key"] == "hvac_temp")
    assert temp["new"] == 26


def test_no_state_change_no_callback():
    captured = []
    val = VAL(on_change=lambda changes: captured.append(changes))

    val.execute("media.next")

    assert captured == []


def test_set_env_triggers_callback():
    captured = []
    val = VAL(on_change=lambda changes: captured.append(changes))

    val.set_env("speed_kmh", 130)

    assert captured and captured[0][0]["key"] == "speed_kmh"
    assert captured[0][0]["new"] == 130
    assert val.state["speed_kmh"] == 130


def test_battery_location_defaults_present():
    val = VAL()

    assert val.state["battery"] == 72
    assert val.state["location"] is None
