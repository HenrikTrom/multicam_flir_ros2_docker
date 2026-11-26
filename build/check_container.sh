#!/bin/bash
set -eo pipefail

test "${ROS_DISTRO}" = jazzy
test "$(id -u)" -ne 0
test -w /home/docker/ros2_ws
id
dpkg-query -W libgentl libspinnaker libspinnaker-dev

# Exercise ROS initialization, the selected middleware, and message delivery.
python3 - <<'PY'
import os
import time
import rclpy
from rclpy.utilities import get_rmw_implementation_identifier
from std_msgs.msg import String

rclpy.init()
node = rclpy.create_node(f'container_check_{os.getpid()}')
received = []
topic = f'/container_check_{os.getpid()}'
subscription = node.create_subscription(String, topic, lambda msg: received.append(msg.data), 10)
publisher = node.create_publisher(String, topic, 10)
deadline = time.monotonic() + 10
try:
    while not received and time.monotonic() < deadline:
        publisher.publish(String(data='jazzy-ok'))
        rclpy.spin_once(node, timeout_sec=0.1)
    assert 'jazzy-ok' in received, 'ROS message delivery timed out'
    print(f'ROS 2 Jazzy message delivery OK ({get_rmw_implementation_identifier()})')
finally:
    node.destroy_node()
    rclpy.shutdown()
PY

# Compile and run against the installed C++ headers and runtime. Camera
# enumeration also works with no cameras connected; it does not acquire images.
check_dir=$(mktemp -d)
trap 'rm -rf "$check_dir"' EXIT
cat > "$check_dir/check.cpp" <<'CPP'
#include <Spinnaker.h>
#include <iostream>

int main()
{
    try {
        auto system = Spinnaker::System::GetInstance();
        const auto version = system->GetLibraryVersion();
        std::cout << "Spinnaker " << version.major << '.' << version.minor << '.'
                  << version.type << '.' << version.build << '\n';
        auto cameras = system->GetCameras();
        std::cout << "Cameras detected: " << cameras.GetSize() << '\n';
        for (unsigned int i = 0; i < cameras.GetSize(); ++i) {
            auto camera = cameras.GetByIndex(i);
            std::cout << "  " << camera->TLDevice.DeviceModelName.GetValue()
                      << " serial=" << camera->TLDevice.DeviceSerialNumber.GetValue() << '\n';
        }
        cameras.Clear();
        system->ReleaseInstance();
        return 0;
    } catch (const Spinnaker::Exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
CPP
g++ -std=c++17 -I/opt/spinnaker/include "$check_dir/check.cpp" \
    -L/opt/spinnaker/lib -lSpinnaker -o "$check_dir/check"
"$check_dir/check"
