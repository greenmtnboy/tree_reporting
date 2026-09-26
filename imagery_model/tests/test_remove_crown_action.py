from pathlib import Path
import subprocess


def test_remove_crown_is_immediate_and_closes_editor():
    source = Path('src/urban_tree_ml/quality.py').read_text(encoding='utf-8')
    handler = source.split('remove.onclick=()=>', 1)[1].split(';form.append(remove)', 1)[0]
    handler = handler.replace('{{', '{').replace('}}', '}')
    subprocess.run(['node', '-e', '''
const calls=[];
const stored={region_id:'crown-123'};
const removeMaskRegion=id=>calls.push(id);
const dialog={close:()=>calls.push('close')};
const confirm=()=>{throw Error('Unexpected confirmation')};
const click=()=>''' + handler + ''';
click();
if(JSON.stringify(calls)!==JSON.stringify(['crown-123','close']))throw Error('Wrong removal behavior');
'''], check=True, capture_output=True, text=True)
