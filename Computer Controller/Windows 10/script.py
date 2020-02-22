'''Windows 10 Node'''

### Libraries required by this Node
import subprocess


### Parameters used by this Node
DEFAULT_FREESPACEMB = 0.5
param_FreeSpaceThreshold = Parameter({'title': 'Freespace threshold (GB)', 'schema': {'type': 'integer', 'hint': DEFAULT_FREESPACEMB}})


### Local actions this Node provides
@local_action({'title':'PowerOff','desc':'Turns this computer off.','group':'Power'})
def powerOff():
    console.log('Action PowerOff requested')
    returncode = subprocess.call('shutdown -s -f -t 0 /c "Nodel is shutting down the machine now"', shell=True)

@local_action({'title':'Suspend','desc':'Suspends this computer.','group':'Power'})
def suspend():
    console.log('Action Suspend requested')
    returncode = subprocess.call("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)

@local_action({'title':'Restart','desc':'Restarts this computer.','group':'Power'})
def restart():
    console.log('Action Restart requested')
    returncode = subprocess.call('shutdown -r -f -t 0 /c "Nodel is restarting the machine now"', shell=True)

@local_action({'title':'Mute','group':'Volume','schema':{'type':'string','enum': ['On', 'Off'], 'required': True}})
def mute(arg):
    console.log('Action Mute%s requested' % arg)
    volumeController.send('set-mute %s' % (1 if arg == 'On' else 0))

@local_action({'title':'MuteOn','desc':'Mute this computer.','group':'Volume'})
def muteOn():
    console.log('Action MuteOn requested')
    volumeController.send('set-mute 1')

@local_action({'title':'MuteOff','desc':'Un-mute this computer.','group':'Volume'})
def muteOff():
    console.log('Action MuteOff requested')
    volumeController.send('set-mute 0')

@local_action({'title':'SetVolume','desc':'Set volume.','schema':{'title':'Drag slider to adjust level.','type':'integer','format':'range','min': 0, 'max': 100,'required':'true'},'group':'Volume'})
def setVolume(arg):
    console.log('Action SetVolume requested - '+str(arg))
    volumeController.send('set-volume %s' % arg)


### Local events provided by this Node
local_event_MuteStatus = LocalEvent({'group': 'Volume', 'schema': {'type': 'boolean'}})
local_event_VolumeStatus = LocalEvent({'group': 'Volume', 'schema': {'type': 'number'}})

local_event_Status = LocalEvent({'group': 'Status', 'order': next_seq(), 'schema': {'type': 'object', 'properties': {
        'level': {'type': 'integer', 'order': 1},
        'message': {'type': 'string', 'order': 2}}}})


# <! -- monitor disk storage
from java.io import File

def check_status():
    # unfortunately this pulls in removable disk drives
    # roots = list(File.listRoots())
    
    roots = [File('.')] # so just using current drive instead
    
    warnings = list()
    
    roots.sort(lambda x, y: cmp(x.getAbsolutePath(), y.getAbsolutePath()))
    
    for root in roots:
        path = root.getAbsolutePath()
        
        total = root.getTotalSpace()
        free = root.getFreeSpace()
        usable = root.getUsableSpace()
        
        if free < (param_FreeSpaceThreshold or DEFAULT_FREESPACEMB)*1024*1024*1024L:
            warnings.append('%s has less than %0.1f GB left' % (path, long(free)/1024/1024/1024))
        
    if len(warnings) > 0:
        local_event_Status.emit({'level': 2, 'message': 'Disk space is low on some drives: %s' % (','.join(warnings))})
        
    else:
        local_event_Status.emit({'level': 0, 'message': 'OK'})

Timer(check_status, 150, 10) # check status every 2.5 mins (10s first time)

# -- >

# <! -- manage system volume
def volumeController_feedback(data):
    try:
        activity = json_decode(data) #except java.lang.Exception as err:
    except:
        console.log(data)
        return
    
    signalName = activity.get('event')
    if is_blank(signalName):
        return
    elif signalName == 'MuteStatus':
        local_event_MuteStatus.emit(activity.get('arg'))
    elif signalName == 'VolumeStatus':
        local_event_VolumeStatus.emit(activity.get('arg'))

def volumeController_started():
    volumeController.send('get-mute')
    volumeController.send('get-volume')

volumeController =  Process([r'%s\VolumeController.exe' % _node.getRoot().getAbsolutePath()], stdout=volumeController_feedback, started=volumeController_started)
volumeController.stop()

def compileComplete(arg):
    if arg.code != 0:
        console.error('BAD COMPILATION RESULT (code was %s)' % arg.code)
        console.error(arg.stdout)
        return
    
    # otherwise run the program
    volumeController.start()

# compile to code on first run
quick_process([r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe", 'VolumeController.cs'], finished=compileComplete)

# -- >

      
### Main
def main(arg = None):
    # Start your script here.
    print 'Nodel script started.'
