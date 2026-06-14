#!/usr/bin/env python3
"""Generate entities.yaml from Feishu 词库 data."""
import json
import glob
import sys
import os

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(BASE_DIR, 'orchestrator', 'edge', 'knowledge', 'entities.yaml')

# ── Load all entity pages ──
all_records = []
for f in sorted(glob.glob(os.path.join(BASE_DIR, 'feishu_tblDLspoGsO4Iu4w_*.json'))):
    with open(f, encoding='utf-8') as fh:
        d = json.load(fh)
        all_records.extend(d['data']['data'])

# Field indices (from feishu fields):
# 0: 词库名称  4: 词库属性  5: 协议标识  7: 父词库  8: 主词  11: 主词别称


def parse_aliases(raw):
    """Split alias field (newline-separated) into list, strip whitespace."""
    if not raw:
        return []
    return [a.strip() for a in str(raw).strip().split('\n') if a.strip()]


def parse_main_words(raw):
    """Split main word field (newline-separated) into list."""
    if not raw:
        return []
    return [w.strip() for w in str(raw).strip().split('\n') if w.strip()]


# ── Collect entities with protocol IDs ──
# Each entry: (main_words, aliases, proto_id, parent)
entities = []
for r in all_records:
    cat = r[4]
    if not isinstance(cat, list):
        continue
    proto_id = r[5]
    if not proto_id or proto_id == '{$origin}':
        continue
    main_words = parse_main_words(r[8])
    aliases = parse_aliases(r[11])
    parent = r[7]  # 父词库
    if main_words:
        entities.append({
            'main_words': main_words,
            'aliases': aliases,
            'proto_id': proto_id,
            'parent': parent,
            'category_tags': cat,
        })

# ── Build parent → children index ──
parent_children = {}
for e in entities:
    p = e['parent']
    if p:
        parent_children.setdefault(p, []).append(e)

# ── Category mapping: parent name → our YAML section ──
# Positions
POSITION_PARENTS = {
    '<基础位置>', '<扩展位置>', '<空调位置>', '<司乘区域>',
    '<前后位置>', '<车道位置>', '<温区位置>',
}
# Wind / aircon modes
WIND_MODE_PARENTS = {'<出风模式>'}
AIRCON_MODE_PARENTS = {'<空调模式>', '<空调个性化模式>', '<除霜雾湿模式>', '<制热制冷模式>', '<除雾模式>', '<除霜模式>'}
# Seat modes
SEAT_MODE_PARENTS = {'<座椅模式>', '<座椅加热模式>', '<座椅通风模式>', '<按摩模式>', '<座椅记忆模式>'}
# Driving modes
DRIVING_MODE_PARENTS = {'<驾驶模式>'}
# Energy modes
ENERGY_MODE_PARENTS = {'<能源模式>'}
# Light colors
LIGHT_COLOR_PARENTS = {'<氛围灯颜色>'}
# Light modes
LIGHT_MODE_PARENTS = {'<双色氛围灯模式>', '<动态氛围灯模式>', '<灯光效果>', '<流光饰板格式>', '<灯语模式>', '<智能迎宾>'}
# HUD display modes
HUD_DISPLAY_PARENTS = {'<hud信息显示>'}
# Rearview mirror modes
MIRROR_MODE_PARENTS = {'<后视镜模式>'}
# Blind spot monitoring modes
BLIND_SPOT_PARENTS = {'<盲区监测模式>', '<盲区监测提醒方式>'}
# Safety driving monitoring
SAFETY_MONITOR_PARENTS = {'<安全驾驶监测>'}
# Single pedal mode
SINGLE_PEDAL_PARENTS = {'<单踏板模式>'}
# Reverse warning
REVERSE_WARNING_PARENTS = {'<倒车行人警示模式>'}
# Steering wheel heating mode
STEERING_WHEEL_PARENTS = {'<方向盘加热模式>'}
# Map view
MAP_VIEW_PARENTS = {'<地图视角>'}
# Screen type
SCREEN_TYPE_PARENTS = {'<屏幕类型>'}
# Fragrance
FRAGRANCE_PARENTS = {'<香氛浓度>', '<香氛类型>'}
# Broadcast style
BROADCAST_STYLE_PARENTS = {'<播报风格>', '<大模型播报模式>'}
# Navigation broadcast
NAV_BROADCAST_PARENTS = {'<导航播报模式>'}
# Team control
TEAM_PARENTS = {'<队列控制>'}
# Image settings
IMAGE_PARENTS = {'<影像设置>'}
# Nozzle heating
NOZZLE_PARENTS = {'<喷嘴加热模式>'}
# Shout mode
SHOUT_PARENTS = {'<喊话模式>'}
# Units
UNIT_CATS = {'单位'}
# Operations (verbs)
VERB_CATS = {'动词'}

# ── Category name mapping ──
CATEGORY_NAMES = {
    'positions': 'positions',
    'wind_modes': 'wind_modes',
    'aircon_modes': 'aircon_modes',
    'seat_modes': 'seat_modes',
    'driving_modes': 'driving_modes',
    'energy_modes': 'energy_modes',
    'light_colors': 'light_colors',
    'light_modes': 'light_modes',
    'hud_display': 'hud_display',
    'mirror_modes': 'mirror_modes',
    'blind_spot_modes': 'blind_spot_modes',
    'safety_monitor': 'safety_monitor',
    'single_pedal_modes': 'single_pedal_modes',
    'reverse_warning_modes': 'reverse_warning_modes',
    'steering_wheel_modes': 'steering_wheel_modes',
    'map_views': 'map_views',
    'screen_types': 'screen_types',
    'fragrance': 'fragrance',
    'broadcast_styles': 'broadcast_styles',
    'nav_broadcast_modes': 'nav_broadcast_modes',
    'team_control': 'team_control',
    'image_settings': 'image_settings',
    'nozzle_modes': 'nozzle_modes',
    'shout_modes': 'shout_modes',
    'units': 'units',
    'operations': 'operations',
}


def collect_from_parents(parent_set, all_ents):
    """Collect entities whose parent is in the given set."""
    result = []
    for e in all_ents:
        if e['parent'] in parent_set:
            result.append(e)
    return result


def collect_from_category(cat_name, all_ents):
    """Collect entities whose category_tags contain the given name."""
    result = []
    for e in all_ents:
        if cat_name in e['category_tags']:
            result.append(e)
    return result


def build_entries(ents):
    """Build a dict of word → proto_id from entities.
    Main words map to proto_id. Aliases also map to proto_id.
    If multiple main words, each gets its own entry.
    """
    entries = {}
    for e in ents:
        pid = e['proto_id']
        for w in e['main_words']:
            entries[w] = pid
        for a in e['aliases']:
            entries[a] = pid
    return entries


def write_section(f, section_name, entries, comment=None):
    """Write a YAML section. Skip if entries is empty."""
    if not entries:
        return
    if comment:
        f.write(f'\n# ── {comment} ──\n')
    f.write(f'{section_name}:\n')
    for word, pid in entries.items():
        # Escape YAML special chars
        word_str = str(word)
        if any(c in word_str for c in ':#{}[]&*!|>\'@"`'):
            word_str = f'"{word_str}"'
        pid_str = str(pid)
        if any(c in pid_str for c in ':#{}[]&*!|>\'@"`'):
            pid_str = f'"{pid_str}"'
        f.write(f'  {word_str}: {pid_str}\n')


# ── Generate entities.yaml ──
with open(OUTPUT, 'w', encoding='utf-8') as f:
    f.write('# entities.yaml — 实体归一化字典（来源：同行者公版语音指令表 6.1 词库）\n')
    f.write('# 自然语言 → 协议标识；主词 + 别称均映射到同一标识\n')
    f.write('# Auto-generated by scripts/generate_entities.py — DO NOT EDIT MANUALLY\n')

    # ── Positions ──
    pos_ents = collect_from_parents(POSITION_PARENTS, entities)
    pos_entries = build_entries(pos_ents)
    write_section(f, 'positions', pos_entries, '位置')

    # ── Wind modes ──
    wind_ents = collect_from_parents(WIND_MODE_PARENTS, entities)
    wind_entries = build_entries(wind_ents)
    write_section(f, 'wind_modes', wind_entries, '出风模式')

    # ── Aircon modes ──
    aircon_ents = collect_from_parents(AIRCON_MODE_PARENTS, entities)
    aircon_entries = build_entries(aircon_ents)
    write_section(f, 'aircon_modes', aircon_entries, '空调模式')

    # ── Seat modes ──
    seat_ents = collect_from_parents(SEAT_MODE_PARENTS, entities)
    seat_entries = build_entries(seat_ents)
    write_section(f, 'seat_modes', seat_entries, '座椅模式')

    # ── Driving modes ──
    drive_ents = collect_from_parents(DRIVING_MODE_PARENTS, entities)
    drive_entries = build_entries(drive_ents)
    write_section(f, 'driving_modes', drive_entries, '驾驶模式')

    # ── Energy modes ──
    energy_ents = collect_from_parents(ENERGY_MODE_PARENTS, entities)
    energy_entries = build_entries(energy_ents)
    write_section(f, 'energy_modes', energy_entries, '能源模式')

    # ── Light colors ──
    color_ents = collect_from_parents(LIGHT_COLOR_PARENTS, entities)
    color_entries = build_entries(color_ents)
    write_section(f, 'light_colors', color_entries, '氛围灯颜色')

    # ── Light modes ──
    light_mode_ents = collect_from_parents(LIGHT_MODE_PARENTS, entities)
    light_mode_entries = build_entries(light_mode_ents)
    write_section(f, 'light_modes', light_mode_entries, '灯光模式')

    # ── HUD display ──
    hud_ents = collect_from_parents(HUD_DISPLAY_PARENTS, entities)
    hud_entries = build_entries(hud_ents)
    write_section(f, 'hud_display', hud_entries, 'HUD 显示')

    # ── Mirror modes ──
    mirror_ents = collect_from_parents(MIRROR_MODE_PARENTS, entities)
    mirror_entries = build_entries(mirror_ents)
    write_section(f, 'mirror_modes', mirror_entries, '后视镜模式')

    # ── Blind spot modes ──
    blind_ents = collect_from_parents(BLIND_SPOT_PARENTS, entities)
    blind_entries = build_entries(blind_ents)
    write_section(f, 'blind_spot_modes', blind_entries, '盲区监测')

    # ── Safety monitor ──
    safety_ents = collect_from_parents(SAFETY_MONITOR_PARENTS, entities)
    safety_entries = build_entries(safety_ents)
    write_section(f, 'safety_monitor', safety_entries, '安全驾驶监测')

    # ── Single pedal modes ──
    pedal_ents = collect_from_parents(SINGLE_PEDAL_PARENTS, entities)
    pedal_entries = build_entries(pedal_ents)
    write_section(f, 'single_pedal_modes', pedal_entries, '单踏板模式')

    # ── Reverse warning modes ──
    reverse_ents = collect_from_parents(REVERSE_WARNING_PARENTS, entities)
    reverse_entries = build_entries(reverse_ents)
    write_section(f, 'reverse_warning_modes', reverse_entries, '倒车行人警示')

    # ── Steering wheel modes ──
    sw_ents = collect_from_parents(STEERING_WHEEL_PARENTS, entities)
    sw_entries = build_entries(sw_ents)
    write_section(f, 'steering_wheel_modes', sw_entries, '方向盘加热模式')

    # ── Map views ──
    map_ents = collect_from_parents(MAP_VIEW_PARENTS, entities)
    map_entries = build_entries(map_ents)
    write_section(f, 'map_views', map_entries, '地图视角')

    # ── Screen types ──
    screen_ents = collect_from_parents(SCREEN_TYPE_PARENTS, entities)
    screen_entries = build_entries(screen_ents)
    write_section(f, 'screen_types', screen_entries, '屏幕类型')

    # ── Fragrance ──
    frag_ents = collect_from_parents(FRAGRANCE_PARENTS, entities)
    frag_entries = build_entries(frag_ents)
    write_section(f, 'fragrance', frag_entries, '香氛')

    # ── Broadcast styles ──
    bc_ents = collect_from_parents(BROADCAST_STYLE_PARENTS, entities)
    bc_entries = build_entries(bc_ents)
    write_section(f, 'broadcast_styles', bc_entries, '播报风格')

    # ── Nav broadcast modes ──
    nav_ents = collect_from_parents(NAV_BROADCAST_PARENTS, entities)
    nav_entries = build_entries(nav_ents)
    write_section(f, 'nav_broadcast_modes', nav_entries, '导航播报模式')

    # ── Team control ──
    team_ents = collect_from_parents(TEAM_PARENTS, entities)
    team_entries = build_entries(team_ents)
    write_section(f, 'team_control', team_entries, '队列控制')

    # ── Image settings ──
    img_ents = collect_from_parents(IMAGE_PARENTS, entities)
    img_entries = build_entries(img_ents)
    write_section(f, 'image_settings', img_entries, '影像设置')

    # ── Nozzle modes ──
    nozzle_ents = collect_from_parents(NOZZLE_PARENTS, entities)
    nozzle_entries = build_entries(nozzle_ents)
    write_section(f, 'nozzle_modes', nozzle_entries, '喷嘴加热模式')

    # ── Shout modes ──
    shout_ents = collect_from_parents(SHOUT_PARENTS, entities)
    shout_entries = build_entries(shout_ents)
    write_section(f, 'shout_modes', shout_entries, '喊话模式')

    # ── Units (from 词库属性=单位) ──
    unit_ents = collect_from_category('单位', entities)
    unit_entries = build_entries(unit_ents)
    write_section(f, 'units', unit_entries, '单位')

    # ── Operations (from 词库属性=动词) ──
    verb_ents = collect_from_category('动词', entities)
    verb_entries = build_entries(verb_ents)
    write_section(f, 'operations', verb_entries, '操作/动词')

print(f'Generated {OUTPUT}')
print(f'Total entity records processed: {len(all_records)}')
print(f'Entities with protocol IDs: {len(entities)}')
