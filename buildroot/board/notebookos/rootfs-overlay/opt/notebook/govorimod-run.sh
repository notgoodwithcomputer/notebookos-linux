#!/bin/sh
# govorimod-run — keeps the Govorimo daemon serving the session.
#
# The daemon owns radio, crypto, mesh and the message store; the app is a
# socket client that survives daemon restarts (it reconnects once a second).
# This wrapper's one job is matching the daemon to the hardware actually
# present:
#
#   no /dev/lora            -> --radio none   (identity, history, reading work;
#                                              nothing transmits)
#   /dev/lora, provisioned  -> --radio e22:/dev/lora
#   /dev/lora, factory      -> --radio none, until the app's provisioning
#                              ceremony finishes and stamps completion
#
# THE PROBE IS EVENT-GATED, NEVER PERIODIC. A factory dongle sits in
# transparent mode on 868.125 MHz, and bytes written to it — including a
# probe's register reads — are TRANSMITTED on that band. One probe per plug
# event and one per provisioning stamp is defensible; a polling loop
# chirping on somebody else's band is not.

SOCK="${GOVORIMO_SOCKET:-/run/govorimo.sock}"
STAMP="${GOVORIMO_STAMP:-/run/govorimo-provisioned.stamp}"
STATE="${NB_HOME:-/root}/.config/notebook/govorimo.d"
LORA="${GOVORIMO_DEV:-/dev/lora}"
BIN="${GOVORIMOD_BIN:-/usr/bin/govorimod}"
mkdir -p "$STATE"

probe_provisioned() {
	python3 /opt/notebook/de/govorimolib.py probe "$LORA" 2>/dev/null \
		| grep -q 'provisioned=True'
}

# What the daemon should run on right now. Probes at most when $1 = fresh.
want_radio() {
	if [ ! -e "$LORA" ]; then
		echo none
		return
	fi
	if [ "$1" = fresh ] && probe_provisioned; then
		echo "e22:$LORA"
	else
		echo none
	fi
}

last_stamp=""
while :; do
	# A new plug event or a fresh provisioning stamp justifies one probe.
	if [ -e "$LORA" ]; then
		RADIO=$(want_radio fresh)
	else
		RADIO=none
	fi
	[ -e "$STAMP" ] && last_stamp=$(stat -c %Y "$STAMP" 2>/dev/null)

	"$BIN" --socket "$SOCK" --state-dir "$STATE" \
		--radio "$RADIO" >/dev/null 2>&1 &
	PID=$!

	# Hold here while the world matches the daemon. Cheap checks only —
	# device presence and stamp mtime — no serial traffic.
	prev_present=$([ -e "$LORA" ] && echo 1 || echo 0)
	while kill -0 "$PID" 2>/dev/null; do
		sleep 2
		present=$([ -e "$LORA" ] && echo 1 || echo 0)
		if [ "$RADIO" != none ] && [ "$present" = 0 ]; then
			break            # dongle pulled out from under the daemon
		fi
		if [ "$RADIO" = none ] && [ "$present" = 1 ]; then
			if [ "$prev_present" = 0 ]; then
				break    # a fresh plug event earns one probe
			fi
			now_stamp=""
			[ -e "$STAMP" ] && now_stamp=$(stat -c %Y "$STAMP" 2>/dev/null)
			if [ "$now_stamp" != "$last_stamp" ]; then
				break    # the ceremony just finished; probe again
			fi
		fi
		prev_present=$present
	done

	if kill -0 "$PID" 2>/dev/null; then
		kill "$PID" 2>/dev/null
	fi
	wait "$PID" 2>/dev/null
	sleep 1
done
