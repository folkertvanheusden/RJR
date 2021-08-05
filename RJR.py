#! /usr/bin/python3

# (C) 2021 by folkert@vanheusden.com

import queue
import select
import signal
import socket
import struct
import sys
import threading
import time
from midiutil import MIDIFile

multicast_group = '225.0.0.37'
multicast_port = 21928

# after this many seconds of nothing played, the
# midi-file will be closed (after which a new one
# will be created)
inactivity = 1 * 60  # in seconds

# this is a maximum. if you go faster, then increase
# this number
bpm = 480

fd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

fd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

fd.bind((multicast_group, multicast_port))

# join multicast group
group = socket.inet_aton(multicast_group)
mreq = struct.pack('4sL', group, socket.INADDR_ANY)
fd.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

thrds = dict()

def signal_handler(sig, frame):
    print('Terminating program...')

    for t in thrds:
        thrds[t]['q'].put(None)

    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def start_file(address):
    tm = time.localtime()
    name = f'recording_{address[0]}-{address[1]}_{tm.tm_year}-{tm.tm_mon:02d}-{tm.tm_mday:02d}_{tm.tm_hour:02d}-{tm.tm_min:02d}-{tm.tm_sec:02d}.mid'

    MyMIDI = MIDIFile(numTracks = 16)

    MyMIDI.addTrackName(0, 0., 'Track 1')
    MyMIDI.addTempo(0, 0, bpm)

    MyMIDI.addCopyright(0, 0, 'Produced by RJR, (C) 2021 by folkert@vanheusden.com')

    return (MyMIDI, name)

def end_file(pars):
    with open(pars[1], 'wb') as binfile:
        pars[0].writeFile(binfile)

state = None

pollerObject = select.poll()
pollerObject.register(fd, select.POLLIN)

def t_to_ticks(t):
    return t * (bpm / 60.0)

def handler(q, address):
    a = f'{address[0]}:{address[1]}'

    print(f'{time.ctime()}] Thread for {a} started')

    state = None

    while True:
        # end file after 30 minutes of silence
        if state and time.time() - state['latest_msg'] >= inactivity:
            end_file(state['file'])
            print(f"{time.ctime()}] {a} File {state['file'][1]} ended")
            break

        try:
            item = q.get(timeout=0.5)

        except queue.Empty:
            continue

        if not item:
            end_file(state['file'])
            break

        data = item[0]
        now = item[1]

        if state == None:
            state = dict()
            state['started_at'] = now
            state['file'] = start_file(address)
            print(f"{time.ctime()}] {a} Started recording to {state['file'][1]}")
            state['playing'] = dict()

        cmd = data[0] & 0xf0
        ch = data[0] & 0x0f

        note = velocity = None

        if len(data) >= 2:
            note = data[1]

        if len(data) >= 3:
            velocity = data[2]

        if cmd in (0x80, 0x90):  # note on/off
            ch_str = '{ch}'
            note_str = '{note}'

            if ch_str in state['playing'] and note_str in state['playing'][ch_str]:
                # emit
                # using channel as track number
                since_start = state['playing'][ch_str][note_str]['t'] - state['started_at']
                t = t_to_ticks(since_start)

                if ch == 9:  # percussion
                    duration = t_to_ticks(1)

                else:
                    since_now = now - state['playing'][ch_str][note_str]['t']
                    duration = t_to_ticks(since_now)

                velocity = state['playing'][ch_str][note_str]['velocity']

                print(f'{time.ctime()}] {a} Played {note} (velocity {velocity}) at {t:.3f} for {duration:.3f} ticks')
                state['file'][0].addNote(0, ch, note, t, duration, velocity)

            if velocity > 0:
                if not ch_str in state['playing']:
                    state['playing'][ch_str] = dict()

                state['playing'][ch_str][note_str] = dict()
                state['playing'][ch_str][note_str]['cmd'] = cmd
                state['playing'][ch_str][note_str]['t'] = now
                state['playing'][ch_str][note_str]['velocity'] = velocity

            elif ch_str in state['playing'] and note_str in state['playing'][ch_str]:
                del state['playing'][ch_str][note_str]

        elif cmd == 0xb0:  # controller change
            cc = data[1]
            parameter = data[2]

            print(f'{time.ctime()}] {a} Channel {ch} controller {cc} change to {parameter}')

            since_start = now - state['started_at']
            t = t_to_ticks(since_start)

            state['file'][0].addControllerEvent(0, ch, t, cc, parameter)

        elif cmd == 0xc0:  # program change
            since_start = now - state['started_at']
            t = t_to_ticks(since_start)

            program = data[1]
            state['file'][0].addProgramChange(0, ch, t, program)

            print(f'{time.ctime()}] {a} Channel {ch} program change to {program}')

        elif cmd == 0xe0:  # pitch wheel
            since_start = now - state['started_at']
            t = t_to_ticks(since_start)

            value = (data[1] << 7) | data[2]
            if value >= 0x4000:
                value = -(0x8000 - value)

            state['file'][0].addPitchWheelEvent(0, ch, t, value)

        state['latest_msg'] = now

    print(f'{time.ctime()}] Thread for {address[0]}:{address[1]} terminating')

while True:
    fds = pollerObject.poll(1000)
    now = time.time()

    for descriptor, event in fds:
        data, address = fd.recvfrom(16)

        if not address in thrds:
            thrds[address] = dict()
            thrds[address]['q'] = queue.Queue()
            thrds[address]['th'] = threading.Thread(target=handler, args=(thrds[address]['q'], address,))
            thrds[address]['th'].start()

        thrds[address]['q'].put((data, now))

    del_queue = []

    for t in thrds:
        thrds[t]['th'].join(timeout=0.000001)

        if not thrds[t]['th'].is_alive():
            del_queue.append(t)

    for d in del_queue:
        del thrds[d]
