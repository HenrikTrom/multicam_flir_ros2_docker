#!/bin/bash
set -euo pipefail

# Bash reserves UID, so use USER_UID / USER_GID for build arguments.
: "${USER_UID:?USER_UID is required}"
: "${USER_GID:?USER_GID is required}"
test "${USER_UID}" -gt 0
test "${USER_GID}" -gt 0

if getent group "${USER_GID}" >/dev/null; then
    group_name="$(getent group "${USER_GID}" | cut -d: -f1)"
else
    group_name=docker
    groupadd --gid "${USER_GID}" "${group_name}"
fi

# Ubuntu images can already contain a user with UID 1000.
existing_user="$(getent passwd "${USER_UID}" | cut -d: -f1 || true)"
if [ -n "${existing_user}" ]; then
    if [ "${existing_user}" != docker ]; then
        usermod --login docker "${existing_user}"
    fi
    usermod --home /home/docker --move-home --shell /bin/bash --gid "${group_name}" docker
else
    useradd --create-home --home-dir /home/docker --shell /bin/bash \
        --gid "${group_name}" --uid "${USER_UID}" docker
fi

printf 'docker ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/docker
chmod 0440 /etc/sudoers.d/docker
visudo --check --file /etc/sudoers.d/docker
