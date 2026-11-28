# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 The Blueberry Pie OS Authors
# usage: python3 -m unittest discover -s tests

from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'usr/libexec/pie-boot-partition'


class BootPartitionTests(unittest.TestCase):
    def run_probe(self, root, slices, types, zfs=False):
        source = SCRIPT.read_text()
        self.assertTrue(source.endswith('\nmain\n'))
        definitions = source[:-len('\nmain\n')]
        with tempfile.TemporaryDirectory() as directory:
            calls = Path(directory) / 'probes'
            header = 'Filesystem 512-blocks Used Avail Capacity Mounted on\n'
            df = header + root + ' 100 10 90 10% /\n'
            mount = root + ' on / (' + ('zfs' if zfs else 'hfs') + ', local)\n'
            pool = 'pie\t100\t10\t90\n\tmirror-0\t100\t10\t90\n\t/dev/disk0s3\t-\t-\t-\n'
            vdev = '/dev/disk0s3' if zfs else root
            disk = re.sub(r's[0-9]+$', '', vdev)
            parts = [definitions,
                     'df() { printf %s ' + shlex.quote(df) + '; }',
                     'mount() { printf %s ' + shlex.quote(mount) + '; }',
                     'zpool() { [ "$*" = "list -H -P -v pie" ] || exit 8; printf %s ' + shlex.quote(pool) + '; }',
                     'boot_slices() { [ "$1" = ' + shlex.quote(disk) + ' ] || exit 9; printf %s ' + shlex.quote('\n'.join(slices) + '\n') + '; }',
                     'fstyp() { printf "%s\\n" "$1" >> ' + shlex.quote(str(calls)) + '; case "$1" in']
            for path, (kind, status) in types.items():
                parts.append(shlex.quote(path) + ') printf %s ' + shlex.quote(kind) + '; return ' + str(status) + ' ;;')
            parts.extend(['*) return 0 ;;', 'esac; }', 'main'])
            result = subprocess.run(['/bin/sh', '-c', '\n'.join(parts)], text=True, capture_output=True)
            probes = calls.read_text().splitlines() if calls.exists() else []
            return result, probes

    def test_pi_fat_first(self):
        result, probes = self.run_probe('/dev/disk0s2', ['/dev/disk0s1', '/dev/disk0s2'], {'/dev/disk0s1': ('msdos\n', 0)})
        self.assertEqual((result.returncode, result.stdout), (0, '/dev/disk0s1\n'))

    def test_rg_loader_then_fat(self):
        result, probes = self.run_probe('pie/root/1', ['/dev/disk0s1', '/dev/disk0s2', '/dev/disk0s3'], {'/dev/disk0s2': ('msdos\n', 1)}, zfs=True)
        self.assertEqual((result.returncode, result.stdout), (0, '/dev/disk0s2\n'))

    def test_empty_probe_success_is_not_fat(self):
        result, probes = self.run_probe('/dev/disk0s3', ['/dev/disk0s1', '/dev/disk0s2'], {})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('no FAT slice on /dev/disk0', result.stderr)

    def test_multiple_fat_volumes_rejected(self):
        result, probes = self.run_probe('/dev/disk0s3', ['/dev/disk0s1', '/dev/disk0s2'], {'/dev/disk0s1': ('msdos\n', 0), '/dev/disk0s2': ('msdos\n', 1)})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('both FAT', result.stderr)

    def test_other_disks_are_not_probed(self):
        result, probes = self.run_probe('/dev/disk0s3', ['/dev/disk1s1', '/dev/disk0s2', '/dev/disk01s1'], {'/dev/disk1s1': ('msdos\n', 0), '/dev/disk0s2': ('msdos\n', 1)})
        self.assertEqual((result.returncode, result.stdout), (0, '/dev/disk0s2\n'))
        self.assertEqual(probes, ['/dev/disk0s2'])

    def test_nested_and_non_numeric_slices_not_probed(self):
        result, probes = self.run_probe('/dev/disk0s3', ['/dev/disk0s2s1', '/dev/disk0s2.new', '/dev/disk0s2'], {'/dev/disk0s2': ('msdos\n', 1)})
        self.assertEqual(result.stdout, '/dev/disk0s2\n')
        self.assertEqual(probes, ['/dev/disk0s2'])

    def test_unsupported_root_rejected(self):
        result, probes = self.run_probe('root_device', ['/dev/disk0s1'], {'/dev/disk0s1': ('msdos\n', 0)})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(probes, [])

    def test_malformed_disk_root_rejected(self):
        for root in ['/dev/diskxs2', '/dev/disk0s', '/dev/disk0s2s1', '/dev/disk0s2/extra', '/dev/disk0:s2', '/dev/disk0s2:3', '/dev/disk0s:3']:
            with self.subTest(root=root):
                result, probes = self.run_probe(root, ['/dev/disk0s1'], {'/dev/disk0s1': ('msdos\n', 0)})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(probes, [])

    def test_empty_slice_set_rejected(self):
        result, probes = self.run_probe('/dev/disk0s2', [], {})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(probes, [])

    def test_multi_digit_disk_number(self):
        result, probes = self.run_probe('/dev/disk10s12', ['/dev/disk1s1', '/dev/disk10s2'], {'/dev/disk10s2': ('msdos\n', 1)})
        self.assertEqual((result.returncode, result.stdout), (0, '/dev/disk10s2\n'))


class BootSlicesTests(unittest.TestCase):
    def slices(self, prefix):
        source = SCRIPT.read_text()
        definitions = source[:-len('\nmain\n')]
        script = definitions + '\nboot_slices ' + shlex.quote(prefix) + '\n'
        result = subprocess.run(['/bin/sh', '-c', script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        return result.stdout.splitlines()

    def test_only_block_slices_of_the_disk(self):
        block = next((d for d in ('/dev/loop0', '/dev/sda') if Path(d).is_block_device()), None)
        if block is None:
            self.skipTest('no block device on this host')
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            for name in ('disk0s1', 'disk0s10', 'disk1s1', 'disk0', 'disk0x1'):
                (d / name).symlink_to(block)
            (d / 'disk0s2').write_text('')
            self.assertEqual(self.slices(str(d / 'disk0')), [str(d / 'disk0s1'), str(d / 'disk0s10')])

    def test_no_slices(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.slices(str(Path(directory) / 'disk0')), [])


if __name__ == '__main__':
    unittest.main()
