"""Verify the actual r8s build, including syscall pointers in the raw Image."""
from pathlib import Path
import hashlib
import json
import re
import struct
import subprocess
import sys

BASE = '16df24ea7b48eb426fce2ff6b751585cc81a19a4'
CHARGING = ('drivers/battery_v2/sec_battery.c',
            'drivers/battery_v2/sec_battery_sysfs.c',
            'drivers/battery_v2/sec_step_charging.c',
            'drivers/battery_v2/include/sec_battery.h',
            'arch/arm64/boot/dts/samsung/battery_data_r8_common.dtsi')


def source():
    for name in CHARGING:
        original = subprocess.check_output(['git', 'show', BASE + ':' + name])
        if Path(name).read_bytes() != original:
            raise ValueError('Charging source changed: ' + name)
    battery = Path(CHARGING[0]).read_text()
    if battery.count('battery->charge_max_mode = true;') != 1:
        raise ValueError('Original V3 must start enabled exactly once')
    required = {
        'fs/file.c': 'SYSCALL_DEFINE3(close_range,',
        'include/uapi/asm-generic/unistd.h': '#define __NR_close_range 436',
        'arch/arm64/include/asm/unistd32.h': '__SYSCALL(__NR_close_range, sys_close_range)',
        'fs/exec.c': 'ksu_handle_execveat(&fd, &filename, &argv, &envp, &flags);',
        'fs/read_write.c': 'ksu_handle_sys_read(fd);',
        'fs/stat.c': 'ksu_handle_newfstat_ret(&fd, &statbuf);',
    }
    for name, marker in required.items():
        if marker not in Path(name).read_text():
            raise ValueError('Missing syscall/manual-hook integration: ' + name)
    if 'ksu_handle_fstat64_ret(&fd, &statbuf);' not in Path('fs/stat.c').read_text():
        raise ValueError('Missing compat fstat64 RC size hook')
    print('Source: original V3/charging DTS unchanged; close_range and manual hooks registered')


def output():
    image = Path('out/arch/arm64/boot/Image').read_bytes()
    if image[56:60] != b'ARMd':
        raise ValueError('Expected raw ARM64 Image')
    symbols = {}
    for line in Path('out/System.map').read_text().splitlines():
        parts = line.split()
        if len(parts) == 3:
            symbols[parts[2]] = int(parts[0], 16)
    handler = symbols['__arm64_sys_close_range']
    for name in ('sys_call_table', 'compat_sys_call_table'):
        offset = symbols[name] - symbols['_text'] + 436 * 8
        if not 0 <= offset <= len(image) - 8:
            raise ValueError('Syscall table outside raw Image: ' + name)
        if struct.unpack_from('<Q', image, offset)[0] != handler:
            raise ValueError('Syscall 436 does not point at close_range: ' + name)
    config = Path('out/.config').read_text()
    for name in ('MODEL_R8S', 'KSU', 'KSU_MANUAL_HOOK', 'COMPAT', 'SND_USB_AUDIO', 'CNSS_QCA6390'):
        if not re.search(r'^CONFIG_' + name + '=y$', config, re.M):
            raise ValueError('Missing r8s kernel option: ' + name)
    for name in ('ksu_handle_execveat', 'ksu_handle_sys_read',
                 'ksu_handle_newfstat_ret', 'ksu_handle_fstat64_ret'):
        if name not in symbols:
            raise ValueError('Missing compiled manual-hook handler: ' + name)
    print('Image: syscall 436 points at the real handler in ARM64 and compat tables; r8s/KSU/audio/Wi-Fi present')


def manifest():
    root = Path('out/cos17-artifacts')
    data = {
        'kernel_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'base_commit': BASE,
        'ksu_commit': subprocess.check_output(['git', '-C', 'KernelSU-Next', 'rev-parse', 'HEAD'], text=True).strip(),
        'target': 'SM-G780F/r8s', 'charge_max_version': 'original V3',
        'charge_max_default': True, 'close_range_arm64_and_compat': True,
        'manual_hooks_native_compat_and_init_rc': True,
        'flashable': False,
        'packaging': 'Repack with the COS header/ramdisk; never flash a generated One UI ramdisk',
        'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(root.iterdir()) if p.is_file() and p.name != 'manifest.json'},
    }
    (root / 'manifest.json').write_text(json.dumps(data, indent=2) + '\n')
    print(json.dumps(data, indent=2))


if __name__ == '__main__':
    {'source': source, 'output': output, 'manifest': manifest}[sys.argv[1]]()
