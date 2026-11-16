"""Identify saved pairings without exposing credentials to the extension."""
PROTOCOLS = ('airplay', 'companion', 'mrp', 'raop', 'dmap')


def identifiers(settings):
    return {value for name in PROTOCOLS if isinstance(value := getattr(getattr(settings.protocols, name), 'identifier', None), str) and value}


def matching_settings(storage, device_ids):
    return [settings for settings in storage.settings if identifiers(settings).intersection(device_ids)]


def clear_credentials(settings):
    for name in PROTOCOLS:
        protocol = getattr(settings.protocols, name)
        protocol.credentials = None
        if hasattr(protocol, 'password'):
            protocol.password = None
