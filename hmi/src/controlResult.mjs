// Shared HMI / Android presentation. Extracted without changing the existing evidence policy.
import { readResultBundles } from './resultBundle.mjs';
import { controlActionWord, controlFeatureName, controlObjectName } from './controlNames.mjs';
export const STATUS_WORD = {
    executed: '已执行',
    verified: '已核实',
    unchanged: '本来就是',
    unverified: '未核实',
    failed: '没生效',
    running: '执行中',
};
const CJK = /[一-鿿]/;
/** 开度类对象：端侧把「开一半」解析成 unit=percent 的 value（fast_intent 车窗 / 天窗 / 遮阳帘分支） */
const PERCENT_OBJECTS = new Set(['window', 'sunroof', 'sunshade']);
function str(v) {
    if (typeof v === 'number' && Number.isFinite(v))
        return String(v);
    return typeof v === 'string' ? v.trim() : '';
}
function isNumeric(v) {
    return /^-?\d+(\.\d+)?$/.test(v);
}
function valueOf(command, payload) {
    const temp = str(payload.temperature) || str(payload.temp);
    if (temp && isNumeric(temp))
        return { value: `${temp}°C`, temperature: true };
    const raw = str(payload.value);
    const [head, mid] = command.split('.');
    if (raw && mid === 'wind_speed')
        return { value: `${raw} 档`, temperature: false };
    if (raw && PERCENT_OBJECTS.has(head) && isNumeric(raw))
        return { value: `${raw}%`, temperature: false };
    const mode = str(payload.mode);
    if (mode && CJK.test(mode))
        return { value: mode, temperature: false };
    return { value: raw, temperature: false };
}
/** 位置只取人话（「主驾」「副驾驶」）：端侧快路径带的是原话里的位置词；协议标识（front_left）不给人看 */
function positionsOf(payload) {
    const raw = payload.positions;
    const list = Array.isArray(raw) ? raw : typeof raw === 'string' ? [raw] : [];
    return list.map(str).filter((x) => x && CJK.test(x)).join('、');
}
export function controlStatusOf(row, flags) {
    if (flags.error)
        return 'unverified';
    if (!row)
        return flags.live ? 'running' : 'executed';
    if (row.status === 'failed')
        return 'failed';
    const ev = row.evidence;
    if (!ev)
        return 'executed';
    if (ev.verified)
        return 'verified';
    if (ev.state === 'unsatisfied')
        return 'failed';
    if (ev.state === 'satisfied' && ev.observed === 'unchanged')
        return 'unchanged';
    return 'unverified';
}
function isControlAction(a) {
    return !!a && typeof a.type === 'string' && (a.type.startsWith('vehicle.control') || a.type.startsWith('media.control'));
}
/** 一轮里的车控 / 媒体控制动作 → 结果项（顺序同 action 帧）；没有就空数组 */
export function controlItems(msg) {
    const actions = (msg.actions || []).filter(isControlAction);
    if (!actions.length)
        return [];
    const rows = readResultBundles({ result_bundles: msg.resultBundles }).flatMap((b) => b.results);
    const used = new Set();
    const flags = { error: !!msg.error, live: !!(msg.pending || msg.streaming) };
    return actions.map((a) => {
        const payload = (a.payload && typeof a.payload === 'object' ? a.payload : {});
        const command = str(payload.command);
        const row = rows.find((r) => !used.has(r) && command && r.intent === command);
        if (row)
            used.add(row);
        const object = controlObjectName(command);
        const { value, temperature } = valueOf(command, payload);
        const status = controlStatusOf(row, flags);
        return {
            kind: a.type.startsWith('media.control') ? 'media' : 'vehicle',
            command,
            object,
            label: object ? `${positionsOf(payload)}${object}${controlFeatureName(command)}` : '',
            action: controlActionWord(command),
            value,
            temperature,
            status,
            note: status === 'unverified' && row?.pending_edge ? row.answer : '',
            sourceKind: row?.evidence?.source_kind || '',
        };
    });
}
/** 一组结果的总状态（回执一行用）：最需要人留意的那个——没生效 > 未核实 > 执行中 > 已执行 > 本来就是 > 已核实 */
export function overallStatus(items) {
    const order = ['failed', 'unverified', 'running', 'executed', 'unchanged', 'verified'];
    return order.find((s) => items.some((i) => i.status === s)) ?? null;
}
/** 给人看的名字：认不出对象就说「车辆操作」，不显示机器名 */
export function itemName(item) {
    return item.label || (item.kind === 'media' ? '媒体' : '车辆操作');
}
