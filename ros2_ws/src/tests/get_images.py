#!/usr/bin/env python3
"""Save one lossless image per camera with exactly equal ROS header timestamps."""

import argparse
from collections import OrderedDict
import json
from pathlib import Path
import re
import shutil
import struct
import tempfile
import time
import zlib


def stamp_key(image):
    stamp = image.header.stamp
    if not 0 <= stamp.nanosec < 1_000_000_000:
        raise ValueError('Invalid nanosecond field in image header')
    return stamp.sec, stamp.nanosec


class ExactImageSet:
    """Bounded per-camera queues; match original integer timestamps exactly."""

    def __init__(self, serials, queue_size):
        if not serials or len(set(serials)) != len(serials) or queue_size < 1:
            raise ValueError('Expected unique camera serials and a positive queue size')
        self.buffers = {serial: OrderedDict() for serial in serials}
        self.counts = {serial: 0 for serial in serials}
        self.queue_size = queue_size

    def add(self, serial, image):
        self.counts[serial] += 1
        stamp = stamp_key(image)
        # Uninitialized stamps must not produce a false synchronized set.
        if stamp == (0, 0):
            return None
        queue = self.buffers[serial]
        queue[stamp] = image
        while len(queue) > self.queue_size:
            queue.popitem(last=False)
        if all(stamp in queue for queue in self.buffers.values()):
            return {serial: queue[stamp] for serial, queue in self.buffers.items()}
        return None


def write_png(path, image):
    """Encode the rig's RGB8 images without OpenCV/cv_bridge dependencies."""
    formats = {'rgb8': (3, 2), 'bgr8': (3, 2), 'mono8': (1, 0)}
    if image.encoding not in formats:
        raise ValueError(f'Unsupported image encoding: {image.encoding}')
    channels, color_type = formats[image.encoding]
    row_size = image.width * channels
    if (image.width <= 0 or image.height <= 0 or image.step < row_size
            or len(image.data) != image.step * image.height):
        raise ValueError('Invalid or incomplete image payload')
    data = memoryview(image.data)
    rows = []
    for row in range(image.height):
        pixels = bytes(data[row * image.step:row * image.step + row_size])
        if image.encoding == 'bgr8':
            rgb = bytearray(pixels)
            rgb[0::3], rgb[2::3] = pixels[2::3], pixels[0::3]
            pixels = bytes(rgb)
        rows.append(b'\x00' + pixels)  # PNG filter type: None

    def chunk(kind, payload):
        return (struct.pack('!I', len(payload)) + kind + payload
                + struct.pack('!I', zlib.crc32(kind + payload) & 0xffffffff))

    header = struct.pack('!IIBBBBB', image.width, image.height, 8, color_type, 0, 0, 0)
    with path.open('xb') as stream:
        stream.write(b'\x89PNG\r\n\x1a\n')
        stream.write(chunk(b'IHDR', header))
        stream.write(chunk(b'IDAT', zlib.compress(b''.join(rows), level=3)))
        stream.write(chunk(b'IEND', b''))


def save_images(images, output_dir, topics, config_file, reliability):
    """Publish the complete image set atomically; leave no partial capture."""
    stamps = {stamp_key(image) for image in images.values()}
    if len(stamps) != 1 or (0, 0) in stamps:
        raise ValueError('Refusing to save images without one identical, nonzero timestamp')
    sec, nanosec = stamps.pop()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f'{sec}_{nanosec:09d}'
    if destination.exists():
        raise FileExistsError(f'Capture already exists: {destination}')
    staging = Path(tempfile.mkdtemp(prefix='.capture-', dir=output_dir))
    manifest = {
        'timestamp': {'sec': sec, 'nanosec': nanosec},
        'timestamp_source': 'Original sensor_msgs/Image.header.stamp; no restamping',
        'config_file': str(config_file),
        'subscription_reliability': reliability,
        'images': [],
    }
    try:
        for serial, image in images.items():
            filename = f'camera_{serial}.png'
            write_png(staging / filename, image)
            manifest['images'].append({
                'serial': serial, 'topic': topics[serial], 'file': filename,
                'header_stamp': {'sec': image.header.stamp.sec,
                                 'nanosec': image.header.stamp.nanosec},
                'frame_id': image.header.frame_id, 'encoding': image.encoding,
                'width': image.width, 'height': image.height,
            })
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        staging.rename(destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return destination


def capture(serials, topics, queue_size, timeout, reliability):
    import rclpy
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import Image

    rclpy.init()
    node = rclpy.create_node('capture_exact_camera_images')
    matcher = ExactImageSet(serials, queue_size)
    matched = None
    qos = QoSProfile(
        history=HistoryPolicy.KEEP_LAST, depth=queue_size,
        reliability=(ReliabilityPolicy.RELIABLE if reliability == 'reliable'
                     else ReliabilityPolicy.BEST_EFFORT),
        durability=DurabilityPolicy.VOLATILE,
    )

    def receive(serial, image):
        nonlocal matched
        if matched is None:
            matched = matcher.add(serial, image)

    try:
        for serial in serials:
            node.create_subscription(
                Image, topics[serial], lambda image, serial=serial: receive(serial, image), qos)
            node.get_logger().info(f'Subscribing ({reliability}): {topics[serial]}')
        deadline = time.monotonic() + timeout
        while rclpy.ok() and matched is None and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=min(0.1, max(0, deadline - time.monotonic())))
        if matched is None:
            latest = {serial: list(queue)[-1] if queue else None
                      for serial, queue in matcher.buffers.items()}
            raise TimeoutError(
                f'No exact timestamp match across all {len(serials)} cameras in {timeout:g}s.\n'
                f'Received messages: {matcher.counts}\nLatest buffered stamps: {latest}\n'
                'No images saved. Run cameras.launch.py with synchronized:=true; '
                'every configured camera must publish.')
        return matched
    finally:
        # Stop the large raw-image subscriptions before compressing/writing PNGs.
        node.destroy_node()
        rclpy.shutdown()


def main():
    script_dir = Path(__file__).resolve().parent
    host_config = script_dir.parents[2] / 'flir_grashopper.json'
    container_config = Path('/home/docker/config/flir_grashopper.json')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-file', type=Path,
                        default=container_config if container_config.exists() else host_config)
    parser.add_argument('--output-dir', type=Path, default=script_dir / 'data')
    parser.add_argument('--timeout', type=float, default=30.0)
    parser.add_argument('--queue-size', type=int, default=8,
                        help='Maximum buffered images per camera (default: 8)')
    parser.add_argument('--qos', choices=['reliable', 'best_effort'], default='reliable')
    args = parser.parse_args()
    if args.timeout <= 0 or args.queue_size < 1:
        parser.error('timeout and queue-size must be positive')
    try:
        config_file = args.config_file.resolve()
        config = json.loads(config_file.read_text())
        serials = [str(camera['serial']) for camera in config['cams']]
        if (not serials or len(set(serials)) != len(serials)
                or any(not re.fullmatch(r'[0-9]+', serial) for serial in serials)):
            raise ValueError('Configuration must contain unique numeric camera serials')
        topics = {serial: f'/grasshopper/camera_{serial}/image_raw' for serial in serials}
        images = capture(serials, topics, args.queue_size, args.timeout, args.qos)
        destination = save_images(images, args.output_dir.resolve(), topics, config_file, args.qos)
    except (OSError, ValueError, KeyError, TypeError, TimeoutError) as error:
        parser.exit(1, f'{error}\n')
    except KeyboardInterrupt:
        parser.exit(130, 'Capture interrupted; no image set saved.\n')
    sec, nanosec = stamp_key(next(iter(images.values())))
    print(f'Saved {len(images)} images with identical header.stamp={sec}.{nanosec:09d}')
    print(destination)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
