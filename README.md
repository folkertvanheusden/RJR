RJR
---
"Random Jamming Recorder" automatically records MIDI streams that come in through multicast (IP-MIDI)


installation
------------
This program requires the mido module:
    apt install python3-mido


usage
-----
Run RJR.py with the '-h' switch to get a list of options.


note
----
If you miss data (notes, program changes, etc), check if your network drops multicast packets.
Also consider using the unicast option (e.g. not using the -m switch).


see also
--------
RJRalsa - same thing, but for (local) ALSA connections.


(C) 2021 by folkert@vanheusden.com
License: GPLv3
