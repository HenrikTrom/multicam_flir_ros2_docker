#!/bin/bash
set -eo pipefail

source "/opt/ros/${ROS_DISTRO}/setup.bash"
cd /home/docker/ros2_ws

driver_dir=src/flir_ros2_camera_driver
if [ ! -d "$driver_dir" ] || [ -z "$(find "$driver_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo '[start-cameras] Driver submodule is empty; installing the bundled pinned checkout.'
    mkdir -p "$driver_dir"
    # Copy Git metadata too, leaving a valid checkout for later submodule updates.
    # Do not preserve root ownership from the image's bundled source directory.
    cp -R /opt/flir_ros2_camera_driver/. "$driver_dir/"
fi

# colcon succeeds even when it finds only the launch package. Require the source
# packages explicitly, regardless of any old binaries in install/.
source_packages=$(colcon list --base-paths src --names-only)
for package in flir_camera_msgs spinnaker_camera_driver spinnaker_synchronized_camera_driver grasshopper_bringup; do
    if ! grep -Fxq -- "$package" <<< "$source_packages"; then
        echo "[start-cameras] ERROR: required source package '$package' is missing or ignored." >&2
        echo '[start-cameras] Existing source directories were left unchanged. From the host repository, run:' >&2
        echo '  git submodule sync --recursive' >&2
        echo '  git submodule update --init --recursive' >&2
        exit 1
    fi
done

echo '[start-cameras] Building ROS workspace...'
colcon build \
    --symlink-install \
    --parallel-workers "${COLCON_PARALLEL_WORKERS:-2}" \
    --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF

# A failed build exits above, even if an older install directory exists.
source install/setup.bash
python3 - <<'PY'
from ament_index_python.resources import get_resource

package = 'spinnaker_synchronized_camera_driver'
plugin = 'spinnaker_synchronized_camera_driver::SynchronizedCameraDriver'
try:
    resource, _ = get_resource('rclcpp_components', package)
except LookupError as error:
    raise SystemExit(f'[start-cameras] ERROR: built driver component is missing from the ament index: {error}')
if plugin not in {line.split(';', 1)[0].strip() for line in resource.splitlines()}:
    raise SystemExit(f'[start-cameras] ERROR: built driver does not register {plugin}')
PY
echo '[start-cameras] Build succeeded; launching the synchronized camera streamer.'
exec ros2 launch grasshopper_bringup cameras.launch.py "$@"
