#!/bin/bash
set -eo pipefail

source "/opt/ros/${ROS_DISTRO}/setup.bash"
cd /home/docker/ros2_ws

echo '[start-cameras] Building ROS workspace...'
colcon build \
    --symlink-install \
    --parallel-workers "${COLCON_PARALLEL_WORKERS:-2}" \
    --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF

# A failed build exits above, even if an older install directory exists.
source install/setup.bash
echo '[start-cameras] Build succeeded; launching the synchronized camera streamer.'
exec ros2 launch grasshopper_bringup cameras.launch.py "$@"
