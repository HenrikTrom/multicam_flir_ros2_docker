# multicam_flir_ros2_docker

ROS 2 Jazzy / Ubuntu 24.04 (amd64) container running the local
`flir_ros2_camera_driver` fork against Spinnaker 4.4.0.246. The project launch
package, `grasshopper_bringup`, lives alongside the fork in `ros2_ws/src`. A small
local driver fix initializes the acquisition watchdog timestamp on startup and
after restarts so the configured timeout applies before the first image arrives.

The rig configuration is the existing JSON file `./flir_grashopper.json`, mounted
read-only at `/home/docker/config/flir_grashopper.json`. It specifies:

- Five Grasshopper3 GS3-U3-32S4C cameras, selected by serial number.
- 1024×768 RGB8, Mode1, vertical binning 2, and zero image offsets.
- 60 fps, manual gain 14 dB, black level 8, and exposure 4000 µs.
- Master `20174578`: free-running, with `ExposureActive` output on Line3.
- Followers: FrameStart triggers from Line3, rising edge, ReadOut overlap, with frame-rate limiting disabled.

The full rig uses the fork's `spinnaker_synchronized_camera_driver` in one
multithreaded component container. It configures all cameras before acquisition
and aligns their ROS timestamps. Single-camera and free-running tests use the
regular `spinnaker_camera_driver` components in one process. The GenICam mapping in
`ros2_ws/src/grasshopper_bringup/config/grasshopper.yaml` covers the image geometry,
analog controls, trigger IO, and chunk metadata. Its ordering matters: triggers
are disabled before configuration, and IO is configured before triggers are armed.
Grasshopper's trigger-overlap setting is applied after enabling its trigger,
when that feature becomes accessible.
This assumes the rig's existing trigger wiring is connected.

Build and start the synchronized five-camera streamer:

```bash
docker compose up -d
docker compose logs -f ros2-flir-multicam
```

Compose builds the Docker image using cached layers, then starts `start-cameras`.
The startup script sources Jazzy, runs `colcon build` across the mounted
`ros2_ws`, sources the resulting workspace overlay, and starts
`ros2 launch grasshopper_bringup cameras.launch.py`. A failed workspace build
stops the container before camera launch, including when an older install exists.
No manual workspace build is required.

Workspace builds use symlink install, Release mode, disabled package tests, and
two parallel package workers by default (`COLCON_PARALLEL_WORKERS` can override
the worker count in the container). Generated `build`, `install`, and `log`
directories persist in the mounted workspace, so subsequent starts build
incrementally. The Docker image contains the SDK, compiler, colcon, and driver
dependencies; workspace sources are read from the bind mount at startup.

`docker compose up` keeps the logs in the foreground;
`docker compose up -d` runs the streamer in the background. Stop with Ctrl+C
in foreground mode, `docker compose stop`, or `docker compose down`. Compose
sends SIGINT and allows up to 30 seconds for camera cleanup.

To rebuild after editing workspace sources, run
`docker compose restart ros2-flir-multicam`. After changing the Dockerfile or
image scripts, run `docker compose up -d` to rebuild the image and recreate the
service. If the service is already running unchanged, `up` leaves it running;
`restart` explicitly reruns the workspace build and launch sequence. To open a
development shell while streaming, use `docker compose exec ros2-flir-multicam bash`.

In another host terminal, check actual ROS images and acquisition metadata:

```bash
docker compose exec ros2-flir-multicam /entrypoint.sh \
  ros2 run grasshopper_bringup check_cameras
```

The check receives a complete raw image from every camera, verifies RGB8 payloads
and dimensions, then samples metadata for five seconds. It checks frame rate,
exposure, and gain and exits with failure if a stream is missing or differs from
the configuration. Exposure allows camera quantization: 4000 µs is reported as
3998 µs on this rig. Raw image subscriptions are released after the first frame
to keep the metadata measurement lightweight.

Each camera publishes:

```text
/grasshopper/camera_<serial>/image_raw
/grasshopper/camera_<serial>/camera_info
/grasshopper/camera_<serial>/meta
```

Images are streamed as uncompressed `sensor_msgs/msg/Image` messages on
`/grasshopper/camera_<serial>/image_raw`, using Fast DDS and Reliable QoS. The
configured Spinnaker pixel-format enum `RGB8` maps to the ROS encoding `rgb8`:
8 bits each for red, green, and blue, interleaved in RGB order. At 1024×768,
each message has `step: 3072` and 2,359,296 pixel-data bytes. Keep the camera's
`pixel_format` value as uppercase `RGB8` in `flir_grashopper.json`; the driver
sets the lowercase ROS encoding. Saved PNG snapshots are produced separately
by `get_images.py`.

To save one image from every camera with exactly equal ROS header timestamps,
keep the synchronized launch running and execute from another host terminal:

```bash
docker compose exec ros2-flir-multicam /entrypoint.sh \
  python3 src/tests/get_images.py
```

`ros2_ws/src/tests/get_images.py` reads the camera serials from the same JSON
configuration. It uses Reliable subscriptions and buffers up to eight images per
camera. It matches the original integer `header.stamp.sec` and
`header.stamp.nanosec` fields exactly, with no approximate matching or restamping.
Only a complete set is saved, as five lossless PNGs and a `manifest.json` recording
each image's original stamp, topic, and frame ID. The set appears atomically under
`ros2_ws/src/tests/data/<sec>_<nanosec>/`. Generated image sets are ignored by Git.
If no full match arrives within 30 seconds, it exits with failure and saves no
images. Options include `--timeout`, `--queue-size`, `--config-file`, `--output-dir`,
and `--qos best_effort` (default: `reliable`).

The synchronized driver assigns a shared estimated timestamp to frame groups.
The capture script verifies equality of the published ROS stamps; measuring
physical exposure skew requires separate hardware validation.

Camera values live in `flir_grashopper.json`, mounted at
`/home/docker/config/flir_grashopper.json`. The launch file reads it at startup and
passes those values to the driver. The launch file also defines trigger setup,
chunk metadata, timestamp behavior, and queue sizes. The mapping YAML translates
ROS parameter names to GenICam nodes; it is not the rig's camera-value file.
Run `docker compose restart ros2-flir-multicam` after editing the JSON.

The image publishers use Reliable QoS with `image_queue_size: 4`; the snapshot
script defaults to Reliable with depth 8. Best effort avoids retries and favors
fresh frames but may lose frames, causing entire synchronized sets to be skipped.
Reliable retries delivery, which can increase latency and resource use with slow
readers; finite history still limits retention. Neither QoS policy synchronizes
the cameras. See the
[ROS QoS documentation](https://github.com/ros2/ros2_documentation/blob/jazzy/source/Concepts/Intermediate/About-Quality-of-Service-Settings.rst).

Stop the streaming service before testing a single camera without external
triggers in a one-off container:

```bash
docker compose stop ros2-flir-multicam
docker compose run --rm --no-deps ros2-flir-multicam \
  ros2 launch grasshopper_bringup cameras.launch.py serial:=19037266 synchronized:=false
```

Stop the one-off launch with Ctrl+C, then restore the full rig with
`docker compose up -d`.

`serial` defaults to all configured cameras, `synchronized` defaults to `true`,
and `config_file` can override the mounted JSON path. A single follower requires
`synchronized:=false` when its master is not running. The recording-only JSON
fields `save_dir` and `codec` are not used by this acquisition launch.

ROS is sourced by `/entrypoint.sh` and interactive Bash shells. The workspace
overlay is sourced when built. `docker compose exec` with a direct command should
use `/entrypoint.sh` as shown above because Docker exec does not run the image's
entrypoint automatically. Reopen existing shells after rebuilding or recreating
the container.

`.env` uses `ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET`; the deprecated
`ROS_LOCALHOST_ONLY` variable is not passed into the container. See the
[ROS discovery documentation](https://github.com/ros2/ros2_documentation/blob/rolling/source/Developer-Tools/Launch/Improved-Dynamic-Discovery.rst).

Fast DDS (`rmw_fastrtps_cpp`, bundled with Jazzy) is the default middleware, with
1 GiB of container shared memory for raw images. On this host, Cyclone DDS
initialized and delivered metadata but did not reliably deliver the 2.36 MB raw
images with the host's 208 KiB UDP receive-buffer limit. Fast DDS delivered all
five image streams without changing host kernel settings. Cyclone DDS remains
installed if needed; using it for large images can require the host/socket
configuration described in the
[ROS DDS tuning guide](https://repo.test.ros2.org/en/rolling/How-To-Guides/DDS-tuning.html).

Set `UID` and `GID` in `.env` to the host user's `id -u` and `id -g`.
`FLIR_GID` must match the host group owning the camera USB devices (currently
`1001`). The container runs as `docker` with these IDs and passwordless sudo for
development. Compose uses Linux host networking and passes `/dev/bus/usb` into
the container. USB permissions, USB buffer sizes, and GigE tuning belong on the
host and are not modified by this project.

Only three vendor packages are copied into `build/spinnaker/`:

| Package | Purpose |
| --- | --- |
| `libgentl_4.4.0.246_amd64.deb` | Camera transport runtime |
| `libspinnaker_4.4.0.246_amd64.deb` | C++ acquisition runtime |
| `libspinnaker-dev_4.4.0.246_amd64.deb` | C++ headers |

The complete vendor SDK is preserved in
`Spinnaker 4.4 Ubuntu 24/spinnaker-4.4.0.246-noble-amd64-pkg/`, outside the build
context. Proprietary packages are ignored by Git, so a fresh checkout needs the
three local `.deb` files before building. The Docker build preseeds acceptance
of the FLIR SDK license for unattended installation. SpinView, SpinVideo, C
bindings, firmware update utilities, and vendor examples are not installed.

For a base-container smoke check, stop camera acquisition first and run:

```bash
docker compose stop ros2-flir-multicam
docker compose run --rm --no-deps ros2-flir-multicam check-container
```

No calibration files have been supplied, so the driver reports missing camera
calibrations and publishes uncalibrated camera info. This does not prevent raw
image acquisition. Hardware triggers are configured, but the image check does
not measure physical trigger skew or verify timestamp alignment.

Validation on 2026-11-26: the image and all four workspace packages build, and
`ros2 topic list` runs without the deprecated-discovery warnings. Cameras
`20174578`, `19246521`, `19338645`, and `19421325` repeatedly delivered complete
1024×768 RGB8 ROS images at approximately 60 fps, with 3998 µs exposure and 14 dB
gain. One earlier run received the same streams from all five cameras, but that
result did not reproduce reliably. Camera `19037266` repeatedly produced no frames
in the synchronized launch; its standalone ROS test started around 30 fps and
also stalled. The checker correctly returns failure for these results. Consistent
ROS acquisition for this camera remains unresolved; this is not an all-camera
streaming pass. The watchdog fix prevents one-second restart loops before a first
image, but does not resolve the remaining acquisition stall.

A later exact-timestamp capture succeeded for all five cameras and was saved in
`ros2_ws/src/tests/data/1795695536_353181735/`. All PNGs decoded as 1024×768 RGB,
and every recorded original ROS header stamp was `1795695536.353181735`. This
validates that snapshot; it does not establish sustained acquisition stability.

Stop the development service with `docker compose down`.
