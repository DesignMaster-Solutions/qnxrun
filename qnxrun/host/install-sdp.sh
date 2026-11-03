#!/usr/bin/env bash
# Runs inside the qnx-host container with $HOME on the SDP volume. Headless QNX SDP 8.0 install.
# Credentials arrive only through the environment; qnxrun masks them in everything this prints.
set -euo pipefail
: "${QSC_RUN:?}" "${QNX_USER:?}" "${QNX_PASS:?}" "${QNX_KEY:?}"
DEST=${DEST:-$HOME/qnx800}
mkdir -p "$HOME/qnx"
# The installer is a self-extracting archive; --tar unpacks the Software Center without the GUI.
sh "$QSC_RUN" --tar xf -C "$HOME/qnx"
CLT=$(find "$HOME/qnx" -maxdepth 3 -name qnxsoftwarecenter_clt -type f | head -1)
[ -n "$CLT" ] || { echo "qnxsoftwarecenter_clt not found in the installer"; exit 1; }
auth=(-myqnx.user "$QNX_USER" -myqnx.password "$QNX_PASS")
"$CLT" "${auth[@]}" -addLicenseKey "$QNX_KEY"
"$CLT" "${auth[@]}" -cleanInstall -mirrorBaseline qnx800 -installBaseline com.qnx.qnx800 -destination "$DEST"
# shellcheck disable=SC1091
source "$DEST/qnxsdp-env.sh"
command -v qcc
command -v mkqnximage
