#!/usr/bin/python3

import argparse
import os
import sys
import pathlib
import shutil
import subprocess
import prctl
import signal
from time import sleep

from plumbum import colors, local
from plumbum.cmd import sudo, tunctl, ifconfig, whoami, python3, make, cat, corealloc


def exception_handler(exception_type, exception, traceback):
    print("%s: %s" % (exception_type.__name__, exception))


#
# run.py script settings
#
SCRIPT_PATH = pathlib.Path(os.path.dirname(os.path.realpath(__file__)))
CARGO_DEFAULT_ARGS = ["--color", "always", "-Zfeatures=all"]
ARCH = "x86_64"
# TODO: should be generated for enabling parallel builds
# QEMU_TAP_NAME = 'tap0'
# QEMU_TAP_ZONE = '172.31.0.20/24'

#
# Important globals
#
BOOTLOADER_PATH = (SCRIPT_PATH / '..').resolve() / 'bootloader'
TARGET_PATH = (SCRIPT_PATH / '..').resolve() / 'target'
KERNEL_PATH = SCRIPT_PATH
LIBS_PATH = (SCRIPT_PATH / '..').resolve() / 'lib'
USR_PATH = (SCRIPT_PATH / '..').resolve() / 'usr'

UEFI_TARGET = "{}-uefi".format(ARCH)
KERNEL_TARGET = "{}-bespin".format(ARCH)
USER_TARGET = "{}-bespin-none".format(ARCH)
USER_RUSTFLAGS = "-Clink-arg=-zmax-page-size=0x200000"

#
# Command line argument parser
#
parser = argparse.ArgumentParser()
# General build arguments
parser.add_argument("-v", "--verbose", action="store_true",
                    help="increase output verbosity")
parser.add_argument("-n", "--norun", action="store_true",
                    help="Only build, don't run")
parser.add_argument("-r", "--release", action="store_true",
                    help="Do a release build.")
parser.add_argument("--kfeatures", type=str, nargs='+', default=[],
                    help="Cargo features to enable (in the kernel).")
parser.add_argument("--ufeatures", type=str, nargs='+', default=[],
                    help="Cargo features to enable (in user-space, use module_name:feature_name syntax to specify module specific features, e.g. init:print-test).")
parser.add_argument('-m', '--mods', nargs='+', default=['init'],
                    help='User-space modules to be included in build & deployment', required=False)
parser.add_argument("--cmd", type=str,
                    help="Command line arguments passed to the kernel.")
parser.add_argument("--machine",
                    help='Which machine to run on (defaults to qemu)', required=False, default='qemu')

# QEMU related arguments
parser.add_argument("--qemu-nodes", type=int,
                    help="How many NUMA nodes and sockets (for qemu).", required=False, default=None)
parser.add_argument("--qemu-cores", type=int,
                    help="How many cores (will get evenly divided among nodes).", default=1)
parser.add_argument("--qemu-memory", type=str,
                    help="How much total memory in MiB (will get evenly divided among nodes).", default=1024)
parser.add_argument("--qemu-affinity", action="store_true", default=False,
                    help="Pin QEMU instance to dedicated host cores.")

parser.add_argument("--qemu-settings", type=str,
                    help="Pass additional generic QEMU arguments.")
parser.add_argument("--qemu-monitor", action="store_true",
                    help="Launch the QEMU monitor (for qemu)")
parser.add_argument("-d", "--qemu-debug-cpu", action="store_true",
                    help="Debug CPU reset (for qemu)")
parser.add_argument('--nic', default='e1000', choices=["e1000", "virtio"],
                    help='What NIC model to use for emulation', required=False)

SV6_EXIT_CODES = {
    0: "[SUCCESS]",
    1: "[FAIL] ReturnFromMain: main() function returned to arch_indepdendent part.",
    2: "[FAIL] Encountered kernel panic.",
    3: "[FAIL] Encountered OOM.",
    4: "[FAIL] Encountered unexpected Interrupt.",
    5: "[FAIL] General Protection Fault.",
    6: "[FAIL] Unexpected Page Fault.",
    7: "[FAIL] Unexpected process exit code when running a user-space test.",
    8: "[FAIL] Unexpected exception during kernel initialization.",
    9: "[FAIL] Got unrecoverable error (machine check, double fault)."
}


def log(msg):
    print(colors.bold | ">>>", end=" "),
    print(colors.bold.reset & colors.info | msg)


def build_kernel(args):
    "Builds the kernel binary"
    log("Build kernel")
    with local.cwd(KERNEL_PATH):
        if args.verbose:
            print("cd {}".format(KERNEL_PATH))
            print("make")
        make


def run(args):
    """
    Run the system on a hardware/emulation platform
    Returns: A bespin exit error code.
    """
    def run_qemu(args):
        log("Starting QEMU")
        qemu_default_args = ['-no-reboot']
        # Setup KVM and required guest hardware features
        qemu_default_args += ['-enable-kvm']
        qemu_default_args += ['-cpu',
                              'host']
                              # 'host,migratable=no,+invtsc,+tsc,+x2apic,+fsgsbase']
        # Use serial communication
        # '-nographic',
        # qemu_default_args += ['-display', 'none', '-serial', 'stdio']
        qemu_default_args += ['-chardev', 'stdio,id=char0,mux=on,logfile=serial.log,signal=off',
                '-serial', 'chardev:char0', '-mon', 'chardev=char0']

        # Enable networking with outside world
        qemu_default_args += ['-net', 'user',  '-net', 'nic,model=e1000',
                '-redir', 'tcp:2323::23', '-redir', 'tcp:8080::80']
        qemu_default_args += ['-device', 'ahci,id=ahci0',
                '-drive', 'if=none,file=./o.qemu/fs.img,format=raw,id=drive-sata0-0-0',
                '-device', 'ide-drive,bus=ahci0.0,drive=drive-sata0-0-0,id=sata0-0-0']
        qemu_default_args += ['-kernel', './o.qemu/kernel.elf']

        def query_host_numa():
            online = cat["/sys/devices/system/node/online"]()
            if "-" in online:
                nlow, nmax = online.split('-')
                assert int(nlow) == 0
                return int(nmax)
            else:
                return int(online.strip())

        host_numa_nodes = query_host_numa()
        if args.qemu_nodes and args.qemu_nodes > 0 and args.qemu_cores > 1:
            for node in range(0, args.qemu_nodes):
                mem_per_node = int(args.qemu_memory) / args.qemu_nodes
                qemu_default_args += ['-object', 'memory-backend-ram,id=nmem{},merge=off,dump=on,prealloc=off,size={}M,host-nodes={},policy=bind'.format(
                    node, int(mem_per_node), 0 if host_numa_nodes == 0 else node % host_numa_nodes)]

                qemu_default_args += ['-numa',
                                      "node,memdev=nmem{},nodeid={}".format(node, node)]
                qemu_default_args += ["-numa", "cpu,node-id={},socket-id={}".format(
                    node, node)]

        if args.qemu_cores and args.qemu_cores > 1 and args.qemu_nodes:
            qemu_default_args += ["-smp", "{},sockets={},maxcpus={}".format(
                args.qemu_cores, args.qemu_nodes, args.qemu_cores)]
        else:
            qemu_default_args += ["-smp",
                                  "{},sockets=1".format(args.qemu_cores)]

        if args.qemu_memory:
            qemu_default_args += ['-m', str(args.qemu_memory)]

        if args.qemu_debug_cpu:
            qemu_default_args += ['-d', 'int,cpu_reset']
        if args.qemu_monitor:
            qemu_default_args += ['-monitor',
                                  'telnet:127.0.0.1:55555,server,nowait']

        # Name threads on host for `qemu_affinity.py` to find it
        qemu_default_args += ['-name', 'sv6,debug-threads=on']

        qemu_args = ['qemu-system-x86_64'] + qemu_default_args.copy()
        if args.qemu_settings:
            qemu_args += args.qemu_settings.split()

        # Create a tap interface to communicate with guest and give it an IP
        # user = (whoami)().strip()
        # group = (local['id']['-gn'])().strip()
        # TODO: Could probably avoid 'sudo' here by doing
        # sudo setcap cap_net_admin .../run.py
        # in the setup.sh script
        # sudo[tunctl[['-t', QEMU_TAP_NAME, '-u', user, '-g', group]]]()
        # sudo[ifconfig[QEMU_TAP_NAME, QEMU_TAP_ZONE]]()

        # Run a QEMU instance
        cmd = qemu_args
        if args.verbose:
            print(' '.join(cmd))

        # Spawn qemu first, then set the guest CPU affinities
        # The `preexec_fn` ensures that qemu dies if run.py exits
        execution = subprocess.Popen(
            cmd, stderr=None, stdout=None, env=os.environ.copy(), preexec_fn=lambda: prctl.set_pdeathsig(signal.SIGKILL))
        from plumbum.machines import LocalCommand
        LocalCommand.QUOTE_LEVEL = 3

        if args.qemu_cores and args.qemu_affinity:
            affinity_list = str(corealloc['-c',
                                          str(args.qemu_cores), '-t', 'interleave']()).strip()
            # For big machines it can take a while to spawn all threads in qemu
            # if but if the threads are not spawned qemu_affinity.py fails, so we sleep
            sleep(2.00)
            if args.verbose:
                log("QEMU affinity {}".format(affinity_list))
            sudo[python3['./qemu_affinity.py',
                         '-k', affinity_list.split(' '), '--', str(execution.pid)]]()

        # Wait until qemu exits
        execution.wait()

        bespin_exit_code = execution.returncode >> 1
        if SV6_EXIT_CODES.get(bespin_exit_code):
            print(SV6_EXIT_CODES[bespin_exit_code])
        else:
            print(
                "[FAIL] Kernel exited with unknown error status {}... Update the script!".format(bespin_exit_code))

        if bespin_exit_code != 0:
            log("Invocation was: {}".format(cmd))
            if execution.stderr:
                print("STDERR: {}".format(execution.stderr.decode('utf-8')))

        return bespin_exit_code

    if args.machine == 'qemu':
        return run_qemu(args)
    else:
        log("Machine {} not supported".format(args.machine))
        return 99


#
# Main routine of run.py
#
if __name__ == '__main__':
    "Execution pipeline for building and launching bespin"
    args = parser.parse_args()

    if args.machine != 'qemu' and (args.qemu_debug_cpu or args.qemu_settings or args.qemu_monitor or args.qemu_cores or args.qemu_nodes):
        log("Can't specify QEMU specific arguments for non-qemu hardware")
        sys.exit(99)

    if args.release:
        CARGO_DEFAULT_ARGS.append("--release")
    if args.verbose:
        CARGO_DEFAULT_ARGS.append("--verbose")
    else:
        # Minimize python exception backtraces
        sys.excepthook = exception_handler

    # Build
    build_kernel(args)

    # Run
    if not args.norun:
        r = run(args)
        sys.exit(r)
