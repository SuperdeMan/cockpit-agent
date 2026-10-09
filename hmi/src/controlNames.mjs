// Shared HMI / Android presentation. Extracted without changing the existing evidence policy.
// mobile/src/core/cards/controlNames.ts
// 车控意图名 → 人话（v3 P4c，D17）：车控结果卡与执行回执的「执行」行共用，不再把 `vehicle.control` 这种动作类型给人看。
//
// **对象中文名的声明源是 `orchestrator/edge/knowledge/commands.yaml`**：每个对象的 `edge_intents` 列出它的意图名，
// `display_name` 是中文名。手机端读不到端侧知识库，`OBJECT_NAME` 是一份**对账过的副本**——
// test/controlNames.test.ts 逐条解析 commands.yaml，断言每个端侧意图都按首段落到它所属对象的 display_name，
// 也断言表里每个键都还在声明源里（删对象不会在这里留一个死名字）。首段与对象键不同名的只有两处：
// `hvac.*` → aircon、`tire_pressure.query` → tire_pressure_monitoring（capability_meta.derive_edge_intents 的 docstring）。
//
// 子功能（三段式意图的中段）与动作（末段）是纯展示词表，不是执行判据；子功能表由测试钉住「每个三段式端侧意图都有名字」。
// 零 RN import。
/** 意图名首段 → 对象中文名（= commands.yaml 对象的 display_name） */
export const OBJECT_NAME = {
    seat: '座椅',
    window: '车窗',
    sunroof: '天窗',
    sunshade: '遮阳帘',
    aircon: '空调',
    hvac: '空调',
    ambient_light: '氛围灯',
    headlight: '大灯',
    warning_light: '双闪',
    trunk: '后备箱',
    door_lock: '车门锁',
    fuel_tank_cover: '油箱盖',
    charging_port: '充电口盖',
    rear_view_mirror: '后视镜',
    steering_wheel: '方向盘',
    wiper: '雨刮',
    fragrance: '香氛',
    tire_pressure: '胎压监测',
    dashcam: '行车记录仪',
    scene_mode: '情景模式',
    power_mode: '动力模式',
    energy_recovery: '能量回收',
    lane_departure_assistance: '车道偏离预警',
    lane_assistance: '车道保持辅助',
    front_defogger: '前挡除雾',
    rear_defogger: '后挡除雾',
    accompany_home: '伴我回家灯光',
    volume: '音量',
    screen: '屏幕',
    media: '媒体',
};
/** 三段式意图的中段（对象的子功能）→ 中文：`seat.heating.on` ⇒「座椅加热」 */
export const FEATURE_NAME = {
    heating: '加热',
    ventilation: '通风',
    massage: '按摩',
    lumbar_support: '腰托',
    wind_speed: '风量',
    speed: '速度',
    height: '高度',
    brightness: '亮度',
};
/** 意图名末段 → 动作词（展示用；执行语义在端侧 VAL） */
export const ACTION_WORD = {
    on: '打开',
    open: '打开',
    off: '关闭',
    close: '关闭',
    set: '设为',
    inc: '调高',
    dec: '调低',
    fold: '折叠',
    unfold: '展开',
    mute: '静音',
    unmute: '取消静音',
    query: '查询',
    play: '播放',
    pause: '暂停',
    stop: '停止',
    next: '切到下一首',
    prev: '切到上一首',
};
function segments(command) {
    return String(command || '').trim().split('.').filter(Boolean);
}
/** 意图名 → 对象中文名；认不出返回空串（调用方回落「车辆操作」，不把机器名给人看） */
export function controlObjectName(command) {
    return OBJECT_NAME[segments(command)[0] ?? ''] ?? '';
}
/** 三段式意图的子功能名（`seat.heating.on` ⇒「加热」）；两段式或认不出 ⇒ 空串 */
export function controlFeatureName(command) {
    const s = segments(command);
    return s.length >= 3 ? FEATURE_NAME[s[1]] ?? '' : '';
}
/** 意图名 → 动作词（末段）；认不出 ⇒ 空串 */
export function controlActionWord(command) {
    const s = segments(command);
    return s.length >= 2 ? ACTION_WORD[s[s.length - 1]] ?? '' : '';
}
