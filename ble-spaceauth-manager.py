import re
import aioserial
import asyncio

# 7-bit C1 ANSI sequences
ansi_escape = re.compile(r'''
    \x1B    # ESC
    [@-_]   # 7-bit C1 Fe
    [0-?]*  # Parameter bytes
    [ -/]*  # Intermediate bytes
    [@-~]   # Final byte
''', re.VERBOSE)


async def manage_serial(s: aioserial.AioSerial):
    s.write(b'ble_start\r\n')
    while True:
        line = (await s.readline_async()).decode(errors='ignore')
        plain_line = ansi_escape.sub('', line)
        print(plain_line, end='', flush=True)


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        manage_serial(aioserial.AioSerial(port='/dev/serial/by-id/usb-ZEPHYR_N39_BLE_KEYKEEPER_0.01-if00')))
