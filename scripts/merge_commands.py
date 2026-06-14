"""合并飞书新对象到 commands.yaml（保留旧 YAML 的干净结构）。"""
import yaml, sys
sys.stdout.reconfigure(encoding='utf-8')

with open('orchestrator/edge/knowledge/commands.yaml', encoding='utf-8') as f:
    c = yaml.safe_load(f)
objects = c.get('objects', {})
print(f'Current objects: {len(objects)}')

new_objects = {
    'bluetooth': {'operates': ['open', 'close', 'connect', 'disconnect', 'query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'wifi': {'operates': ['open', 'close', 'connect', 'disconnect'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'hotspot': {'operates': ['open', 'close'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'phone': {'operates': ['open', 'close', 'query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'contacts': {'operates': ['open', 'close', 'query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'call_log': {'operates': ['open', 'close'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'radio': {'operates': ['open', 'close', 'play', 'set'], 'attrs': [], 'modes': ['FM', 'AM'], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'online_radio': {'operates': ['open', 'close', 'play'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'online_only', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'music': {'operates': ['play', 'pause', 'stop', 'switch', 'resume', 'close', 'query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'audiobook': {'operates': ['play', 'pause', 'stop', 'switch'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'online_only', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'opera': {'operates': ['play', 'stop', 'switch'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'online_only', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'news': {'operates': ['play', 'stop', 'query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'online_only', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'video': {'operates': ['play', 'pause', 'stop', 'resume', 'close', 'set'], 'attrs': [], 'modes': ['full_screen'], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'TV': {'operates': ['open', 'close'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'equalizer': {'operates': ['set', 'switch', 'open', 'close'], 'attrs': [], 'modes': ['rock', 'pop', 'classical', 'jazz', 'country', 'custom', 'standard', 'original'], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'voice_assistant': {'operates': ['open', 'close', 'stop'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'system': {'operates': ['open', 'close', 'set', 'query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'surround_view': {'operates': ['open', 'close'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'dashboard': {'operates': ['open', 'close', 'set', 'query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'auto_hold': {'operates': ['open', 'close'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': True, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'epb': {'operates': ['open', 'close'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': True, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'frunk': {'operates': ['open', 'close'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': True, 'voice_forbidden': False, 'projects': []},
    'interaction': {'operates': ['confirm', 'cancel', 'select', 'prev', 'next'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'offline_ok', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'navigation': {'operates': ['plan', 'cancel', 'start', 'resume', 'locate', 'query', 'open', 'close', 'set'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'online_only', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'map': {'operates': ['open', 'close', 'set'], 'attrs': [], 'modes': ['2D', '3D', 'satellite'], 'positions': False, 'units': [], 'online': 'online_only', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'food': {'operates': ['query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'online_only', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'hotel': {'operates': ['query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'online_only', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'flight': {'operates': ['query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'online_only', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'train': {'operates': ['query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'online_only', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
    'stock': {'operates': ['query'], 'attrs': [], 'modes': [], 'positions': False, 'units': [], 'online': 'online_only', 'drive_restricted': False, 'require_confirm': False, 'voice_forbidden': False, 'projects': []},
}

for name, defn in new_objects.items():
    if name not in objects:
        objects[name] = defn

c['objects'] = objects
with open('orchestrator/edge/knowledge/commands.yaml', 'w', encoding='utf-8') as f:
    f.write('# commands.yaml — 车控命令 schema（来源：同行者公版语音指令表 6.1）\n')
    f.write('# 每个 object 声明：operates/attrs/modes/positions/units/online/drive_restricted/require_confirm/voice_forbidden\n\n')
    yaml.dump(c, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print(f'Updated objects: {len(objects)}')
