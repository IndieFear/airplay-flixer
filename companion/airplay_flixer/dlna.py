"""Bounded SSDP discovery of UPnP AV renderers and AVTransport playback."""
import asyncio
import hashlib
import ipaddress
import socket
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urljoin
import re

TARGETS = ('urn:schemas-upnp-org:device:MediaRenderer:1',
           'urn:schemas-upnp-org:service:AVTransport:1')
MAX_DESCRIPTION = 512 * 1024


def local_location(value, sender):
    try:
        address = ipaddress.ip_address(sender)
        parsed = urlsplit(value)
        return (parsed.scheme in {'http', 'https'} and parsed.hostname == str(address)
                and not parsed.username and not parsed.password
                and address.is_private and not address.is_loopback
                and not address.is_unspecified and not address.is_multicast)
    except ValueError:
        return False


def ssdp_location(data, sender):
    if len(data) > 16384:
        return None
    lines = data.decode('utf-8', errors='replace').splitlines()
    if not lines or not lines[0].upper().startswith('HTTP/1.1 200 '):
        return None
    headers = {}
    for line in lines[1:]:
        key, separator, value = line.partition(':')
        if separator:
            headers[key.strip().lower()] = value.strip()
    location = headers.get('location', '')
    return location if local_location(location, sender) else None


def parse_description(data, location, address):
    if len(data) > MAX_DESCRIPTION or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    ns = '{urn:schemas-upnp-org:device-1-0}'
    devices = []
    for device in root.iter(ns + 'device'):
        def value(name):
            return (device.findtext(ns + name) or '').strip()[:160]
        if not value('deviceType').startswith('urn:schemas-upnp-org:device:MediaRenderer:'):
            continue
        services = device.find(ns + 'serviceList')
        if services is None or not any((s.findtext(ns + 'serviceType') or '').startswith('urn:schemas-upnp-org:service:AVTransport:') for s in services):
            continue
        base = root.findtext(ns + 'URLBase') or location
        transport = next(s for s in services if (s.findtext(ns + 'serviceType') or '').startswith('urn:schemas-upnp-org:service:AVTransport:'))
        control = urljoin(base, (transport.findtext(ns + 'controlURL') or '').strip())
        service_type = (transport.findtext(ns + 'serviceType') or '').strip()
        if not transport.findtext(ns + 'controlURL') or not local_location(control, address) or not re.fullmatch(r'urn:schemas-upnp-org:service:AVTransport:\d+', service_type):
            continue
        connection = None
        for service in services:
            kind = (service.findtext(ns + 'serviceType') or '').strip()
            endpoint = urljoin(base, (service.findtext(ns + 'controlURL') or '').strip())
            if re.fullmatch(r'urn:schemas-upnp-org:service:ConnectionManager:\d+', kind) and local_location(endpoint, address):
                connection = {'serviceType': kind, 'controlURL': endpoint}
        udn = value('UDN') or location
        devices.append({'id': 'dlna:' + hashlib.sha256(udn.encode()).hexdigest()[:24],
                        'name': value('friendlyName') or 'Device DLNA',
                        'address': address, 'manufacturer': value('manufacturer'),
                        'model': value('modelName'), 'protocol': 'dlna',
                        'paired': False, 'pairingRequired': False, 'canCast': True,
                        'controlURL': control, 'serviceType': service_type, 'connectionManager': connection})
    return devices


class Discovery(asyncio.DatagramProtocol):
    def __init__(self):
        self.locations = {}
        self.error = None

    def datagram_received(self, data, addr):
        if len(self.locations) >= 64:
            return
        location = ssdp_location(data, addr[0])
        if location:
            self.locations[location] = addr[0]

    def error_received(self, error):
        self.error = error


async def discover(client, timeout=3):
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    transport = None
    protocol = Discovery()
    try:
        sock.setblocking(False)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.bind(('0.0.0.0', 0))
        transport, _ = await loop.create_datagram_endpoint(lambda: protocol, sock=sock)
        for _ in range(2):
            for target in TARGETS:
                packet = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n'
                          'MAN: "ssdp:discover"\r\nMX: 2\r\nST: ' + target + '\r\n\r\n')
                transport.sendto(packet.encode('ascii'), ('239.255.255.250', 1900))
        await asyncio.sleep(timeout)
        if protocol.error and not protocol.locations:
            raise protocol.error
    finally:
        if transport:
            transport.close()
        else:
            sock.close()

    semaphore = asyncio.Semaphore(6)

    async def describe(location, address):
        async with semaphore:
            try:
                # No redirects, cookies, or media credentials on discovery reads.
                async with client.get(location, allow_redirects=False, timeout=3) as response:
                    if response.status != 200:
                        return []
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(16384):
                        body.extend(chunk)
                        if len(body) > MAX_DESCRIPTION:
                            return []
                    return parse_description(bytes(body), location, address)
            except (OSError, TimeoutError, ValueError):
                return []
            except Exception:
                # A broken TV description must not prevent other devices appearing.
                return []
    results = await asyncio.gather(*(describe(url, address) for url, address in protocol.locations.items()))
    return list({device['id']: device for batch in results for device in batch}.values())


def time_value(value):
    try:
        parts = value.split(':')
        if len(parts) != 3:
            return None
        h, m, s = map(float, parts)
        return h * 3600 + m * 60 + s if h >= 0 and 0 <= m < 60 and 0 <= s < 60 else None
    except (ValueError, AttributeError):
        return None


def time_target(value):
    whole = int(value)
    return f'{whole // 3600:02}:{whole // 60 % 60:02}:{whole % 60:02}'


def metadata(url, title, mime, duration=0):
    didl = 'urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/'
    root = ET.Element('DIDL-Lite', {'xmlns': didl, 'xmlns:dc': 'http://purl.org/dc/elements/1.1/', 'xmlns:upnp': 'urn:schemas-upnp-org:metadata-1-0/upnp/'})
    item = ET.SubElement(root, 'item', {'id': '0', 'parentID': '-1', 'restricted': '1'})
    ET.SubElement(item, 'dc:title').text = title
    ET.SubElement(item, 'upnp:class').text = 'object.item.videoItem'
    attrs = {'protocolInfo': f'http-get:*:{mime}:*'}
    if duration > 0:
        attrs['duration'] = time_target(duration)
    ET.SubElement(item, 'res', attrs).text = url
    return ET.tostring(root, encoding='unicode')


class DlnaError(ValueError):
    def __init__(self, action, code=None):
        self.code = code
        if code in {'714', '704'}:
            message = 'The TV rejected this DLNA video format. Try an MP4 source. HLS support depends on the TV.'
        elif code in {'710', '711'}:
            message = 'This TV does not support seeking in this video.'
        elif code == '701':
            message = 'This DLNA command is unavailable in the current player state.'
        else:
            message = f'DLNA command {action} failed' + (f' (code {code})' if code and code.isdigit() else '') + '.'
        super().__init__(message)


class DlnaPlayer:
    def __init__(self, client, device):
        self.client = client
        self.device = device
        self.lock = asyncio.Lock()
        self.seekable = True
        self.pauseable = True

    async def command(self, action, **arguments):
        service = self.device['serviceType']
        control = self.device['controlURL']
        if not local_location(control, self.device['address']) or not re.fullmatch(r'urn:schemas-upnp-org:service:(?:AVTransport|ConnectionManager):\d+', service):
            raise ValueError('Invalid DLNA control URL. Scan again.')
        envelope = ET.Element('s:Envelope', {'xmlns:s': 'http://schemas.xmlsoap.org/soap/envelope/', 's:encodingStyle': 'http://schemas.xmlsoap.org/soap/encoding/'})
        body = ET.SubElement(envelope, 's:Body')
        operation = ET.SubElement(body, f'u:{action}', {'xmlns:u': service})
        for key, value in {**({'InstanceID': 0} if ':AVTransport:' in service else {}), **arguments}.items():
            ET.SubElement(operation, key).text = str(value)
        async with self.lock:
            try:
                async with self.client.post(control, data=ET.tostring(envelope, encoding='utf-8', xml_declaration=True), headers={'Content-Type': 'text/xml; charset="utf-8"', 'SOAPAction': f'"{service}#{action}"'}, allow_redirects=False, timeout=8) as response:
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(16384):
                        data.extend(chunk)
                        if len(data) > MAX_DESCRIPTION:
                            raise DlnaError(action)
                    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                        raise DlnaError(action)
                    try:
                        root = ET.fromstring(data)
                    except ET.ParseError:
                        raise DlnaError(action) from None
                    values = {node.tag.split('}')[-1]: (node.text or '') for node in root.iter()}
                    if response.status != 200 or 'Fault' in values:
                        raise DlnaError(action, values.get('errorCode'))
                    return values
            except DlnaError:
                raise
            except Exception:
                raise ValueError('The DLNA TV is not responding. Check that it is on and connected to the same network.') from None

    async def formats(self):
        connection = self.device.get('connectionManager')
        if not connection:
            return set()
        reader = DlnaPlayer(self.client, {**self.device, **connection})
        try:
            result = await reader.command('GetProtocolInfo')
            return {parts[2].lower() for item in result.get('Sink', '').split(',') if len(parts := item.split(':', 3)) == 4 and parts[0].lower() == 'http-get'}
        except ValueError:
            return set()

    async def load(self, url, title, mime, duration):
        await self.command('SetAVTransportURI', CurrentURI=url, CurrentURIMetaData=metadata(url, title, mime, duration))
        await self.command('Play', Speed=1)

    async def seek(self, target):
        try:
            await self.command('Seek', Unit='REL_TIME', Target=time_target(target))
        except DlnaError as error:
            if error.code in {'401', '710'}:
                self.seekable = False
            raise

    async def toggle(self, paused):
        try:
            await self.command('Play', Speed=1) if paused else await self.command('Pause')
        except DlnaError as error:
            if not paused and error.code == '401':
                self.pauseable = False
            raise
