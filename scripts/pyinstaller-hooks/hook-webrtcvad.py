"""The maintained wheel distribution exports the legacy webrtcvad module."""
from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata('webrtcvad-wheels')
hiddenimports = ['_webrtcvad']
