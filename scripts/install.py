#!/usr/bin/env python3
"""Install the already built app in this user's Applications folder."""
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
source = root / 'dist/Airplay Flixer.app'
if not source.exists():
    raise SystemExit('Build first with .venv/bin/python scripts/build.py')
applications = Path.home() / 'Applications'
applications.mkdir(exist_ok=True)
target = applications / source.name
copy_required = True
if target.exists():
    with (target / 'Contents/Info.plist').open('rb') as stream:
        if plistlib.load(stream).get('CFBundleIdentifier') != 'com.airplay-flixer.app':
            raise SystemExit('Another application already uses this name. Installation cancelled.')
    signature = Path('Contents/_CodeSignature/CodeResources')
    if (target / signature).exists() and (target / signature).read_bytes() == (source / signature).read_bytes():
        copy_required = False
    else:
        # Keep an existing installation recoverable.
        target.rename(applications / f'Airplay Flixer.previous-{time.time_ns()}.app')
if copy_required:
    shutil.copytree(source, target)
# Ignore Finder bookkeeping inherited from a File Provider source folder.
for name in ('com.apple.FinderInfo', 'com.apple.ResourceFork'):
    subprocess.run(['/usr/bin/xattr', '-d', name, str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(target)], check=True)
if '--no-launch' not in sys.argv:
    subprocess.run(['/usr/bin/open', '-g', str(target)], check=True)
print(f'Installed {target}')
