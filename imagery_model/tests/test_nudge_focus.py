import subprocess
from urban_tree_ml.quality import _render_grouped_registration_html


def test_nudge_focus_restores_after_release_and_blur_without_saving():
    html = _render_grouped_registration_html([], {}, [])
    start = html.index('let nudgeFocusCard=')
    end = html.index("document.addEventListener('visibilitychange'", start)
    code = html[start:end]
    script = '''
const handlers={},timers=new Map();let timerId=0;
const setTimeout=fn=>{timers.set(++timerId,fn);return timerId},clearTimeout=id=>timers.delete(id);
const document={addEventListener:(k,f)=>handlers[k]=f};
const window={addEventListener:(k,f)=>handlers[k]=f};
const classes=new Set(),card={classList:{add:k=>classes.add(k),remove:k=>classes.delete(k)}};
''' + code + '''
focusNudge(card,'W');if(!classes.has('nudge-focus'))throw Error('not focused');
focusNudge(card,'d');handlers.keyup({key:'w'});if(timers.size)throw Error('still holding D');
handlers.keyup({key:'d'});if(timers.size!==1)throw Error('no restoration');
focusNudge(card,'a');if(timers.size)throw Error('repeat should cancel restoration');
handlers.keyup({key:'a'});[...timers.values()][0]();
if(classes.size)throw Error('not restored');
focusNudge(card,'s');handlers.blur();if(classes.size||nudgeHeldKeys.size)throw Error('blur stuck');
'''
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
    assert '.card .local-dim,' in html
    assert '.tree-species-label.local-focus { visibility: hidden !important;' in html
    assert 'filter: opacity(25%)' in html
    assert 'setTimeout(clearNudgeFocus,750)' in html
