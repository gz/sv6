#!/bin/bash

# $1 -> total number of cores
# $2 -> total number of sockets

set -ex

function exec_tmux {
	tmux send-keys -t sv6 $1 Enter
}

function run_qemu {
	tmux send-keys -t sv6 "./run.py --qemu-affinity --qemu-cores $1 --qemu-nodes $2 --qemu-memory 65536" Enter
}

function exec_tmux_vmops {
	tmux send-keys -t sv6 "vmops $1 local 1" Enter
}

# let's compile first to avoid the delay
make -j

qemu-system-x86_64 --version

# First create the tmux session and detach from it
tmux new-session -n'sv6' -s'sv6' -d

# Now run the qemu script
# clean up the serial.log file first
rm sv6_serial.log -f

# run the script
run_qemu $1 $2

# let's sleep for a while, since sv6 takes a bit of time to boot up
sleep 30

# Now start running the script
for cores in 1 `seq 8 8 $1`; do
	exec_tmux_vmops $cores
	sleep 60
done

exec_tmux "halt"
sleep 5
sync
tmux kill-session -t 'sv6'

cat sv6_serial.log

python3 ./parse.py
