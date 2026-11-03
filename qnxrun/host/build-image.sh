#!/usr/bin/env bash
# Runs inside the qnx-host container: bakes qnxrun-agent into a QNX 8.0 QEMU image at /vm.
set -euo pipefail
# shellcheck disable=SC1091
source "$HOME/qnx800/qnxsdp-env.sh" >/dev/null
cd /vm
mkdir -p bin local/snippets
qcc -Vgcc_ntox86_64 -std=gnu11 -Wall -Wextra -Werror -O2 -D_QNX_SOURCE /qnxrun/qnxrun-agent.c -o bin/qnxrun-agent
echo "[perms=555] bin/qnxrun-agent=/vm/bin/qnxrun-agent" > local/snippets/system_files.custom
mkqnximage --type=qemu --arch=x86_64 --noprompt --ssh-ident=none --hostname=qnxrun --build
[ -z "${QNXRUN_OWNER:-}" ] || chown -R "$QNXRUN_OWNER" /vm
