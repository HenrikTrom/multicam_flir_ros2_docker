ARG ROS_DISTRO=jazzy
FROM ros:${ROS_DISTRO}-ros-base-noble

ARG ROS_DISTRO
ARG USER_UID=1000
ARG USER_GID=1000
ARG FLIR_GID=1001

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    SPINNAKER_ROOT_DIR=/opt/spinnaker \
    GENICAM_GENTL64_PATH=/opt/spinnaker/lib/spinnaker-gentl

# Install only the C++ acquisition SDK and its headers, without the GUI,
# video recording libraries, documentation, or vendor examples.
COPY spinnaker/*.deb /tmp/spinnaker/
RUN test "$(dpkg --print-architecture)" = amd64 \
    && printf 'libspinnaker libspinnaker/accepted-flir-eula boolean true\n' | debconf-set-selections \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential \
        sudo \
        ros-${ROS_DISTRO}-rmw-cyclonedds-cpp \
        /tmp/spinnaker/*.deb \
    && ldconfig \
    && rm -rf /tmp/spinnaker /var/lib/apt/lists/*

COPY user.sh /tmp/user.sh
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        cmake \
        ca-certificates \
        git \
        python3-colcon-common-extensions \
        libyaml-cpp-dev \
        ros-${ROS_DISTRO}-camera-info-manager \
        ros-${ROS_DISTRO}-diagnostic-updater \
        ros-${ROS_DISTRO}-image-transport \
    && rm -rf /var/lib/apt/lists/*

# Keep this revision aligned with the repository's FLIR driver submodule.
# Bundle a complete checkout so a plain clone can start without runtime Git/network access.
ARG FLIR_DRIVER_REVISION=986172dbd8dab9ac5a365d0fcbd785508803e960
RUN git init --quiet --initial-branch=source /opt/flir_ros2_camera_driver \
    && git -C /opt/flir_ros2_camera_driver remote add origin \
        https://github.com/HenrikTrom/flir_ros2_camera_driver.git \
    && git -C /opt/flir_ros2_camera_driver fetch --depth 1 origin "${FLIR_DRIVER_REVISION}" \
    && git -C /opt/flir_ros2_camera_driver checkout --quiet --detach FETCH_HEAD \
    && test "$(git -C /opt/flir_ros2_camera_driver rev-parse HEAD)" = "${FLIR_DRIVER_REVISION}"

RUN bash /tmp/user.sh \
    && groupadd --gid "${FLIR_GID}" flirimaging \
    && usermod --append --groups flirimaging docker \
    && printf '\nsource /opt/ros/%s/setup.bash\n' "${ROS_DISTRO}" >> /home/docker/.bashrc \
    && printf '[ ! -f /home/docker/ros2_ws/install/setup.bash ] || source /home/docker/ros2_ws/install/setup.bash\n' >> /home/docker/.bashrc \
    && install -d -o docker -g "${USER_GID}" /home/docker/ros2_ws /home/docker/ros2_ws/src \
    && rm /tmp/user.sh

COPY --chmod=755 check_container.sh /usr/local/bin/check-container
COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY --chmod=755 start_cameras.sh /usr/local/bin/start-cameras

USER docker
WORKDIR /home/docker/ros2_ws

# Build the mounted workspace before sourcing its overlay and streaming.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["start-cameras"]
