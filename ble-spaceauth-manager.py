import re
import aioserial
import asyncio
import os
import signal
import sys
import serial.serialutil
import fcntl
from enum import IntEnum

# 7-bit C1 ANSI sequences
ansi_escape = re.compile(r'''
    \x1B    # ESC
    [@-_]   # 7-bit C1 Fe
    [0-?]*  # Parameter bytes
    [ -/]*  # Intermediate bytes
    [@-~]   # Final byte
''', re.VERBOSE)

do_sanity_checks = True
verbose = False


def confirm_authentication(address, battery):
    print("\t[%s] successfully authenticated. Remaining Battery: %s%%" % (address, battery))


def read_db(coins="coins.txt", central="central.txt"):
    coin_list = []
    identity = None
    with open(coins, "r") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for line in f:
            m = re.match(r"(.{17})\s+(.{32})\s+(.{32})\s+(.{64})", line)
            if m:
                coin_list.append(m.groups())
    with open(central, "r") as f:
        line = f.readline()
        m = re.match(r"(.{17})\s+(.{32})", line)
        if m:
            identity = m.groups()
    return identity, coin_list


def parse_bond(l):
    bond = r"\[(.{17})\] keys: 34, flags: 17\r\n"
    m = re.match(bond, l)
    if m:
        return m.groups()


def parse_spacekey(l):
    spacekey = r"\[(.{17})\] : ([A-F0-9]{2})\.\.\.\r\n"
    m = re.match(spacekey, l)
    if m:
        return m.groups()


class StatusType(IntEnum):
    IDENTITY = 0
    DEVICE_FOUND = 1
    BATTERY_LEVEL = 2
    CONNECTED = 3
    AUTHENTICATED = 4
    DISCONNECTED = 5


def parse_status(l):
    regs = {
        StatusType.IDENTITY: r"<inf> bt_hci_core: Identity: (.{17}) \((.*)\)",
        StatusType.DEVICE_FOUND: r"<inf> app: Device found: \[(.{17})\] \(RSSI (-?\d+)\) \(TYPE (\d)\) \(BONDED (\d)\)",
        StatusType.BATTERY_LEVEL: r"<inf> app: Battery Level: (\d{1,3})%",
        StatusType.CONNECTED: r"<inf> app: Connected: \[(.{17})\]",
        StatusType.AUTHENTICATED: r"<inf> app: KEY AUTHENTICATED. OPEN DOOR PLEASE.",
        StatusType.DISCONNECTED: r"<inf> app: Disconnected: \[(.{17})\] \(reason (\d+)\)",
    }
    for k in regs:
        m = re.search(pattern=regs[k], string=l)
        if m:
            if verbose:
                print('\t', k, m.groups())
            return k, m.groups()
    return None, None


async def serial_fetch_line(s):
    line = (await s.readline_async()).decode(errors='ignore')
    plain_line = ansi_escape.sub('', line)
    return plain_line


async def manage_serial(s: aioserial.AioSerial):
    line = None
    s.write(b'ble_start\r\n')

    config_identity, coin_list = read_db()

    bonds = []
    spacekeys = []

    # list registered bonds
    s.write(b'stats bonds\r\n')
    while not (line and line.endswith('stats bonds\r\n')):
        line = await serial_fetch_line(s)
        print(line, end='', flush=True)
    while line != 'done\r\n':
        line = await serial_fetch_line(s)
        bond = parse_bond(line)
        if bond:
            bonds.append(bond)

    # list registered spacekeys
    s.write(b'stats spacekey\r\n')
    while not line.endswith('stats spacekey\r\n'):
        line = await serial_fetch_line(s)
        print(line, end='', flush=True)
    while line != 'done\r\n':
        line = await serial_fetch_line(s)
        spacekey = parse_spacekey(line)
        if spacekey:
            spacekeys.append(spacekey)

    if do_sanity_checks:
        assert len(bonds) == len(spacekeys)
        for i in zip(bonds, spacekeys, coin_list):
            assert i[0][0] == i[1][0], "addresses must match"
            assert i[2][0] == i[0][0], "addresses must match"
            assert i[2][3][:2] == i[1][1], "spacekey must match"

    battery_level = 0
    coin_address = ""
    # main event loop
    while True:
        line = await serial_fetch_line(s)
        print(line, end='', flush=True)
        k, v = parse_status(line)
        if do_sanity_checks:
            if k == StatusType.IDENTITY:
                assert v[0].upper() == config_identity[0], v
        if k == StatusType.AUTHENTICATED:
            confirm_authentication(coin_address, battery_level)
        elif k == StatusType.BATTERY_LEVEL:
            battery_level = v[0]
        elif k == StatusType.CONNECTED:
            coin_address = v[0]
        elif k == StatusType.DISCONNECTED:
            battery_level = 0
            coin_address = ""


def signal_handler(signum, frame):
    central_serial.write(b"reboot\r\n")
    sys.exit(0)


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    signal.signal(signal.SIGINT, signal_handler)
    while True:
        try:
            central_serial = aioserial.AioSerial(
                port=os.path.realpath('/dev/serial/by-id/usb-ZEPHYR_N39_BLE_KEYKEEPER_0.01-if00'))
            loop.run_until_complete(manage_serial(central_serial))
        except serial.serialutil.SerialException:
            print("LOST CONNECTION. RECONNECTING...", file=sys.stderr)
            loop.run_until_complete(asyncio.sleep(5))
