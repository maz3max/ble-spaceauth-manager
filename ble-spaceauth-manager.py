import re
import aioserial
import asyncio
import os
import signal
import sys
import serial.serialutil
import fcntl
from enum import IntEnum
from paho.mqtt.publish import single as mqtt_pub
import argparse

# 7-bit C1 ANSI sequences
ansi_escape = re.compile(r'''
    \x1B    # ESC
    [@-_]   # 7-bit C1 Fe
    [0-?]*  # Parameter bytes
    [ -/]*  # Intermediate bytes
    [@-~]   # Final byte
''', re.VERBOSE)

parser = argparse.ArgumentParser(description='BLE Space Authentication Helper script.')
parser.add_argument('--skip-sanity-checks', action='store_true', default=False,
                    help='skip matching with local coin list')
parser.add_argument('--verbose', action='store_true', default=False, help='print more output')
parser.add_argument('--topic', default='Netz39/Things/Door/Command', help='MQTT Topic to publish to')
parser.add_argument('--host', default='localhost', help='MQTT Host to publish to')
parser.add_argument('--port', default=1883, type=int, help='MQTT Host Port to publish to')
parser.add_argument('--qos', default=2, type=int, help='QOS of MQTT message')
parser.add_argument('--msg', default=b"door open", type=bytes, help='MQTT Message payload')
args = parser.parse_args()


def confirm_authentication(address, battery):
    print("\t[%s] successfully authenticated. Remaining Battery: %s%%" % (address, battery))
    mqtt_pub(topic=args.topic, payload=args.msg, qos=args.qos, hostname=args.host)


# read coins and central ids from pseudo-database
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


def _parse_bond(l):
    bond = r"\[(.{17})\] keys: 34, flags: 17\r\n"
    m = re.match(bond, l)
    if m:
        return m.groups()


# list registered bonds
async def request_bonds(s: aioserial.AioSerial):
    bonds = []
    s.write(b'stats bonds\r\n')
    line = None
    while not (line and line.endswith('stats bonds\r\n')):
        line = await serial_fetch_line(s)
        print(line, end='', flush=True)
    while line != 'done\r\n':
        line = await serial_fetch_line(s)
        bond = _parse_bond(line)
        if bond:
            bonds.append(bond)
    return bonds


def _parse_spacekey(l):
    spacekey = r"\[(.{17})\] : ([A-F0-9]{2})\.\.\.\r\n"
    m = re.match(spacekey, l)
    if m:
        return m.groups()


# list registered spacekeys
async def request_spacekeys(s: aioserial.AioSerial):
    spacekeys = []
    s.write(b'stats spacekey\r\n')
    line = None
    while not (line and line.endswith('stats spacekey\r\n')):
        line = await serial_fetch_line(s)
        print(line, end='', flush=True)
    while line != 'done\r\n':
        line = await serial_fetch_line(s)
        spacekey = _parse_spacekey(line)
        if spacekey:
            spacekeys.append(spacekey)
    return spacekeys


class StatusType(IntEnum):
    IDENTITY = 0
    DEVICE_FOUND = 1
    BATTERY_LEVEL = 2
    CONNECTED = 3
    AUTHENTICATED = 4
    DISCONNECTED = 5


# parse status messages
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
            if args.verbose:
                print('\t', k, m.groups())
            return k, m.groups()
    return None, None


# read line and remove color codes
async def serial_fetch_line(s):
    line = (await s.readline_async()).decode(errors='ignore')
    plain_line = ansi_escape.sub('', line)
    return plain_line


# main state machine routine
async def manage_serial(s: aioserial.AioSerial):
    s.write(b'ble_start\r\n')

    if not args.skip_sanity_checks:
        config_identity, coin_list = read_db()
        bonds = await request_bonds(s)
        spacekeys = await request_spacekeys(s)
        assert len(bonds) == len(spacekeys) == len(coin_list), "number of coins does not match"
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
        if not args.skip_sanity_checks:
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


# user-initiated termination
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
