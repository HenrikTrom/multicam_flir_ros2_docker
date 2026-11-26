#!/usr/bin/env python3
"""Check real images and acquisition metadata from the launched Grasshopper rig."""

import argparse
import json
import time

import rclpy
from flir_camera_msgs.msg import ImageMetaData
from rclpy.qos import QoSProfile, qos_profile_sensor_data
from sensor_msgs.msg import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-file', default='/home/docker/config/flir_grashopper.json')
    parser.add_argument('--serial', default='', help='Check one configured camera')
    parser.add_argument('--duration', type=float, default=5.0,
                        help='Seconds to collect metadata after receiving the first images')
    parser.add_argument('--timeout', type=float, default=30.0)
    args = parser.parse_args()
    if args.duration <= 0 or args.timeout <= 0:
        parser.error('duration and timeout must be positive')
    with open(args.config_file) as stream:
        config = json.load(stream)
    cameras = {camera['serial']: camera for camera in config['cams']
               if not args.serial or camera['serial'] == args.serial}
    if not cameras:
        parser.error('serial is not in the configuration')

    rclpy.init()
    node = rclpy.create_node('grasshopper_image_check')
    images = {}
    metadata = {serial: [] for serial in cameras}
    image_subscriptions = {}
    meta_subscriptions = []

    def image_callback(serial, msg):
        images[serial] = (msg.width, msg.height, msg.encoding, len(msg.data), msg.step)
        # One complete image per camera is enough to check its format. Release
        # the large raw-image stream while measuring the smaller metadata stream.
        node.destroy_subscription(image_subscriptions[serial])

    for serial in cameras:
        prefix = f'/grasshopper/camera_{serial}'
        image_subscriptions[serial] = node.create_subscription(
            Image, prefix + '/image_raw',
            lambda msg, serial=serial: image_callback(serial, msg), QoSProfile(depth=1))
        meta_subscriptions.append(node.create_subscription(
            ImageMetaData, prefix + '/meta',
            lambda msg, serial=serial: metadata[serial].append(
                (msg.camera_time, msg.exposure_time, msg.gain)), qos_profile_sensor_data))

    start = time.monotonic()
    ready = None
    try:
        while time.monotonic() - start < args.timeout + args.duration:
            rclpy.spin_once(node, timeout_sec=0.1)
            if ready is None:
                if all(serial in images and metadata[serial] for serial in cameras):
                    ready = time.monotonic()
                    for samples in metadata.values():
                        samples.clear()
                elif time.monotonic() - start >= args.timeout:
                    break
            elif time.monotonic() - ready >= args.duration:
                break

        failures = []
        print('Serial     Image           Frames   Camera rate  Exposure  Gain')
        for serial, camera in cameras.items():
            if serial not in images or len(metadata[serial]) < 2:
                failures.append(f'{serial}: missing images or metadata')
                print(f'{serial}   MISSING (image={serial in images}, metadata={len(metadata[serial])})')
                continue
            width, height, encoding, size, step = images[serial]
            samples = metadata[serial]
            dt = (samples[-1][0] - samples[0][0]) * 1e-9
            rate = (len(samples) - 1) / dt if dt > 0 else 0.0
            print(f'{serial}   {width}x{height} {encoding:<5} {len(samples):6}   '
                  f'{rate:8.2f} Hz  {samples[-1][1]:5} us  {samples[-1][2]:.2f} dB')
            if (width, height, encoding) != (config['img_width'], config['img_height'], 'rgb8'):
                failures.append(f'{serial}: unexpected image dimensions or encoding')
            if size != step * height or step != width * 3:
                failures.append(f'{serial}: incomplete RGB image payload')
            if not 0.95 * config['fps'] <= rate <= 1.05 * config['fps']:
                failures.append(f'{serial}: frame rate differs from configured {config["fps"]} Hz')
            if ready is not None and len(samples) < 0.9 * args.duration * config['fps']:
                failures.append(f'{serial}: stream stalled or delivered too few frames during the check')
            exposure_tolerance = max(5.0, 0.005 * camera['exposure_time'])
            if any(abs(sample[1] - camera['exposure_time']) > exposure_tolerance for sample in samples):
                failures.append(f'{serial}: exposure differs from configuration')
            if any(abs(sample[2] - camera['gain']) > 0.1 for sample in samples):
                failures.append(f'{serial}: gain differs from configuration')
        if failures:
            raise SystemExit('\n'.join(failures))
        print('All configured ROS camera streams passed.')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
