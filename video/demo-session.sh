#!/usr/bin/env bash
# The demo footage: real commands on the host, typed at a human pace, against a target already up.
#   asciinema rec --headless -c "bash video/demo-session.sh" demo.cast   (from the repository root)
cd "$(dirname "$0")/../tests/e2e" || exit 1
qnxrun() { python3 ../../qnxrun.py "$@"; }
type_cmd() {
  printf '\033[1;32m$\033[0m '
  local i
  for ((i = 0; i < ${#1}; i++)); do printf '%s' "${1:i:1}"; sleep 0.045; done
  sleep 0.4; printf '\n'
  eval "$1"
  sleep "${2:-2}"
}
clear
type_cmd "qnxrun doctor" 3
type_cmd "cat hello.c" 3
type_cmd 'qnxrun run hello.c 7 "a b" '"'"'$(reboot)'"'"'; echo "host rc=$?"' 4
type_cmd 'qnxrun run --timeout 5 build/stream; echo "host rc=$?"' 3
type_cmd 'qnxrun run --timeout 5 build/hang; echo "host rc=$?"' 2
type_cmd "qnxrun shell -c 'pidin -f a | grep -c hang'" 3
type_cmd '(cd ../.. && python3 -m tests.tamper tests/e2e/build/victim); echo "host rc=$?"' 2
type_cmd "qnxrun shell -c 'ls /tmp/qnxrun-victim-ran'" 3
type_cmd 'qnxrun test build/hello build/victim --junit junit.xml; echo "host rc=$?"' 3
type_cmd "grep -o '<failure[^>]*>' junit.xml" 4
printf '\n'
