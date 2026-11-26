"""Regression checks for exact timestamps and complete capture output."""

import array
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
import zlib

from get_images import ExactImageSet, save_images, stamp_key, write_png


def image(nanosec=1, pixels=b'\xff\x00\x00', step=3, width=1):
    return SimpleNamespace(
        header=SimpleNamespace(stamp=SimpleNamespace(sec=42, nanosec=nanosec), frame_id='camera'),
        encoding='rgb8', width=width, height=1, step=step, data=array.array('B', pixels))


class ExactCaptureTests(unittest.TestCase):
    def test_one_nanosecond_difference_never_matches(self):
        matcher = ExactImageSet(['a', 'b'], 4)
        self.assertIsNone(matcher.add('a', image(100)))
        self.assertIsNone(matcher.add('b', image(101)))
        match = matcher.add('b', image(100))
        self.assertEqual({stamp_key(msg) for msg in match.values()}, {(42, 100)})

    def test_out_of_order_arrivals_wait_for_every_camera(self):
        matcher = ExactImageSet(['a', 'b', 'c'], 4)
        self.assertIsNone(matcher.add('a', image(2)))
        self.assertIsNone(matcher.add('a', image(1)))
        self.assertIsNone(matcher.add('c', image(1)))
        match = matcher.add('b', image(1))
        self.assertEqual(set(match), {'a', 'b', 'c'})
        self.assertEqual({stamp_key(msg) for msg in match.values()}, {(42, 1)})

    def test_missing_camera_keeps_buffers_bounded(self):
        matcher = ExactImageSet(['a', 'b'], 2)
        for timestamp in range(1, 100):
            self.assertIsNone(matcher.add('a', image(timestamp)))
        self.assertEqual(list(matcher.buffers['a']), [(42, 98), (42, 99)])
        self.assertIsNone(matcher.add('b', image(1)))

    def test_zero_stamps_are_not_synchronized_frames(self):
        matcher = ExactImageSet(['a', 'b'], 2)
        msg = image(0)
        msg.header.stamp.sec = 0
        self.assertIsNone(matcher.add('a', msg))
        self.assertIsNone(matcher.add('b', msg))

    def test_manifest_preserves_each_original_stamp(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            messages = {'a': image(123), 'b': image(123)}
            output = save_images(messages, directory, {'a': '/a', 'b': '/b'}, Path('rig.json'), 'reliable')
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['timestamp'], {'sec': 42, 'nanosec': 123})
            self.assertEqual(len(list(output.glob('*.png'))), 2)
            for entry in manifest['images']:
                self.assertEqual(entry['header_stamp'], manifest['timestamp'])
            with self.assertRaises(FileExistsError):
                save_images(messages, directory, {'a': '/a', 'b': '/b'}, Path('rig.json'), 'reliable')

    def test_mismatch_and_encoding_failure_publish_no_partial_set(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaises(ValueError):
                save_images({'a': image(1), 'b': image(2)}, directory, {}, Path('rig.json'), 'reliable')
            bad = image(1, pixels=b'\x00')
            with self.assertRaises(ValueError):
                save_images({'a': image(1), 'b': bad}, directory, {'a': '/a', 'b': '/b'},
                            Path('rig.json'), 'reliable')
            self.assertEqual(list(directory.iterdir()), [])

    def test_png_omits_ros_row_padding_and_preserves_rgb_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'image.png'
            write_png(path, image(pixels=b'\xff\x00\x00\x00\xff\x00\x99\x99', step=8, width=2))
            png = path.read_bytes()
            self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
            offset = 8
            compressed = b''
            while offset < len(png):
                size = struct.unpack('!I', png[offset:offset + 4])[0]
                kind = png[offset + 4:offset + 8]
                payload = png[offset + 8:offset + 8 + size]
                crc = struct.unpack('!I', png[offset + 8 + size:offset + 12 + size])[0]
                self.assertEqual(crc, zlib.crc32(kind + payload) & 0xffffffff)
                if kind == b'IDAT':
                    compressed += payload
                offset += size + 12
            self.assertEqual(zlib.decompress(compressed), b'\x00\xff\x00\x00\x00\xff\x00')


if __name__ == '__main__':
    unittest.main()
