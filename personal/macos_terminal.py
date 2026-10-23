#!/usr/bin/env python3
"""Export a separate Terminal profile: keep Meta and German []{} characters."""
import copy
import argparse
import ctypes
import datetime
import pathlib
import plistlib
import subprocess
import sys

NAME = 'Herdr - Alt und Klammern'


class Preferences:
    """Use cfprefsd rather than editing a running application's plist on disk."""
    def __init__(self, domain='com.apple.Terminal'):
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        ptr = ctypes.c_void_p
        for name, args, result in (
            ('CFStringCreateWithCString', [ptr, ctypes.c_char_p, ctypes.c_uint32], ptr),
            ('CFDataCreate', [ptr, ptr, ctypes.c_long], ptr),
            ('CFDataGetLength', [ptr], ctypes.c_long),
            ('CFDataGetBytePtr', [ptr], ptr),
            ('CFPropertyListCreateWithData', [ptr, ptr, ctypes.c_ulong, ptr, ptr], ptr),
            ('CFPropertyListCreateData', [ptr, ptr, ctypes.c_long, ctypes.c_ulong, ptr], ptr),
            ('CFPreferencesCopyAppValue', [ptr, ptr], ptr),
            ('CFPreferencesSetAppValue', [ptr, ptr, ptr], None),
            ('CFPreferencesAppSynchronize', [ptr], ctypes.c_bool),
            ('CFRelease', [ptr], None),
        ):
            function = getattr(self.cf, name)
            function.argtypes, function.restype = args, result
        self.domain = self.string(domain)

    def string(self, value):
        return self.cf.CFStringCreateWithCString(None, value.encode('utf-8'), 0x08000100)

    def get(self, name):
        key = self.string(name)
        value = self.cf.CFPreferencesCopyAppValue(key, self.domain)
        self.cf.CFRelease(key)
        if not value:
            return None
        data = self.cf.CFPropertyListCreateData(None, value, 200, 0, None)
        try:
            return plistlib.loads(ctypes.string_at(self.cf.CFDataGetBytePtr(data), self.cf.CFDataGetLength(data)))
        finally:
            self.cf.CFRelease(data)
            self.cf.CFRelease(value)

    def set(self, name, value):
        raw = plistlib.dumps(value, fmt=plistlib.FMT_BINARY)
        data = self.cf.CFDataCreate(None, ctypes.c_char_p(raw), len(raw))
        obj = self.cf.CFPropertyListCreateWithData(None, data, 0, None, None)
        key = self.string(name)
        try:
            self.cf.CFPreferencesSetAppValue(key, obj, self.domain)
        finally:
            for ref in (key, obj, data):
                self.cf.CFRelease(ref)

    def sync(self):
        if not self.cf.CFPreferencesAppSynchronize(self.domain):
            raise RuntimeError('Terminal-Einstellungen konnten nicht gespeichert werden.')


def make_profile(destination):
    prefs = Preferences()
    name = prefs.get('Default Window Settings') or 'Basic'
    profile = copy.deepcopy((prefs.get('Window Settings') or {}).get(name))
    if profile is None:
        builtin = '/System/Applications/Utilities/Terminal.app/Contents/Resources/Initial Settings/Basic.terminal'
        profile = plistlib.loads(subprocess.check_output(['/usr/bin/plutil', '-convert', 'binary1', '-o', '-', builtin]))
    original = copy.deepcopy(profile)
    source = '/System/Applications/Utilities/Terminal.app/Contents/Resources/keyMappings.plist'
    keys = plistlib.loads(subprocess.check_output(['/usr/bin/plutil', '-convert', 'binary1', '-o', '-', source]))
    keys.update(profile.get('keyMapBoundKeys', {}))
    keys['~0035'] = '['
    keys['~0036'] = ']'
    keys['~0038'] = '{'
    keys['~0039'] = '}'
    keys['~006C'] = '@'
    # LF works in existing Codex sessions too, including legacy Terminal.app.
    keys['^000D'] = '\n'
    profile['keyMapBoundKeys'] = keys
    profile['useOptionAsMetaKey'] = True
    profile['name'] = NAME
    profile['type'] = 'Window Settings'
    # No startup commands: importing this file only opens a normal shell.
    profile.pop('CommandString', None)
    destination = pathlib.Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(plistlib.dumps(profile, fmt=plistlib.FMT_BINARY))
    for key, value in original.items():
        if key not in ('keyMapBoundKeys', 'useOptionAsMetaKey', 'name', 'type', 'CommandString'):
            assert profile[key] == value, key
    assert profile['keyMapBoundKeys']['~0035'] == '['
    assert profile['keyMapBoundKeys']['~0036'] == ']'
    assert profile['keyMapBoundKeys']['~0038'] == '{'
    assert profile['keyMapBoundKeys']['~0039'] == '}'
    assert profile['keyMapBoundKeys']['~006C'] == '@'
    assert profile['keyMapBoundKeys']['^000D'] == '\n'
    assert profile['useOptionAsMetaKey'] is True
    print(destination)


def install(destination):
    prefs = Preferences()
    keys = ('Window Settings', 'Default Window Settings', 'Startup Window Settings')
    before = {key: prefs.get(key) for key in keys}
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = pathlib.Path(destination).with_name('Terminal.before-herdr-' + stamp + '.plist')
    backup.write_bytes(plistlib.dumps({k: v for k, v in before.items() if v is not None}, fmt=plistlib.FMT_BINARY))
    backup.chmod(0o600)
    profiles = (before['Window Settings'] or {}).copy()
    profiles[NAME] = plistlib.loads(pathlib.Path(destination).read_bytes())
    prefs.set('Window Settings', profiles)
    prefs.set('Default Window Settings', NAME)
    prefs.set('Startup Window Settings', NAME)
    prefs.sync()
    assert prefs.get('Default Window Settings') == NAME
    assert prefs.get('Startup Window Settings') == NAME
    assert prefs.get('Window Settings')[NAME]['keyMapBoundKeys'] == profiles[NAME]['keyMapBoundKeys']
    print('Terminal: Standard- und Startprofil gesetzt. Terminal neu starten, falls es noch das alte Profil verwendet.')
    print('Sicherung:', backup)
    release_ctrl_return(pathlib.Path(destination).parent)


def release_ctrl_return(directory):
    # macOS 15+ reserves this key for context menus before Terminal sees it.
    library = ctypes.CDLL('/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight')
    get = library.CGSGetSymbolicHotKeyValue
    get.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_ushort),
                    ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(ctypes.c_ulong)]
    get.restype = ctypes.c_int
    character, code, flags = ctypes.c_ushort(), ctypes.c_ushort(), ctypes.c_ulong()
    result = get(159, ctypes.byref(character), ctypes.byref(code), ctypes.byref(flags))
    if result != 0 or (code.value, flags.value) != (36, 262144):
        print('Kein standardmäßiger macOS-Kontextmenü-Konflikt auf Ctrl+Return erkannt.')
        return
    prefs = Preferences('com.apple.symbolichotkeys')
    hotkeys = prefs.get('AppleSymbolicHotKeys') or {}
    backup = directory / 'Keyboard.before-herdr-ctrl-return.plist'
    if not backup.exists():
        backup.write_bytes(plistlib.dumps(hotkeys, fmt=plistlib.FMT_BINARY))
        backup.chmod(0o600)
    hotkeys['159'] = {'enabled': False, 'value': {'type': 'standard',
                       'parameters': [character.value, code.value, flags.value]}}
    prefs.set('AppleSymbolicHotKeys', hotkeys)
    prefs.sync()
    enable = library.CGSSetSymbolicHotKeyEnabled
    enable.argtypes, enable.restype = [ctypes.c_int, ctypes.c_bool], ctypes.c_int
    if enable(159, False) != 0:
        print('Tastaturänderung gespeichert; sie wird nach erneuter Anmeldung aktiv.')
    activate = pathlib.Path('/System/Library/PrivateFrameworks/SystemAdministration.framework/Resources/activateSettings')
    if activate.exists():
        subprocess.run([str(activate), '-u'], check=True)
    print('Ctrl+Return für Eingabe freigegeben. Terminal vollständig neu starten.')


if __name__ == '__main__':
    if sys.platform != 'darwin':
        raise SystemExit('Dieses Profil ist fuer macOS Terminal.app.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', nargs='?', type=pathlib.Path,
                        default=pathlib.Path.home() / '.config/herdr/Herdr.terminal')
    parser.add_argument('--install', action='store_true', help='Als Standard- und Startprofil speichern')
    args = parser.parse_args()
    make_profile(args.output)
    if args.install:
        install(args.output)
