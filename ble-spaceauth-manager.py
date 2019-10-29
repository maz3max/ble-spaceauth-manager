import re
import aioserial
import asyncio
import os
import signal
import sys
import serial.serialutil

# 7-bit C1 ANSI sequences
ansi_escape = re.compile(r'''
    \x1B    # ESC
    [@-_]   # 7-bit C1 Fe
    [0-?]*  # Parameter bytes
    [ -/]*  # Intermediate bytes
    [@-~]   # Final byte
''', re.VERBOSE)


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


def parse_status(l):
    regs = {
        'identity': r"<inf> bt_hci_core: Identity: (.{17}) \((.*)\)",  #
        'device_found': r"<inf> app: Device found: \[(.{17})\] \(RSSI (-?\d+)\) \(TYPE (\d)\) \(BONDED (\d)\)",  #
        'battery_level': r"<inf> app: Battery Level: (\d{1,3})%",
        'connected': r"<inf> app: Connected: \[(.{17})\]",  #
        'authenticated': r"<inf> app: KEY AUTHENTICATED. OPEN DOOR PLEASE.",  #
        'disconnected': r"<inf> app: Disconnected: \[(.{17})\] \(reason (\d+)\)",  #
    }
    for k in regs:
        m = re.search(pattern=regs[k], string=l)
        if m:
            print(k, m.groups())
            break


async def getline(s):
    line = (await s.readline_async()).decode(errors='ignore')
    plain_line = ansi_escape.sub('', line)
    return plain_line


async def manage_serial(s: aioserial.AioSerial):
    line = None
    s.write(b'ble_start\r\n')

    # list registered bonds
    s.write(b'stats bonds\r\n')
    while not (line and line.endswith('stats bonds\r\n')):
        line = await getline(s)
        print(line, end='', flush=True)
    while line != 'done\r\n':
        line = await getline(s)
        print(parse_bond(line))

    # list registered spacekeys
    s.write(b'stats spacekey\r\n')
    while not line.endswith('stats spacekey\r\n'):
        line = await getline(s)
        print(line, end='', flush=True)
    while line != 'done\r\n':
        line = await getline(s)
        print(parse_spacekey(line))

    # main event loop
    while True:
        line = await getline(s)
        print(line, end='', flush=True)
        parse_status(line)


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
