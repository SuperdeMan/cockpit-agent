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


def test_initial_speed_gear_self_consistent():
    val = VAL()

    # 初始停车：P 挡且车速 0（消除 P 挡 60km/h 的矛盾）
    assert val.state["gear"] == "P"
    assert val.state["speed_kmh"] == 0


def test_speeding_up_engages_drive():
    val = VAL()

    val.set_env("speed_kmh", 50)         # P 挡起步 → 自动挂 D
    assert val.state["gear"] == "D"
    assert val.state["speed_kmh"] == 50


def test_park_zeroes_speed():
    captured = []
    val = VAL(on_change=lambda changes: captured.append(changes))

    val.set_env("speed_kmh", 60)         # 先动起来（gear→D）
    assert val.state["gear"] == "D" and val.state["speed_kmh"] == 60

    val.set_env("gear", "P")             # 挂 P → 车速归 0，同一轮回调
    assert val.state["speed_kmh"] == 0
    last = {change["key"]: change["new"] for change in captured[-1]}
    assert last["gear"] == "P" and last["speed_kmh"] == 0


def test_sunroof_set_opens_not_fallback():
    v = VAL()
    v.execute({"domain": "car_control", "intent": "sunroof.set",
               "data": {"object": "sunroof", "operate": "set"}})
    assert v.state["sunroof"] != "closed"     # 真打开
    assert "sunroof_set" not in v.state        # 不再落兜底键


def test_media_play_sets_playing_not_fallback():
    v = VAL()
    v.execute({"domain": "media", "intent": "music.play",
               "data": {"object": "music", "operate": "play"}})
    assert v.state["media"] == "playing"
    assert "music_play" not in v.state         # 不再落兜底键


def test_ambient_set_color_turns_light_on():
    v = VAL()
    v.execute({"domain": "car_control", "intent": "ambient_light.set",
               "data": {"object": "ambient_light", "operate": "set", "tag": "orange"}})
    assert v.state["ambient_light_color"] == "orange"
    assert v.state["ambient_light"] is True    # 设色隐含开灯
