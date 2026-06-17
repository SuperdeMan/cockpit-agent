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


def test_window_set_records_percentage():
    v = VAL()
    v.execute({"domain": "car_control", "intent": "window.set",
               "data": {"object": "window", "operate": "set", "value": 50}})
    assert v.state["window"] == "50%"          # 记录开合度
    assert "window_set" not in v.state          # 不再落兜底键


def test_window_open_with_value_records_percentage():
    v = VAL()
    v.execute({"domain": "car_control", "intent": "window.open",
               "data": {"object": "window", "operate": "open", "value": 30}})
    assert v.state["window"] == "30%"


def test_window_open_without_value_stays_fully_open():
    v = VAL()
    v.execute({"domain": "car_control", "intent": "window.open",
               "data": {"object": "window", "operate": "open"}})
    assert v.state["window"] == "open"          # 不带程度仍视为全开


def test_media_play_sets_playing_not_fallback():
    v = VAL()
    v.execute({"domain": "media", "intent": "music.play",
               "data": {"object": "music", "operate": "play"}})
    assert v.state["media"] == "playing"
    assert "music_play" not in v.state         # 不再落兜底键


def test_aircon_wind_speed_set_via_mode_not_rejected():
    # fast_intent 本地路径用 mode=wind_speed（伪模式）；VAL 应接受而非判"暂不支持"，
    # 且话术用"档"而非温度的"度"。
    v = VAL()
    ok, speech = v.execute({"domain": "setting", "intent": "control",
                            "data": {"object": "aircon", "operate": "set",
                                     "mode": "wind_speed", "value": "3"}})
    assert ok is True
    assert v.state["hvac_wind_speed"] == 3
    assert "档" in speech and "度" not in speech


def test_aircon_wind_speed_set_without_value_no_keyerror():
    # set 无具体值不应抛 KeyError，回退到当前档
    v = VAL()
    ok, _ = v.execute({"domain": "setting", "intent": "control",
                       "data": {"object": "aircon", "operate": "set", "mode": "wind_speed"}})
    assert ok is True
    assert v.state["hvac_wind_speed"] == 1


def test_driving_mode_set_sport():
    v = VAL()
    ok, _ = v.execute({"domain": "setting", "intent": "control",
                       "data": {"object": "driving_mode", "operate": "set", "mode": "sport"}})
    assert ok is True
    assert v.state["driving_mode"] == "sport"


def test_window_inc_dec_steps_percentage():
    v = VAL()
    base = {"domain": "setting", "intent": "control"}
    v.execute({**base, "data": {"object": "window", "operate": "inc"}})
    assert v.state["window"] == "20%"            # 关→开大一点
    v.execute({**base, "data": {"object": "window", "operate": "inc"}})
    assert v.state["window"] == "40%"            # 再开大
    v.execute({**base, "data": {"object": "window", "operate": "dec"}})
    assert v.state["window"] == "20%"            # 关小一点


def test_headlight_off_blocked_while_driving():
    # 行驶中关大灯被安全门控拦截（夜间致盲），灯保持开
    v = VAL()
    v.set_env("speed_kmh", 60)
    v.state["headlight"] = True
    ok, _ = v.execute({"domain": "setting", "intent": "control",
                       "data": {"object": "headlight", "operate": "close"}})
    assert ok is False
    assert v.state["headlight"] is True


def test_headlight_on_allowed_while_driving():
    # 行驶中开大灯是安全正向动作，应放行
    v = VAL()
    v.set_env("speed_kmh", 60)
    ok, _ = v.execute({"domain": "setting", "intent": "control",
                       "data": {"object": "headlight", "operate": "open"}})
    assert ok is True
    assert v.state["headlight"] is True


def test_headlight_off_allowed_when_parked():
    v = VAL()
    v.state["headlight"] = True
    ok, _ = v.execute({"domain": "setting", "intent": "control",
                       "data": {"object": "headlight", "operate": "close"}})
    assert ok is True
    assert v.state["headlight"] is False


def test_battery_query_reports_level_without_mutation():
    # "还剩多少电量" → 本地回显真实电量，不改状态、不误答胎压
    v = VAL()
    before = dict(v.state)
    ok, speech = v.execute({"domain": "query", "intent": "query",
                            "data": {"object": "battery", "operate": "query"}})
    assert ok is True
    assert "72" in speech and "%" in speech
    assert "胎压" not in speech
    assert v.state == before


def test_ambient_set_color_turns_light_on():
    v = VAL()
    v.execute({"domain": "car_control", "intent": "ambient_light.set",
               "data": {"object": "ambient_light", "operate": "set", "tag": "orange"}})
    assert v.state["ambient_light_color"] == "orange"
    assert v.state["ambient_light"] is True    # 设色隐含开灯
