#! /usr/bin/python3

# (C) 2021 by folkert@vanheusden.com

import select
import signal
import socket
import struct
import sys
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

def signal_handler(sig, frame):
    if state:
        print('Terminating program with data...')
        end_file(state['file'])

    else:
        print('Terminating program...')

    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def start_file():
    tm = time.localtime()
    name = 'recording_%04d-%02d-%02d_%02d-%02d-%02d.mid' % (tm.tm_year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec)

    MyMIDI = MIDIFile(numTracks = 16)

    for track in range(0, 16):
        MyMIDI.addTrackName(track, 0, 'Channel %d' % (track + 1))
        MyMIDI.addTempo(track, 0, bpm)

    return (MyMIDI, name)

def end_file(pars):
    with open(pars[1], 'wb') as binfile:
        pars[0].writeFile(binfile)

state = None

pollerObject = select.poll()
pollerObject.register(fd, select.POLLIN)

def t_to_ticks(t):
    return t * (bpm / 60.0)

while True:
    fds = pollerObject.poll(1000)
    now = time.time()

    # end file after 30 minutes of silence
    if state and now - state['latest_msg'] >= inactivity:
        end_file(state['file'])
        print('File %s ended' % state['file'][1])
        state = None

    for descriptor, event in fds:
        data, address = fd.recvfrom(16)

        if state == None:
            state = dict()
            state['started_at'] = now
            state['file'] = start_file()
            print('Started recording to %s' % state['file'][1])
            state['playing'] = dict()

        cmd = data[0] & 0xf0
        ch = data[0] & 0x0f

        note = velocity = None

        if len(data) >= 2:
            note = data[1]

        if len(data) >= 3:
            velocity = data[2]

        if cmd in (0x80, 0x90):  # note on/off
            ch_str = '%d' % ch
            note_str = '%d' % note

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

                print('Played %d (velocity %d) at %f for %d ticks' % (note, velocity, t, duration))
                state['file'][0].addNote(ch, ch, note, t, duration, velocity)

            if velocity > 0:
                if not ch_str in state['playing']:
                    state['playing'][ch_str] = dict()

                state['playing'][ch_str][note_str] = dict()
                state['playing'][ch_str][note_str]['cmd'] = cmd
                state['playing'][ch_str][note_str]['t'] = now
                state['playing'][ch_str][note_str]['velocity'] = velocity

            elif ch_str in state['playing'] and note_str in state['playing'][ch_str]:
                del state['playing'][ch_str][note_str]

        elif cmd == 0xc0:  # program change
            ch_str = '%d' % ch
            note_str = '%d' % note

            since_start = now - state['started_at']
            t = t_to_ticks(since_start)

            program = data[1]
            state['file'][0].addProgramChange(ch, ch, t, program)

        state['latest_msg'] = now
