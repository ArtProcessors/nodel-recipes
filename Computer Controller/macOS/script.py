'''
**macOS Computer Controller** with native system operations via Swift companion.

`rev 2 2025.12.23`

Includes:

* sleep, prevent sleep (caffeinate)
* power settings (WoL, auto-restart, power nap, TCP keep-alive)
* periodic screenshots
* volume control, audio metering
* CPU monitoring
* hardware information
* disk space monitoring

Requires Xcode Command Line Tools for Swift compilation.

**REVISION HISTORY**

* rev 2: added TCP Keep-Alive control; PowerOff now disables Power Nap and TCP Keep-Alive for true sleep (WoL-compatible)
* rev 1: initial release

'''

import os

# <!-- Power settings helpers (requires sudoers allowlist for write operations)

def _set_power_setting(key, enabled):
    '''Set a pmset power setting (requires sudoers allowlist).'''
    val_str = '1' if enabled else '0'
    def on_result(result):
        if result.code != 0:
            stderr = result.stderr or ''
            if 'password is required' in stderr or 'sudo:' in stderr:
                console.warn('%s: sudo not configured - run install-sudoers.sh' % key)
            else:
                console.warn('pmset %s failed: %s' % (key, stderr.strip() or 'unknown'))
        # Re-emit settings after change attempt (will show actual state)
        _emit_power_settings()
    quick_process(['/usr/bin/sudo', '-n', '/usr/bin/pmset', '-a', key, val_str], finished=on_result)

def _emit_power_settings():
    '''Read and emit all power settings as events.'''
    def on_result(result):
        if result.code != 0:
            console.warn('pmset -g failed: %s' % (result.stderr or result.stdout or 'unknown'))
            return
        if not result.stdout:
            return
        for line in result.stdout.split('\n'):
            line = line.strip()
            parts = line.split()
            if len(parts) >= 2:
                key, value = parts[0], parts[1]
                enabled = (value == '1')
                if key == 'womp':
                    local_event_WakeOnLAN.emit(enabled)
                elif key == 'autorestart':
                    local_event_AutoRestartOnPowerLoss.emit(enabled)
                elif key == 'networkoversleep':
                    local_event_WakeForNetworkAccess.emit(enabled)
                elif key == 'powernap':
                    local_event_PowerNap.emit(enabled)
                elif key == 'tcpkeepalive':
                    local_event_TCPKeepAlive.emit(enabled)
    quick_process(['/usr/bin/pmset', '-g'], finished=on_result)

# -->

# <!-- Parameters

DEFAULT_FREESPACE_GB = 0.5

param_FreeSpaceThreshold = Parameter({ 'title': 'Freespace threshold (GB)', 'schema': { 'type': 'number', 'hint': '(0 to disable, default %s)' % DEFAULT_FREESPACE_GB }})

param_EnableScreenshots = Parameter({ 'title': 'Enable screenshots', 'group': 'Screenshots', 'schema': { 'type': 'boolean', 'default': False }})

param_ScreenshotInterval = Parameter({ 'title': 'Screenshot interval (s)', 'group': 'Screenshots', 'schema': { 'type': 'integer', 'hint': '(0 to disable, default 60 when enabled)' }})

param_EnableAudioMeter = Parameter({ 'title': 'Enable audio meter', 'group': 'Audio', 'schema': { 'type': 'boolean', 'default': False }})

param_EnableCPUPolling = Parameter({ 'title': 'Enable CPU polling', 'group': 'Monitoring', 'schema': { 'type': 'boolean', 'default': False }})

param_EnableVolumePolling = Parameter({ 'title': 'Enable volume polling', 'group': 'Volume', 'schema': { 'type': 'boolean', 'default': False }})

param_PowerOffMode = Parameter({ 'title': 'Power Off mode', 'group': 'Power', 'schema': { 'type': 'string', 'enum': ['Sleep', 'Shutdown'], 'hint': 'Sleep is WOL compatible (default), Shutdown is not' }})

# -->

# <!-- CPU

local_event_CPU = LocalEvent({ 'order': next_seq(), 'schema': { 'type': 'number' }})

# -->

# <!-- Power actions

@local_action({ 'group': 'Power', 'order': next_seq() })
def Wake():
    console.info('Wake action - waking display')
    quick_process(['caffeinate', '-u', '-t', '1'])

@local_action({ 'group': 'Power', 'order': next_seq() })
def Sleep():
    console.info('Sleep action')
    quick_process(['pmset', 'sleepnow'])

local_event_PreventSleep = LocalEvent({ 'group': 'Power Settings', 'order': next_seq(), 'desc': 'Uses macOS caffeinate (-d -i) to prevent idle and display sleep.', 'schema': { 'type': 'boolean' }})

_prevent_sleep = Process(['/usr/bin/caffeinate', '-d', '-i'])
_prevent_sleep.stop()
_prevent_sleep_active = False

@local_action({ 'group': 'Power Settings', 'order': next_seq(), 'desc': 'Toggles macOS caffeinate (-d -i) to prevent idle and display sleep.', 'schema': { 'type': 'boolean' } })
def PreventSleep(arg):
    console.info('PreventSleep %s action' % arg)
    global _prevent_sleep_active
    if arg in [ True, 1, 'On', 'ON', 'on' ]:
        if not _prevent_sleep_active:
            _prevent_sleep.start()
            _prevent_sleep_active = True
        local_event_PreventSleep.emit(True)
    elif arg in [ False, 0, 'Off', 'OFF', 'off' ]:
        if _prevent_sleep_active:
            _prevent_sleep.stop()
            _prevent_sleep_active = False
        local_event_PreventSleep.emit(False)
    else:
        console.warn('PreventSleep: invalid arg %s' % arg)

@local_action({ 'title': 'Power Off', 'group': 'Power', 'order': next_seq(), 'desc': 'Uses Sleep (WOL compatible) or Shutdown based on Power Off mode parameter.' })
def PowerOff():
    mode = param_PowerOffMode or 'Sleep'
    if mode == 'Shutdown':
        console.warn('PowerOff action (Shutdown mode) - this CANNOT be recovered via Wake-on-LAN!')
        quick_process(['osascript', '-e', 'tell application "System Events" to shut down'])
    else:
        console.info('PowerOff action (Sleep mode - WOL compatible)')
        # Disable our own prevent-sleep first
        PreventSleep.call(False)
        # Kill any other caffeinate processes (e.g., KeepingYouAwake)
        quick_process(['pkill', 'caffeinate'])
        # Disconnect screen sharing sessions (prevents system sleep)
        quick_process(['pkill', 'screensharingd'])
        # Disable Power Nap and TCP Keep-Alive for true sleep (enables WoL)
        _set_power_setting('powernap', False)
        _set_power_setting('tcpkeepalive', False)
        # Delay to let settings apply, then sleep
        call(lambda: quick_process(['pmset', 'sleepnow']), 2)

@local_action({ 'title': 'Shutdown', 'group': 'Power', 'order': next_seq(),
                'desc': 'Tries unattended (sudo) first, falls back to attended (AppleScript).',
                'caution': 'WARNING: Cannot be recovered via Wake-on-LAN. Requires physical power button or auto-restart after power loss.' })
def Shutdown():
    console.warn('Shutdown action - this CANNOT be recovered via Wake-on-LAN!')
    def on_unattended_result(result):
        if result.code != 0:
            stderr = result.stderr or ''
            if 'password is required' in stderr or 'sudo:' in stderr:
                console.info('Unattended shutdown unavailable, falling back to attended')
            else:
                console.warn('Unattended shutdown failed: %s - falling back to attended' % (stderr.strip() or result.stdout or 'unknown'))
            quick_process(['osascript', '-e', 'tell application "System Events" to shut down'])
    quick_process(['/usr/bin/sudo', '-n', '/sbin/shutdown', '-h', 'now'], finished=on_unattended_result)

@local_action({ 'title': 'Restart', 'group': 'Power', 'order': next_seq(),
                'desc': 'Tries unattended (sudo) first, falls back to attended (AppleScript).' })
def Restart():
    console.info('Restart action')
    def on_unattended_result(result):
        if result.code != 0:
            stderr = result.stderr or ''
            if 'password is required' in stderr or 'sudo:' in stderr:
                console.info('Unattended restart unavailable, falling back to attended')
            else:
                console.warn('Unattended restart failed: %s - falling back to attended' % (stderr.strip() or result.stdout or 'unknown'))
            quick_process(['osascript', '-e', 'tell application "System Events" to restart'])
    quick_process(['/usr/bin/sudo', '-n', '/sbin/shutdown', '-r', 'now'], finished=on_unattended_result)

# -->

# <!-- Network

@local_action({ 'title': 'Emit MAC Addresses', 'group': 'Network', 'order': next_seq() })
def EmitMACAddresses():
    _controller.send('emit-mac-addresses')

# -->

# <!-- Volume and mute

local_event_Mute = LocalEvent({ 'group': 'Volume', 'order': next_seq(), 'schema': { 'type': 'boolean' }})

@local_action({ 'group': 'Mute', 'order': next_seq(), 'schema': { 'type': 'boolean' } })
def Mute(arg):
    console.info('Mute %s action' % arg)
    if arg in [ True, 1, 'On', 'ON', 'on' ]:
        state = True
    elif arg in [ False, 0, 'Off', 'OFF', 'off']:
        state = False
    else:
        console.warn('Mute: arg missing')
        return

    _controller.send('set-mute %s' % ('true' if state else 'false'))

@local_action({ 'title': 'On', 'group': 'Mute', 'order': next_seq() })
def MuteOn():
    Mute.call(True)

@local_action({ 'title': 'Off', 'group': 'Mute', 'order': next_seq() })
def MuteOff():
    Mute.call(False)

local_event_Volume = LocalEvent({ 'title': 'Volume (%)', 'group': 'Volume', 'order': next_seq(), 'schema': {'type': 'number' }})


@local_action({ 'title': 'Volume (%)', 'group': 'Volume', 'order': next_seq(), 'schema': { 'type': 'integer', 'format': 'range', 'min': 0, 'max': 100 }})
def Volume(arg):
    console.info('Volume %s action' % arg)

    if arg == None or arg < 0 or arg > 100:
        console.warn('Volume: no arg or outside 0 - 100')
        return

    # Convert percentage to 0.0-1.0 scalar
    _controller.send('set-volume %s' % (arg / 100.0))

local_event_AudioMeter = LocalEvent({ 'title': 'Audio Meter (dB)', 'order': next_seq(), 'schema': { 'type': 'number' }})

local_event_OutputDevice = LocalEvent({ 'title': 'Output Device', 'group': 'Volume', 'order': next_seq(), 'schema': { 'type': 'string' }})

# -->

# <!-- Hardware info

local_event_CPUName = LocalEvent({ 'group': 'Hardware Info', 'order': next_seq(), 'schema': { 'type': 'string' }})

local_event_Model = LocalEvent({ 'group': 'Hardware Info', 'order': next_seq(), 'schema': { 'type': 'string' }})

local_event_Cores = LocalEvent({ 'group': 'Hardware Info', 'order': next_seq(), 'schema': { 'type': 'string' }})

local_event_LogicalProcessors = LocalEvent({ 'group': 'Hardware Info', 'order': next_seq(), 'schema': { 'type': 'string' }})

local_event_PhysicalMemory = LocalEvent({ 'group': 'Hardware Info', 'order': next_seq(), 'schema': { 'type': 'string' }})

local_event_Architecture = LocalEvent({ 'group': 'Hardware Info', 'order': next_seq(), 'schema': { 'type': 'string' }})

local_event_MACAddress = LocalEvent({ 'title': 'MAC Address (Primary)', 'group': 'Network', 'order': next_seq(), 'schema': { 'type': 'string' }})

local_event_MACAddresses = LocalEvent({ 'title': 'MAC Addresses (All)', 'group': 'Network', 'order': next_seq(), 'schema': { 'type': 'array', 'items': { 'type': 'object', 'properties': { 'interface': { 'type': 'string' }, 'mac': { 'type': 'string' }}}}})

# -->

# <!-- Power Management Settings

local_event_WakeOnLAN = LocalEvent({ 'title': 'Wake on LAN (womp)', 'group': 'Power Settings', 'order': next_seq(), 'desc': 'Allows Wake on LAN (pmset womp).', 'schema': { 'type': 'boolean' }})

local_event_AutoRestartOnPowerLoss = LocalEvent({ 'title': 'Auto-Restart on Power Loss', 'group': 'Power Settings', 'order': next_seq(), 'desc': 'Restarts automatically after power loss (pmset autorestart).', 'schema': { 'type': 'boolean' }})

local_event_WakeForNetworkAccess = LocalEvent({ 'title': 'Wake for Network Access', 'group': 'Power Settings', 'order': next_seq(), 'desc': 'Allows network activity to wake the system (pmset networkoversleep).', 'schema': { 'type': 'boolean' }})

local_event_PowerNap = LocalEvent({ 'title': 'Power Nap', 'group': 'Power Settings', 'order': next_seq(), 'desc': 'If enabled, macOS may wake briefly during sleep to perform background tasks (usually on AC power).', 'schema': { 'type': 'boolean' }})

local_event_TCPKeepAlive = LocalEvent({ 'title': 'TCP Keep-Alive', 'group': 'Power Settings', 'order': next_seq(), 'desc': 'If enabled, maintains network connections during sleep (prevents true sleep, breaks WoL).', 'schema': { 'type': 'boolean' }})

# -->

# <!-- Power Settings actions

@local_action({ 'title': 'Wake on LAN (womp)', 'group': 'Power Settings', 'order': next_seq(), 'desc': 'Enable or disable Wake on LAN (pmset womp). Requires sudoers setup.', 'schema': { 'type': 'boolean' } })
def WakeOnLAN(arg):
    console.info('WakeOnLAN %s action' % arg)
    if arg in [ True, 1, 'On', 'ON', 'on' ]:
        _set_power_setting('womp', True)
    elif arg in [ False, 0, 'Off', 'OFF', 'off' ]:
        _set_power_setting('womp', False)
    else:
        console.warn('WakeOnLAN: invalid arg %s' % arg)

@local_action({ 'title': 'Auto-Restart on Power Loss', 'group': 'Power Settings', 'order': next_seq(), 'desc': 'Enable or disable auto-restart after power loss (pmset autorestart). Requires sudoers setup.', 'schema': { 'type': 'boolean' } })
def AutoRestartOnPowerLoss(arg):
    console.info('AutoRestartOnPowerLoss %s action' % arg)
    if arg in [ True, 1, 'On', 'ON', 'on' ]:
        _set_power_setting('autorestart', True)
    elif arg in [ False, 0, 'Off', 'OFF', 'off' ]:
        _set_power_setting('autorestart', False)
    else:
        console.warn('AutoRestartOnPowerLoss: invalid arg %s' % arg)

@local_action({ 'title': 'Wake for Network Access', 'group': 'Power Settings', 'order': next_seq(), 'desc': 'Enable or disable wake for network access (pmset networkoversleep). Requires sudoers setup.', 'schema': { 'type': 'boolean' } })
def WakeForNetworkAccess(arg):
    console.info('WakeForNetworkAccess %s action' % arg)
    if arg in [ True, 1, 'On', 'ON', 'on' ]:
        _set_power_setting('networkoversleep', True)
    elif arg in [ False, 0, 'Off', 'OFF', 'off' ]:
        _set_power_setting('networkoversleep', False)
    else:
        console.warn('WakeForNetworkAccess: invalid arg %s' % arg)

@local_action({ 'title': 'Power Nap', 'group': 'Power Settings', 'order': next_seq(), 'desc': 'Enable or disable Power Nap (pmset powernap). Requires sudoers setup.', 'schema': { 'type': 'boolean' } })
def PowerNap(arg):
    console.info('PowerNap %s action' % arg)
    if arg in [ True, 1, 'On', 'ON', 'on' ]:
        _set_power_setting('powernap', True)
    elif arg in [ False, 0, 'Off', 'OFF', 'off' ]:
        _set_power_setting('powernap', False)
    else:
        console.warn('PowerNap: invalid arg %s' % arg)

@local_action({ 'title': 'TCP Keep-Alive', 'group': 'Power Settings', 'order': next_seq(), 'desc': 'Enable or disable TCP Keep-Alive (pmset tcpkeepalive). Disabling allows true sleep for WoL. Requires sudoers setup.', 'schema': { 'type': 'boolean' } })
def TCPKeepAlive(arg):
    console.info('TCPKeepAlive %s action' % arg)
    if arg in [ True, 1, 'On', 'ON', 'on' ]:
        _set_power_setting('tcpkeepalive', True)
    elif arg in [ False, 0, 'Off', 'OFF', 'off' ]:
        _set_power_setting('tcpkeepalive', False)
    else:
        console.warn('TCPKeepAlive: invalid arg %s' % arg)

# -->

# <!-- Status

local_event_Status = LocalEvent({ 'group': 'Status', 'order': next_seq(), 'schema': { 'type': 'object', 'properties': {
                                      'level':   {'type': 'integer', 'order': 1 },
                                      'message': {'type': 'string', 'order': 2 }}}})

local_event_PermissionNeeded = LocalEvent({ 'group': 'Status', 'order': next_seq(), 'schema': { 'type': 'string' }})

# -->

# <!-- Disk space check

from java.io import File

def check_status():
    roots = [ File('.') ]

    warnings = list()

    roots.sort(lambda x, y: cmp(x.getAbsolutePath(), y.getAbsolutePath()))

    for root in roots:
        path = root.getAbsolutePath()

        total = root.getTotalSpace()
        free = root.getFreeSpace()
        usable = root.getUsableSpace()

        # Use None check to allow 0 to disable warnings
        threshold = param_FreeSpaceThreshold if param_FreeSpaceThreshold is not None else DEFAULT_FREESPACE_GB
        if threshold > 0 and free < threshold * 1024 * 1024 * 1024L:
            warnings.append('%s has less than %0.1f GB left' % (path, long(free)/1024/1024/1024))

    # Check power settings for WOL compatibility
    wolEnabled = local_event_WakeOnLAN.getArg()
    if wolEnabled == False:
        warnings.append('Wake on LAN is disabled (enable in System Settings > Energy)')

    autoRestart = local_event_AutoRestartOnPowerLoss.getArg()
    if autoRestart == False:
        warnings.append('Auto-restart on power loss is disabled')

    if len(warnings) > 0:
        local_event_Status.emit({'level': 2, 'message': '; '.join(warnings)})

    else:
        local_event_Status.emit({'level': 0, 'message': 'OK'})

Timer(check_status, 150, 10) # check status every 2.5 mins (10s first time)

# -->

# <!-- Controller feedback

def controller_feedback(data):
    log(1, 'feedback> %s' % data)

    if data.startswith('//'):
        # ignore comments
        return

    try:
        message = json_decode(data)

    except:
        console.warn('feedback problem, expected JSON data, got [%s]' % data)
        return

    signalName = message.get('event')
    arg = message.get('arg')

    signal = lookup_local_event(signalName)

    if not signal:
        if signalName and signalName.startswith('Screenshot'):
            # create screenshot signal dynamically
            signal = Event(signalName, { 'order': next_seq(), 'group': 'Screenshots', 'schema': { 'type': 'string', 'format': 'image' }})

        else:
            # unknown event
            log(1, 'ignoring unknown signal %s' % signalName)
            return

    signal.emit(arg)

    # Handle permission warnings specially
    if signalName == 'PermissionNeeded':
        if arg == 'screen-recording':
            console.warn('Screen Recording permission not granted. Enable in System Settings > Privacy & Security > Screen Recording')

# <!-- Polling timers

CPU_POLL_INTERVAL = 10
VOLUME_POLL_INTERVAL = 5
SCREENSHOT_FIRST_DELAY = 10

def _poll_cpu():
    _controller.send('get-cpu')

def _poll_volume():
    _controller.send('get-mute')
    _controller.send('get-volume')

def _poll_screenshots():
    _controller.send('screenshot')

_cpu_poll_timer = Timer(_poll_cpu, CPU_POLL_INTERVAL, stopped=True)
_volume_poll_timer = Timer(_poll_volume, VOLUME_POLL_INTERVAL, stopped=True)
_screenshot_timer = Timer(_poll_screenshots, 60, SCREENSHOT_FIRST_DELAY, stopped=True)

def start_cpu_polling():
    _cpu_poll_timer.setInterval(CPU_POLL_INTERVAL)
    _cpu_poll_timer.start()
    _poll_cpu()

def stop_cpu_polling():
    _cpu_poll_timer.stop()

def start_volume_polling():
    _volume_poll_timer.setInterval(VOLUME_POLL_INTERVAL)
    _volume_poll_timer.start()
    _controller.send('list-devices')
    _poll_volume()

def stop_volume_polling():
    _volume_poll_timer.stop()

def start_screenshots(interval=None):
    if interval is None:
        interval = param_ScreenshotInterval if param_ScreenshotInterval is not None else 60
    if interval <= 0:
        console.warn('Screenshots enabled but interval <= 0; skipping')
        stop_screenshots()
        return
    _screenshot_timer.setInterval(interval)
    _screenshot_timer.setDelay(SCREENSHOT_FIRST_DELAY)
    _screenshot_timer.start()

def stop_screenshots():
    _screenshot_timer.stop()

def set_screenshot_interval(interval):
    if interval is None:
        console.warn('SetScreenshotInterval: arg missing')
        return
    if interval <= 0:
        stop_screenshots()
        return
    _screenshot_timer.setInterval(interval)
    if param_EnableScreenshots:
        start_screenshots(interval)

# -->

def apply_config():
    if param_EnableAudioMeter:
        _controller.send('start-audio-meter')
    else:
        _controller.send('stop-audio-meter')

    if param_EnableCPUPolling:
        start_cpu_polling()
    else:
        stop_cpu_polling()

    if param_EnableVolumePolling:
        start_volume_polling()
    else:
        stop_volume_polling()

    if param_EnableScreenshots:
        start_screenshots()
    else:
        stop_screenshots()

def controller_started():
    console.info('MacController started')
    _controller.send('emit-hardware-info')
    _emit_power_settings()
    _controller.send('emit-mac-addresses')
    apply_config()

# -->

# <!-- Compilation and process management

NODE_ROOT = str(_node.getRoot().getAbsolutePath())
COMPILER_PATH = '/usr/bin/swiftc'
SOURCE_FILE = 'MacController.swift'
BINARY_FILE = 'MacController'

_controller = Process([ '%s/%s' % (NODE_ROOT, BINARY_FILE) ],
                     stdout=controller_feedback,
                     started=controller_started)
_controller.stop()

@after_main
def performCompilation():
    # Check if compiler exists
    if not os.path.exists(COMPILER_PATH):
        console.error('Swift compiler not found at %s' % COMPILER_PATH)
        console.error('Install Xcode Command Line Tools: xcode-select --install')
        local_event_Status.emit({'level': 2, 'message': 'Swift compiler not found'})
        return

    sourcePath = '%s/%s' % (NODE_ROOT, SOURCE_FILE)
    binaryPath = '%s/%s' % (NODE_ROOT, BINARY_FILE)

    # Check if source exists
    if not os.path.exists(sourcePath):
        console.error('Source file not found: %s' % sourcePath)
        return

    # Check if binary exists and is newer than source
    if os.path.exists(binaryPath):
        sourceTime = os.path.getmtime(sourcePath)
        binaryTime = os.path.getmtime(binaryPath)
        if binaryTime > sourceTime:
            console.info('Binary is up to date, skipping compilation')
            _controller.start()
            return

    console.info('Compiling MacController.swift...')

    # Compile with optimization
    quick_process([COMPILER_PATH, '-O', '-o', binaryPath, sourcePath], finished=compileComplete)

def compileComplete(result):
    if result.code != 0:
        console.error('Compilation failed (code %s)' % result.code)
        if result.stderr:
            console.error(result.stderr)
        if result.stdout:
            console.error(result.stdout)
        local_event_Status.emit({'level': 2, 'message': 'Compilation failed'})
        return

    console.info('Compilation successful')
    _controller.start()

# -->

# <!-- Cleanup

@at_cleanup
def cleanup():
    global _prevent_sleep_active
    if _prevent_sleep_active:
        _prevent_sleep.stop()
        _prevent_sleep_active = False
    _cpu_poll_timer.stop()
    _volume_poll_timer.stop()
    _screenshot_timer.stop()

# -->

# <!-- Logging

local_event_LogLevel = LocalEvent({'group': 'Debug', 'order': 10000+next_seq(), 'desc': 'Use this to ramp up the logging (with indentation)',
                                   'schema': {'type': 'integer'}})

def warn(level, msg):
    if local_event_LogLevel.getArg() >= level:
        console.warn(('  ' * level) + msg)

def log(level, msg):
    if local_event_LogLevel.getArg() >= level:
        console.log(('  ' * level) + msg)

# -->

# <!-- Main

def main(arg = None):
    console.info('macOS Computer Controller started')

# -->
