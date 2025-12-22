'''
_(rev 1)_

**PM2 Process Controller** - Manages long-running applications through [PM2](https://pm2.keymetrics.io/)

Designed for museum interactives and kiosk applications - Electron apps, Unity apps, MadMapper, TouchDesigner, VLC, etc.

**Features:**
- Process lifecycle management (start, stop, restart)
- Real-time status updates via PM2 event bus
- Metrics monitoring (CPU, memory, restart count)
- Log streaming to Nodel console
- Auto-restart on crash

**Requirements:**
- Node.js on system PATH
- PM2 installed globally via [npm](https://www.npmjs.com/package/pm2) or [bun](https://bun.sh):

```
npm install pm2 -g
```
or
```
bun install pm2 -g
```
'''

NPM_MODULE = 'pm2'
JS_ENTRYPOINT = 'go.js'

import os

# === PARAMETERS ===

param_disabled = Parameter({'desc': 'Disables this node', 'schema': {'type': 'boolean'}})

param_AppPath = Parameter({
    'title': 'Application Path (required)',
    'required': True,
    'schema': {'type': 'string', 'hint': '(e.g. "C:\\apps\\myapp.exe" or "/home/user/MyApp")'},
    'desc': 'Full path to the application executable to run'
})

param_Name = Parameter({
    'title': 'PM2 Process Name',
    'schema': {'type': 'string', 'hint': '(defaults to node name if blank)'},
    'desc': 'Name used to identify this process in PM2'
})

param_Args = Parameter({
    'title': 'Script Arguments',
    'schema': {'type': 'string', 'hint': 'e.g. --port 3000 --env production'},
    'desc': 'Arguments passed to the script (space-delimited)'
})

param_Cwd = Parameter({
    'title': 'Working Directory',
    'schema': {'type': 'string', 'hint': 'e.g. C:\\apps\\myapp'},
    'desc': 'Working directory for the process'
})

param_MaxMemory = Parameter({
    'title': 'Max Memory Restart',
    'schema': {'type': 'string', 'hint': '(e.g. "500M", "1G")'},
    'desc': 'Restart when memory exceeds this limit'
})

param_MaxRestarts = Parameter({
    'title': 'Max Restarts',
    'schema': {'type': 'integer', 'hint': '(default: 16)'},
    'desc': 'Maximum number of consecutive restarts before stopping'
})

param_AutoRestart = Parameter({
    'title': 'Auto Restart',
    'schema': {'type': 'boolean'},
    'desc': 'Automatically restart on crash (default: true)'
})

param_Watch = Parameter({
    'title': 'Watch for Changes',
    'schema': {'type': 'boolean'},
    'desc': 'Restart when file changes detected'
})

param_EnvVars = Parameter({
    'title': 'Environment Variables',
    'schema': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'key': {'type': 'string', 'order': 1},
        'value': {'type': 'string', 'order': 2}
    }}},
    'desc': 'Environment variables to set for the process'
})

param_PowerStateOnStart = Parameter({
    'title': 'Running state on Node Start',
    'schema': {'type': 'string', 'enum': ['On', 'Off', '(previous)']},
    'desc': 'What power state to start in when the node boots'
})

param_LogStreaming = Parameter({
    'title': 'Stream Logs to Console',
    'schema': {'type': 'boolean'},
    'desc': 'Stream PM2 logs to Nodel console'
})

param_bridgeConfig = Parameter({
    'title': 'Bridge Configuration (Advanced)',
    'schema': {'type': 'object', 'properties': {
        'workingDir': {'type': 'string', 'hint': '(default is node home)', 'desc': 'Working directory for the Node.js bridge'},
        'nodejsDir': {'type': 'string', 'hint': '(default will use system path)', 'desc': 'Path to Node.js if not on PATH'}
    }},
    'desc': 'Advanced: Configure the Node.js bridge process (not the managed application)'
})


# === CORE EVENTS (defined statically for power state management) ===

local_event_Running = LocalEvent({
    'group': 'Power', 'order': 1,
    'schema': {'type': 'string', 'enum': ['On', 'Off']},
    'desc': 'Actual running state of the PM2 process'
})

local_event_DesiredPower = LocalEvent({
    'group': 'Power', 'order': 2,
    'schema': {'type': 'string', 'enum': ['On', 'Off']},
    'desc': 'The desired power state, set using the action'
})

local_event_Power = LocalEvent({
    'group': 'Power', 'order': 3,
    'schema': {'type': 'string', 'enum': ['On', 'Partially On', 'Off', 'Partially Off']},
    'desc': 'Effective power state using Nodel conventions'
})

local_event_PowerOn = LocalEvent({'group': 'Power', 'title': 'On', 'order': 4, 'schema': {'type': 'boolean'}})
local_event_PowerOff = LocalEvent({'group': 'Power', 'title': 'Off', 'order': 5, 'schema': {'type': 'boolean'}})

local_event_LastStarted = LocalEvent({
    'group': 'Monitoring', 'order': 50,
    'schema': {'type': 'string'},
    'desc': 'Last time the process was started'
})

local_event_FirstInterrupted = LocalEvent({
    'group': 'Monitoring', 'order': 51,
    'schema': {'type': 'string'},
    'desc': 'First time the process crashed unexpectedly'
})

local_event_LastInterrupted = LocalEvent({
    'group': 'Monitoring', 'order': 52,
    'schema': {'type': 'string'},
    'desc': 'Last time the process crashed unexpectedly'
})

local_event_Status = LocalEvent({
    'order': -100, 'group': 'Status',
    'schema': {'type': 'object', 'properties': {
        'level': {'type': 'integer'},
        'message': {'type': 'string'}
    }}
})


# === STATE ===

_process = None
_workingDir = None
_previousRunning = None


# === MAIN ===

def main():
    if param_disabled:
        console.warn('Node is disabled')
        return

    if is_blank(param_AppPath):
        console.warn('No application path specified - configure Application Path parameter')
        return

    # Determine working directory
    global _workingDir
    workingDir = (param_bridgeConfig or EMPTY).get('workingDir')

    if is_blank(workingDir):
        _workingDir = _node.getRoot().getAbsolutePath()
    else:
        if not os.path.exists(workingDir):
            console.warn('Working directory %s does not exist!' % workingDir)
            return
        _workingDir = workingDir

    console.info('PM2 Controller starting...')
    console.info('Application: %s' % param_AppPath)
    console.info('Working directory: %s' % _workingDir)

    prepareNPMpackages()


def prepareNPMpackages():
    '''Ensure pm2 module is installed locally for the Node.js bridge'''

    pm2Path = os.path.join(_workingDir, 'node_modules', 'pm2')

    if os.path.exists(pm2Path):
        console.info('%s: ready' % NPM_MODULE)
        kickOffNodejsProcess()
    else:
        console.info('%s: installing locally (one-time)...' % NPM_MODULE)
        installPM2locally()


def installPM2locally():
    '''Install pm2 module locally for the bridge to use'''
    nodejsDir = (param_bridgeConfig or EMPTY).get('nodejsDir')
    npmbin = 'npm' if is_blank(nodejsDir) else os.path.join(nodejsDir, 'npm')

    if 'windir' in os.environ:
        npmbin = npmbin + '.cmd'

    def finished(state):
        if state.code != 0:
            console.warn('%s: npm install may have failed (exit code %s)' % (NPM_MODULE, state.code))
            console.warn('stdout: %s' % state.stdout)
            console.warn('stderr: %s' % state.stderr)
        else:
            console.info('%s: installed locally' % NPM_MODULE)

        kickOffNodejsProcess()

    cmd = [npmbin, 'install', NPM_MODULE]
    console.info('Installing: %s' % ' '.join(cmd))
    quick_process(cmd, working=_workingDir, mergeErr=True, finished=finished)


def kickOffNodejsProcess():
    nodejsDir = (param_bridgeConfig or EMPTY).get('nodejsDir')
    nodebin = 'node' if is_blank(nodejsDir) else os.path.join(nodejsDir, 'node')

    global _process
    cmds = [nodebin, JS_ENTRYPOINT]

    _process = Process(cmds,
                       started=process_started,
                       stopped=process_stopped,
                       stderr=lambda data: console.warn('stderr> %s' % data),
                       stdout=handle_stdout,
                       stdin=None,
                       mergeErr=False)

    if _workingDir:
        _process.setWorking(_workingDir)

    console.info('Launching: %s' % ' '.join(cmds))


def process_started():
    console.info('Node.js bridge started')
    # Send configuration to go.js
    sendConfig()


def process_stopped(exitCode):
    console.warn('Node.js bridge stopped (exit code %s)' % exitCode)
    # Could implement restart logic here


def sendConfig():
    '''Send process configuration to go.js'''
    processName = param_Name if not is_blank(param_Name) else _node.getName()

    config = {
        'script': param_AppPath,
        'name': processName,
        'logStreaming': param_LogStreaming or False
    }

    # Optional parameters
    if not is_blank(param_Args):
        config['args'] = param_Args

    if not is_blank(param_Cwd):
        config['cwd'] = param_Cwd

    if not is_blank(param_MaxMemory):
        config['maxMemory'] = param_MaxMemory

    if param_MaxRestarts is not None:
        config['maxRestarts'] = param_MaxRestarts

    if param_AutoRestart is not None:
        config['autoRestart'] = param_AutoRestart

    if param_Watch is not None:
        config['watch'] = param_Watch

    # Environment variables
    if param_EnvVars:
        env = {}
        for item in param_EnvVars:
            key = item.get('key')
            value = item.get('value')
            if key:
                env[key] = value or ''
        if env:
            config['env'] = env

    message = json_encode({'config': config})
    console.info('Sending config: %s' % message)
    _process.sendNow(message)

    # Handle initial power state
    handlePowerStateOnStart()


def handlePowerStateOnStart():
    if param_PowerStateOnStart == 'On':
        lookup_local_action('Power').call('On')
    elif param_PowerStateOnStart == 'Off':
        lookup_local_action('Power').call('Off')
    else:
        # (previous) - check stored state
        if local_event_DesiredPower.getArg() == 'On':
            lookup_local_action('Power').call('On')


# === STDOUT HANDLER ===

def handle_stdout(line):
    log(1, 'stdout> %s' % line)

    json_message = line.strip()

    # Ignore comments
    if json_message.startswith('#'):
        return

    # Ignore non-JSON
    if not json_message.startswith('{'):
        return

    try:
        message = json_decode(json_message)
        handle_message(message)
    except Exception, e:
        console.warn('Failed to parse JSON: %s' % str(e))
        console.warn('Raw message: %s' % json_message[:200])


def handle_message(message):
    # Handle events
    event = message.get('event')
    if event:
        handle_event(event, message.get('arg'))
        return

    # Handle log streaming
    logType = message.get('log')
    if logType:
        handle_log(logType, message.get('data'))
        return

    # Handle metadata (dynamic reflection)
    actions = message.get('actions')
    events = message.get('events')

    if actions:
        process_actions_reflection(actions)

    if events:
        process_events_reflection(events)


def handle_event(name, arg):
    '''Handle events from go.js'''
    global _previousRunning

    # Special handling for Running event
    if name == 'Running':
        wasRunning = _previousRunning
        _previousRunning = arg

        local_event_Running.emit(arg)
        determinePower()

        # Detect start
        if arg == 'On' and wasRunning != 'On':
            local_event_LastStarted.emit(str(date_now()))

        # Detect interruption (stopped when we wanted it on)
        if arg == 'Off' and wasRunning == 'On':
            if local_event_DesiredPower.getArg() == 'On':
                recordInterruption()

        return

    # Forward other events to dynamically created local events
    evt = lookup_local_event(name)
    if evt:
        evt.emit(arg)
    else:
        log(2, 'Unhandled event: %s = %s' % (name, arg))


def handle_log(logType, data):
    '''Handle log output from PM2'''
    if logType == 'err':
        console.warn('pm2> %s' % data)
    else:
        console.info('pm2> %s' % data)


# === DYNAMIC REFLECTION ===

def process_actions_reflection(actions):
    '''Create local actions from go.js metadata'''
    for i in range(len(actions)):
        actionDef = actions[i]
        name = actionDef['name']
        metadata = actionDef.get('metadata') or {}

        # Skip actions we handle specially
        if name in ['Power', 'PowerOn', 'PowerOff']:
            continue

        create_action(name, metadata)


def create_action(name, metadata):
    '''Create a single action that forwards to go.js'''
    def handler(arg, actionName=name):
        sendAction(actionName, arg)

    Action(name, handler, metadata)


def process_events_reflection(events):
    '''Create local events from go.js metadata'''
    for i in range(len(events)):
        eventDef = events[i]
        name = eventDef['name']
        metadata = eventDef.get('metadata') or {}

        # Skip events we define statically
        if name in ['Running', 'Power', 'DesiredPower', 'PowerOn', 'PowerOff',
                    'LastStarted', 'FirstInterrupted', 'LastInterrupted', 'Status']:
            continue

        Event(name, metadata)


def sendAction(name, arg=None):
    '''Send an action to go.js'''
    message = {'action': name}
    if arg is not None:
        message['arg'] = arg
    _process.sendNow(json_encode(message))


# === POWER ACTIONS ===

@local_action({
    'group': 'Power', 'order': 1,
    'schema': {'type': 'string', 'enum': ['On', 'Off']},
    'desc': 'Start or stop the PM2 process'
})
def Power(arg):
    # Clear interruption tracking on manual power change
    local_event_FirstInterrupted.emit('')

    local_event_DesiredPower.emit(arg)
    sendAction('Power', arg)


@local_action({'group': 'Power', 'title': 'On', 'order': 2})
def PowerOn():
    Power.call('On')


@local_action({'group': 'Power', 'title': 'Off', 'order': 3})
def PowerOff():
    Power.call('Off')


# === POWER STATE LOGIC ===

def determinePower():
    '''Calculate effective power state from desired and actual'''
    desired = local_event_DesiredPower.getArg()
    running = local_event_Running.getArg()

    if desired is None:
        state = running
    elif desired == running:
        state = running
    else:
        state = 'Partially %s' % desired

    local_event_Power.emit(state)
    local_event_PowerOn.emit(running == 'On')
    local_event_PowerOff.emit(running == 'Off')


@after_main
def bindPowerEvents():
    local_event_Running.addEmitHandler(lambda arg: determinePower())
    local_event_DesiredPower.addEmitHandler(lambda arg: determinePower())


# === INTERRUPTION TRACKING ===

def recordInterruption():
    '''Record when process stops unexpectedly'''
    nowStr = str(date_now())
    local_event_LastInterrupted.emit(nowStr)

    # Only set FirstInterrupted once
    if len(local_event_FirstInterrupted.getArg() or '') == 0:
        local_event_FirstInterrupted.emit(nowStr)

    console.warn('Process interruption detected at %s' % nowStr)


# === SIGNAL PERSISTENCE ===

@after_main
def ensurePersistSignals():
    def ensure(s):
        s.addEmitHandler(lambda arg: s.persistNow())

    for s in [local_event_Running, local_event_DesiredPower, local_event_Power,
              local_event_LastStarted, local_event_FirstInterrupted, local_event_LastInterrupted]:
        ensure(s)


# === STATUS CHECK ===

def statusCheck():
    '''Periodic health check'''
    now = date_now()
    nowMillis = now.getMillis()

    # Check for recent interruptions (4 day window)
    firstInterrupted = date_parse(local_event_FirstInterrupted.getArg() or '1960')
    diff = nowMillis - firstInterrupted.getMillis()

    if diff < 4 * 24 * 3600 * 1000:
        lastInterrupted = date_parse(local_event_LastInterrupted.getArg() or '1960')
        local_event_Status.emit({
            'level': 1,
            'message': 'Process interruptions detected (last: %s)' % toBriefTime(lastInterrupted)
        })
        return

    # Check if supposed to be running but isn't
    if local_event_DesiredPower.getArg() == 'On' and local_event_Running.getArg() != 'On':
        local_event_Status.emit({
            'level': 2,
            'message': 'Process is not running'
        })
        return

    local_event_Status.emit({'level': 0, 'message': 'OK'})


statusCheck_timer = Timer(statusCheck, 30)


# === UTILITIES ===

def toBriefTime(dateTime):
    '''Convert datetime to brief relative time string'''
    now = date_now()
    nowMillis = now.getMillis()
    diff = (nowMillis - dateTime.getMillis()) / 60000  # in minutes

    if diff == 0:
        return '<1 min ago'
    elif diff < 60:
        return '%s mins ago' % int(diff)
    elif diff < 24 * 60:
        return dateTime.toString('h:mm:ss a')
    elif diff < 365 * 24 * 60:
        return dateTime.toString('h:mm:ss a, E d-MMM')
    elif diff > 10 * 365 * 24 * 60:
        return 'never'
    else:
        return '>1 year'


# === LOGGING ===

local_event_LogLevel = LocalEvent({
    'group': 'Debug', 'order': 10000,
    'desc': 'Use this to ramp up the logging (with indentation)',
    'schema': {'type': 'integer'}
})


def warn(level, msg):
    if (local_event_LogLevel.getArg() or 0) >= level:
        console.warn(('  ' * level) + msg)


def log(level, msg):
    if (local_event_LogLevel.getArg() or 0) >= level:
        console.log(('  ' * level) + msg)


# === INITIALIZATION ===

@before_main
def initRunningState():
    local_event_Running.emit('Off')
