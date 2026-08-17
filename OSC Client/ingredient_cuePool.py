# coding=utf-8
'''
CuePool ingredient for the OSC Client node.

`rev 1 2026.08.17`

Adds CuePool transport control and a health check on top of the generic OSC
Client recipe. Requires `OSC Client` rev 3 or later, which provides `send_osc`
and `register_message_handler`.

CuePool keeps the `/qplayer` address namespace for compatibility with the
original QPlayer remote protocol, so every address below is `/qplayer/...`
even though the application is CuePool.

Set the node's **Receive port** parameter to enable the health check. CuePool
replies to `/qplayer/remote/ping` with an argument-free
`/qplayer/remote/pong`. Builds up to and including 0.10.1 broadcast that reply
on their configured TX port (8000 by default), so the receive port must be 8000
to hear them. Builds carrying the unicast-pong fix reply directly to whichever
port the ping came from, and any receive port then works.

`/qplayer/save` is deliberately not exposed. It overwrites the commissioned
show file on the target machine, and nothing on a dashboard should be able to
do that by mis-click.

For per-cue buttons, use the base recipe's **Patterns** parameter with the
address `/qplayer/go` and the cue number in the Argument field. That produces a
labelled button per cue without this ingredient needing a cue list of its own.
Patterns also reaches the addresses deliberately left out here, such as
`/dmx/1/1` and `/recorder/record`.
'''

from java.lang import System

# <-- parameters

param_pingIntervalSeconds = Parameter({
    'title': 'Health check interval',
    'desc': 'Seconds between automatic pings. Set to 0 to disable the '
            'automatic check; the Ping action still works. Requires a receive '
            'port to be configured.',
    'schema': {'type': 'integer', 'hint': '60 (default)'},
    'order': next_seq()
})

# -->

# <-- constants

DEFAULT_PING_INTERVAL_SECONDS = 60

PING_ADDRESS = '/qplayer/remote/ping'
PONG_ADDRESS = '/qplayer/remote/pong'

# A pong is expected well inside this; beyond it the machine is treated as down.
PONG_TIMEOUT_SECONDS = 10

# -->

# <-- monitoring

local_event_Connected = LocalEvent({
    'title': 'Connected',
    'group': 'Monitoring',
    'order': next_seq(),
    'schema': {'type': 'boolean'}
})

local_event_LastPong = LocalEvent({
    'title': 'Last pong',
    'group': 'Monitoring',
    'order': next_seq(),
    'schema': {'type': 'string'}
})

local_event_RoundTripMilliseconds = LocalEvent({
    'title': 'Round trip (ms)',
    'group': 'Monitoring',
    'order': next_seq(),
    'schema': {'type': 'integer'}
})

local_event_Status = LocalEvent({
    'title': 'Status',
    'group': 'Status',
    'order': next_seq(),
    'schema': {'type': 'object', 'properties': {
        'level': {'type': 'integer', 'order': 1},
        'message': {'type': 'string', 'order': 2}
    }}
})

# Timestamp of the ping we are waiting on, or None when not waiting.
_pingSentAt = [None]


def _on_osc_message(address, args, source):
    'Registered with the base recipe; called for every decoded inbound message.'
    if address != PONG_ADDRESS:
        return

    sentAt = _pingSentAt[0]
    if sentAt is None:
        # An unsolicited pong: a broadcast reply to somebody else's ping, or a
        # reply that arrived after we had already given up.
        return

    _pingSentAt[0] = None
    roundTrip = int(System.currentTimeMillis() - sentAt)

    local_event_Connected.emit(True)
    local_event_LastPong.emit(str(date_now()))
    local_event_RoundTripMilliseconds.emit(roundTrip)
    local_event_Status.emit({'level': 0, 'message': 'OK'})


def _check_pong_timeout():
    sentAt = _pingSentAt[0]
    if sentAt is None:
        return

    if System.currentTimeMillis() - sentAt < PONG_TIMEOUT_SECONDS * 1000:
        return

    _pingSentAt[0] = None
    local_event_Connected.emit(False)
    local_event_Status.emit({
        'level': 2,
        'message': 'No response from CuePool within %s seconds' % PONG_TIMEOUT_SECONDS
    })


def send_ping(level=1):
    '''
    Sends a health-check ping. Shared by the Ping action and the timer.

    The timer passes level 2 so a node left running does not fill its console
    with one line a minute; an operator pressing Ping gets level 1.
    '''
    if _receivePort <= 0:
        console.warn('Ping needs a receive port; set the "Receive port" parameter.')
        return

    _pingSentAt[0] = System.currentTimeMillis()
    send_osc(PING_ADDRESS, None, level=level)


@local_action({'title': 'Ping', 'group': 'Monitoring', 'order': next_seq()})
def Ping(arg=None):
    send_ping()

# -->

# <-- transport

@local_action({'title': 'Go', 'group': 'Transport', 'order': next_seq(),
               'desc': 'GO. Supply a cue number to fire that cue instead.',
               'schema': {'type': 'string'}})
def Go(arg=None):
    send_osc('/qplayer/go', arg)


@local_action({'title': 'Stop', 'group': 'Transport', 'order': next_seq(),
               'desc': 'Stops everything. Supply a cue number to stop one cue.',
               'schema': {'type': 'string'}})
def Stop(arg=None):
    send_osc('/qplayer/stop', arg)


@local_action({'title': 'Pause', 'group': 'Transport', 'order': next_seq(),
               'desc': 'Pauses everything. Supply a cue number to pause one cue.',
               'schema': {'type': 'string'}})
def Pause(arg=None):
    send_osc('/qplayer/pause', arg)


@local_action({'title': 'Unpause', 'group': 'Transport', 'order': next_seq(),
               'desc': 'Resumes everything. Supply a cue number to resume one cue.',
               'schema': {'type': 'string'}})
def Unpause(arg=None):
    send_osc('/qplayer/unpause', arg)


@local_action({'title': 'Preload', 'group': 'Transport', 'order': next_seq(),
               'desc': 'Decodes a cue and holds it Ready at the given time.',
               'schema': {'type': 'object', 'properties': {
                   'cue': {'type': 'string', 'title': 'Cue number', 'order': 1},
                   'time': {'type': 'string', 'title': 'Time (seconds)', 'hint': '0', 'order': 2}
               }}})
def Preload(arg=None):
    if arg is None:
        console.warn('Preload needs a cue number and a time.')
        return

    cue = (arg.get('cue') or '').strip()
    if len(cue) == 0:
        console.warn('Preload needs a cue number.')
        return

    time = (arg.get('time') or '0').strip()
    send_osc('/qplayer/preload', '%s, %s' % (cue, time))


@local_action({'title': 'Select', 'group': 'Transport', 'order': next_seq(),
               'desc': 'Moves the selection to a cue number.',
               'schema': {'type': 'string'}})
def Select(arg=None):
    send_osc('/qplayer/select', arg)


# CuePool subscribes to /qplayer/up and /qplayer/down but its handlers are
# empty as of 0.10.1 (main.rs: `OscEvent::Up => {}`), so these send correctly
# and the application ignores them. Verified against 0.10.1 on 2026-08-17. They
# are kept so the dashboard is ready when CuePool implements them; use Select
# to move the selection in the meantime.
_UNIMPLEMENTED = 'Sent correctly, but ignored by CuePool up to and including ' \
                 '0.10.1. Use Select to move the selection.'


@local_action({'title': 'Up', 'group': 'Transport', 'order': next_seq(),
               'desc': _UNIMPLEMENTED})
def Up(arg=None):
    send_osc('/qplayer/up', None)


@local_action({'title': 'Down', 'group': 'Transport', 'order': next_seq(),
               'desc': _UNIMPLEMENTED})
def Down(arg=None):
    send_osc('/qplayer/down', None)

# -->

# <-- main

# The base recipe defines main(); extend it rather than replace it, so the
# generic pattern actions and the custom action are still created.
_original_main = globals().get('main')


def main(arg=None):
    if _original_main is not None:
        _original_main(arg)

    register_message_handler(_on_osc_message)

    if _receivePort <= 0:
        local_event_Status.emit({
            'level': 1,
            'message': 'Send only: set a receive port to enable the health check'
        })
        console.info('CuePool ingredient loaded (send only).')
        return

    local_event_Connected.emit(False)
    local_event_Status.emit({'level': 1, 'message': 'Waiting for the first CuePool response'})

    interval = param_pingIntervalSeconds
    if interval is None:
        interval = DEFAULT_PING_INTERVAL_SECONDS

    if interval > 0:
        # CuePool may start after Nodel, so allow it to settle before the first
        # ping rather than raising a false alarm at boot.
        Timer(lambda: send_ping(level=2), interval, 10)
        Timer(_check_pong_timeout, PONG_TIMEOUT_SECONDS, PONG_TIMEOUT_SECONDS)

    console.info('CuePool ingredient loaded, listening on %s.' % _receivePort)

# -->
