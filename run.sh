#!/bin/bash

set -ex

function exec_tmux {
	tmux send-keys -t sv6 $1 Enter
}

function run_qemu {
	tmux send-keys -t sv6 "./run.py --qemu-affinity --qemu-cores 80 --qemu-nodes 8 --qemu-memory 65536" Enter
}

function exec_tmux_vmops {
	tmux send-keys -t sv6 "vmops $1 local 1" Enter
}

# let's compile first to avoid the delay
make -j

# First create the tmux session and detach from it
tmux new-session -n'sv6' -s'sv6' -d

# Now run the qemu script
# clean up the serial.log file first
rm serial.log -f

# run the script
run_qemu

# let's sleep for a while, since sv6 takes a bit of time to boot up
sleep 30

# Now start running the script
MAX_CORES=80
for cores in 1 `seq 8 8 $MAX_CORES`; do
	exec_tmux_vmops $cores
	sleep 60
done

exec_tmux "halt"
sleep 5
tmux kill-session -t 'sv6'

python3 ./parse.py
