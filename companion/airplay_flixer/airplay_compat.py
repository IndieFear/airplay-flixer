"""Modern URL playback with CMTime positions and playback events."""
import asyncio
import logging
from pyatv.support.metadata import MediaMetadata
from .now_playing import clean_title

from pyatv.protocols.raop.protocols import airplayv2
from .timeline import cmtime


async def play_url(atv, url, position=0, timeline=None, title=None, artwork=None):
    title = clean_title(title)
    original = airplayv2.AirPlayV2

    class PositionedStream(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._title_sent = False
            self._artwork_sent = False
            if timeline:
                timeline.controller = self

        async def _send_url_command(self, command):
            if command.get('type') == 'insertPlayQueueItem':
                command = {**command, 'item': {**command['item'], 'Start-Position': cmtime(position)}}
            response = await super()._send_url_command(command)
            if title and not self._title_sent and command.get('type') == 'setRate' and command.get('rate') == 1 and 200 <= response.code < 300:
                self._title_sent = True
                try:
                    metadata_response = await asyncio.wait_for(self.rtsp.set_metadata(
                        self.context.rtsp_session, self.context.rtpseq, self.context.rtptime,
                        MediaMetadata(title=title)), 2)
                    logging.getLogger(__name__).warning('AirPlay title metadata: HTTP=%s', metadata_response.code)
                except Exception as error:
                    # Metadata rejection must never interrupt an otherwise valid movie.
                    logging.getLogger(__name__).warning('AirPlay title metadata unavailable: %s', type(error).__name__)
            if artwork and not self._artwork_sent and command.get('type') == 'setRate' and command.get('rate') == 1 and 200 <= response.code < 300:
                self._artwork_sent = True
                try:
                    artwork_response = await asyncio.wait_for(self.rtsp.set_artwork(
                        self.context.rtsp_session, self.context.rtpseq, self.context.rtptime, artwork), 2)
                    logging.getLogger(__name__).warning('AirPlay artwork metadata: HTTP=%s', artwork_response.code)
                except Exception as error:
                    logging.getLogger(__name__).warning('AirPlay artwork metadata unavailable: %s', type(error).__name__)
            return response

        def _handle_event(self, event):
            super()._handle_event(event)
            if timeline:
                timeline.event(event)

    # Keep the streaming task alive; timeline seeks use the remote session.
    airplayv2.AirPlayV2 = PositionedStream
    try:
        await atv.stream.play_url(url, position=position)
    finally:
        airplayv2.AirPlayV2 = original
        if timeline:
            timeline.controller = None
