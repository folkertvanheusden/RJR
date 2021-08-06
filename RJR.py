#! /usr/bin/python3

# (C) 2021 by folkert@vanheusden.com

import getopt
import queue
import select
import signal
import socket
import struct
import sys
import threading
import time
from mido import bpm2tempo, Message, MetaMessage, MidiFile, MidiTrack, second2tick

is_multicast = False
bind_addr = '127.0.0.1'
bind_port = 21928

# after this many seconds of nothing played, the
# midi-file will be closed (after which a new one
# will be created)
inactivity = 1 * 60  # in seconds

# this is a maximum. if you go faster, then increase
# this number
bpm = 960

ppqn = 64

# minimum size of output
min_size = 0

def usage():
    print('-a x   bind to listen address x')
    print('-p x   port to bind to')
    print('-m     address/port is a multicast group')
    print('-i x   inactivity timer (in seconds, optional)')
    print('-b x   BPM (optional)')
    print('-q x   PPQN (optional)')
    print('-n x   minimum size, in notes (optional)')

try:
    opts, args = getopt.getopt(sys.argv[1:], 'a:p:mi:b:q:n:')

except getopt.GetoptError as err:
    print(err)
    usage()
    sys.exit(1)

for o, a in opts:
    if o in ("-a"):
        bind_addr = a

    elif o in ("-p"):
        bind_port = int(a)

    elif o in ("-m"):
        is_multicast = True

    elif o in ("-i"):
        inactivity = float(a)

    elif o in ("-b"):
        bpm = a

    elif o in ("-q"):
        ppqn = a

    elif o in ("-n"):
        min_size = int(a)

    else:
        usage()
        assert False, "unhandled option"

fd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

fd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

fd.bind((bind_addr, bind_port))

# join multicast group
if is_multicast:
    group = socket.inet_aton(bind_addr)
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

    track = MidiTrack()

    track.append(MetaMessage('copyright', text='RJR (C) 2021 by folkert@vanheusden.com'))

    track.append(MetaMessage('set_tempo', tempo=bpm2tempo(bpm)))

    return (track, name)

def end_file(pars):
    mid = MidiFile(ticks_per_beat=ppqn)

    if len(pars[0]) >= min_size:
        mid.tracks.append(pars[0])
        mid.save(pars[1])

    else:
        print(f'Not storing file: not long enough (is {len(pars[0])} MIDI messages in length currently)')

def t_to_tick(ts, p_ts):
    return int(second2tick(ts - p_ts, ppqn, bpm2tempo(bpm)))

state = None

pollerObject = select.poll()
pollerObject.register(fd, select.POLLIN)

def handler(q, address):
    a = f'{address[0]}:{address[1]}'

    print(f'{time.ctime()}] Thread for {a} started')

    state = None

    while True:
        # end file after x seconds of silence
        if state and time.time() - state['latest_msg'] >= inactivity:
            end_file(state['file'])
            print(f"{time.ctime()}] {a} File {state['file'][1]} ended")
            state = None
            break

        try:
            item = q.get(timeout=0.5)

        except queue.Empty:
            continue

        if not item:
            end_file(state['file'])
            state = None
            break

        data = item[0]
        now = item[1]

        if state == None:
            state = dict()
            state['latest_msg'] = state['started_at'] = now
            state['file'] = start_file(address)
            print(f"{time.ctime()}] {a} Started recording to {state['file'][1]}")
            state['playing'] = dict()

        cmd = data[0] & 0xf0
        ch = data[0] & 0x0f

        if cmd in (0x80, 0x90):  # note on/off
            note = data[1]
            velocity = data[2]

            t = t_to_tick(now, state['latest_msg'])

            state['file'][0].append(Message('note_on' if cmd == 0x90 else 'note_off', channel=ch, note=note, velocity=velocity, time=t))

            print(f'{time.ctime()}] {a} Played {note} (velocity {velocity}) at {t}')

        elif cmd == 0xb0:  # controller change
            cc = data[1]
            parameter = data[2]

            print(f'{time.ctime()}] {a} Channel {ch} controller {cc} change to {parameter}')

            t = t_to_tick(now, state['latest_msg'])

            state['file'][0].append(Message('control_change', channel=ch, control=cc, value=parameter, time=t))

        elif cmd == 0xc0:  # program change
            program = data[1]

            print(f'{time.ctime()}] {a} Channel {ch} program change to {program}')

            t = t_to_tick(now, state['latest_msg'])

            state['file'][0].append(Message('program_change', channel=ch, program=program, time=t))

        elif cmd == 0xe0:  # pitch wheel
            t = t_to_tick(now, state['latest_msg'])

            value = (data[1] << 7) | data[2]
            if value >= 0x4000:
                value = -(0x8000 - value)

            state['file'][0].append(Message('pitchwheel', channel=ch, pitch=value, time=t))

        if cmd < 0xf0:
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
