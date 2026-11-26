#!/bin/bash
set -e
# The streamer builds against Jazzy before loading the newly built overlay.
if [ "${1:-}" = start-cameras ]; then
    exec "$@"
fi
source "/opt/ros/${ROS_DISTRO}/setup.bash"
if [ -f /home/docker/ros2_ws/install/setup.bash ]; then
    source /home/docker/ros2_ws/install/setup.bash
fi
exec "$@"
