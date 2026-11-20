"""Build for this Python interpreter and install only into this source checkout."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='Use only cached Cargo dependencies')
    parser.add_argument('--stage', action='store_true', help='Verify the build alongside a running curator')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    crate = root/'native'/'review_native'
    command = ['cargo', 'build', '--release', '--locked', '--manifest-path', str(crate/'Cargo.toml')]
    if args.offline:
        command.append('--offline')
    env = {**os.environ, 'PYO3_PYTHON': sys.executable, 'CARGO_TARGET_DIR': str(crate/'target')}
    subprocess.run(command, env=env, check=True)
    filename = ('_review_native.dll' if sys.platform == 'win32' else
                'lib_review_native.dylib' if sys.platform == 'darwin' else 'lib_review_native.so')
    source = crate/'target'/'release'/filename
    directory = crate/'target'/'staged' if args.stage else root/'src'/'urban_tree_ml'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/('_review_native' + sysconfig.get_config_var('EXT_SUFFIX'))
    temporary = target.with_name(f'.{target.name}.{uuid4().hex}.tmp')
    try:
        shutil.copyfile(source, temporary)
        # Windows refuses to replace a module loaded by a running server. Fail
        # safely rather than stopping that server or overwriting a mapped DLL.
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    env = {**env, 'PYTHONPATH':str(root/'src'), 'TREE_ML_REVIEW_NATIVE':'required'}
    subprocess.run([sys.executable, '-c',
                    'import sys, urban_tree_ml; urban_tree_ml.__path__.insert(0, sys.argv[1]); '
                    'from urban_tree_ml import _review_native; '
                    'assert _review_native.match_chips(b"", b"", [], 1.0) == []; '
                    'from urban_tree_ml.review_native import backend_info, encode_json; '
                    'assert encode_json({"ok": True}) == b\'{"ok":true}\'; print(backend_info())', str(directory)],
                   env=env, check=True)
    print(f'{"Staged" if args.stage else "Installed"} {target}')


if __name__ == '__main__':
    main()
