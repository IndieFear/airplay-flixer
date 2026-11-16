#!/usr/bin/env python3
"""Build a self-contained Apple Silicon/Intel app for the current Mac."""
from __future__ import annotations

import base64
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
APP = DIST / 'Airplay Flixer.app'


def run(*arguments):
    subprocess.run([str(arg) for arg in arguments], cwd=ROOT, check=True)


def minimum_macos(*binaries):
    versions = [(13, 0)]
    for binary in binaries:
        output = subprocess.check_output(['/usr/bin/vtool', '-show-build', binary], text=True)
        versions.extend(tuple(map(int, value.split('.'))) for value in re.findall(r'^\s*minos\s+(\d+(?:\.\d+)+)', output, re.M))
    return '.'.join(map(str, max(versions)))


def copy_notices(destination):
    excluded = {'auditok', 'faust-cchardet', 'pip', 'pyinstaller-hooks-contrib'}
    for dist in metadata.distributions():
        if dist.name.lower() in excluded:
            continue
        for entry in dist.files or ():
            if '.dist-info' not in entry.parts[0] or not any(
                part.lower().startswith(('license', 'copying', 'notice', 'copyright')) for part in entry.parts[1:]
            ):
                continue
            source = dist.locate_file(entry)
            if source.is_file():
                target = destination / f'{dist.name}-{dist.version}' / Path(*entry.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    shutil.copy2(Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt', destination / 'Python.txt')
    for name, relative in {
        'OpenSSL.txt': 'openssl@3/LICENSE.txt',
        'XZ.txt': 'xz/share/doc/xz/COPYING',
        'XZ-0BSD.txt': 'xz/share/doc/xz/COPYING.0BSD',
        'mpdecimal.txt': 'mpdecimal/share/doc/mpdecimal/COPYRIGHT.txt',
    }.items():
        for prefix in (Path('/opt/homebrew/opt'), Path('/usr/local/opt')):
            source = prefix / relative
            if source.is_file():
                shutil.copy2(source, destination / name)
                break


def main():
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(ROOT / 'build/pyinstaller-cache')
    manifest = json.loads((ROOT / 'extension/manifest.json').read_text())
    if not manifest.get('key'):
        raise SystemExit('La clé publique stable de l’extension est absente.')
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    identifier = ''.join(chr(ord('a') + int(n, 16)) for n in digest)
    DIST.mkdir(exist_ok=True)
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--name', 'FlixerEngine',
        '--paths', ROOT / 'companion', '--additional-hooks-dir', ROOT / 'scripts/pyinstaller-hooks', '--collect-submodules', 'pyatv.protocols', '--copy-metadata', 'pyatv', '--copy-metadata', 'ffsubsync',
        '--exclude-module', 'auditok', '--exclude-module', 'cchardet',
        '--distpath', DIST / 'engine-build', '--workpath', ROOT / 'build/pyinstaller', '--specpath', ROOT / 'build', ROOT / 'companion/entry.py')
    # Documents may be backed by iCloud File Provider, which continuously adds
    # FinderInfo to .app folders. Assemble/sign outside that filesystem first.
    staging = tempfile.TemporaryDirectory(prefix='airplay-flixer-build-')
    app = Path(staging.name) / APP.name
    contents = app / 'Contents'
    resources = contents / 'Resources'
    (contents / 'MacOS').mkdir(parents=True)
    resources.mkdir()
    shutil.copytree(DIST / 'engine-build/FlixerEngine', resources / 'engine')
    bundled_extension = resources / 'extension'
    shutil.copytree(ROOT / 'extension', bundled_extension, ignore=shutil.ignore_patterns('.DS_Store'))
    iconset = Path(staging.name) / 'AirplayFlixer.iconset'
    renderer = ROOT / 'build/render-icons'
    run('/usr/bin/swiftc', '-module-cache-path', ROOT / 'build/swift-cache', ROOT / 'scripts/render_icons.swift', '-o', renderer, '-framework', 'AppKit')
    run(renderer, bundled_extension / 'icons', iconset)
    run('/usr/bin/iconutil', '-c', 'icns', iconset, '-o', resources / 'AirplayFlixer.icns')
    shutil.copy2(ROOT / 'README.md', resources / 'README.md')
    (resources / 'extension-id.txt').write_text(identifier + '\n')
    executable = contents / 'MacOS/AirplayFlixer'
    run('/usr/bin/swiftc', '-O', '-module-cache-path', ROOT / 'build/swift-cache', ROOT / 'native/MenuBar.swift', ROOT / 'native/PlaybackPanel.swift', '-o', executable, '-framework', 'AppKit')
    minimum = minimum_macos(executable, resources / 'engine/_internal/Python')
    info = {
        'CFBundleIdentifier': 'com.airplay-flixer.app', 'CFBundleName': 'Airplay Flixer',
        'CFBundleDisplayName': 'Airplay Flixer', 'CFBundleExecutable': 'AirplayFlixer',
        'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': manifest['version'], 'CFBundleVersion': '1',
        'CFBundleIconFile': 'AirplayFlixer.icns',
        'LSUIElement': True, 'LSMinimumSystemVersion': minimum, 'NSHighResolutionCapable': True,
        'NSLocalNetworkUsageDescription': 'Airplay Flixer recherche ton Apple TV et lui transmet les vidéos que tu choisis de caster.',
        'NSBonjourServices': ['_airplay._tcp', '_mediaremotetv._tcp', '_companion-link._tcp', '_raop._tcp'],
    }
    with (contents / 'Info.plist').open('wb') as stream:
        plistlib.dump(info, stream)
    # Finder/resource-fork attributes inherited from local dependencies prevent
    # signing. These are generated copies, never the installed dependencies.
    run('/usr/bin/xattr', '-cr', app)
    run('/usr/bin/codesign', '--force', '--deep', '--sign', '-', app)
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
    if APP.exists():
        shutil.rmtree(APP)
    shutil.copytree(app, APP)
    # Convenient, separate folder for Chrome's "Load unpacked" picker.
    extension = DIST / 'Chrome Extension'
    if extension.exists():
        shutil.rmtree(extension)
    shutil.copytree(bundled_extension, extension)
    run('/usr/bin/ditto', '-c', '-k', '--keepParent', app, DIST / 'Airplay-Flixer-mac.zip')
    with ZipFile(DIST / 'Airplay-Flixer-Chrome.zip', 'w', ZIP_DEFLATED) as archive:
        for path in sorted(extension.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(extension))

    share = Path(staging.name) / 'Airplay Flixer'
    share.mkdir()
    shutil.move(app, share / app.name)
    shutil.copytree(extension, share / extension.name)
    copy_notices(share / 'Licences tierces')
    architecture = platform.machine()
    chip = 'Apple Silicon' if architecture == 'arm64' else 'Intel'
    guide = (ROOT / 'scripts/share-guide.html').read_text()
    guide = guide.replace('@VERSION@', manifest['version']).replace('@MACOS@', minimum).replace('@CHIP@', chip)
    (share / 'Commencer ici.html').write_text(guide)
    share_zip = DIST / f"Airplay-Flixer-{manifest['version']}-Mac-{architecture}.zip"
    run('/usr/bin/ditto', '-c', '-k', '--norsrc', '--keepParent', share, share_zip)
    print(f'\nApplication : {APP}\nExtension : {extension}\nIdentifiant : {identifier}\nArchive à partager : {share_zip}')
    staging.cleanup()


if __name__ == '__main__':
    main()
