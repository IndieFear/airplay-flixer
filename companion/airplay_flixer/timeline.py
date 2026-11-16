"""Playback clock anchored to AirPlay events."""
import math
import time


def seconds(value):
    if isinstance(value, dict):
        scale = value.get('timescale', 0)
        if not isinstance(scale, (float, int)) or scale <= 0:
            return None
        raw = value.get('value', 0)
        if not isinstance(raw, (int, float)):
            return None
        value = raw / scale
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def cmtime(value):
    return {'value': round(value * 1000), 'timescale': 1000, 'flags': 1, 'epoch': 0}


class Timeline:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.anchor = clock()
        self.position = 0.0
        self.duration = 0.0
        self.rate = 0.0
        self.state = 'idle'
        self.started = False
        self.controller = None

    def current(self):
        value = max(0, self.position + (self.clock() - self.anchor) * self.rate)
        return min(value, self.duration) if self.duration else value

    def update(self, position=None, duration=None, rate=None, state=None):
        current = self.current()
        self.position = current if seconds(position) is None else seconds(position)
        if seconds(duration):
            self.duration = seconds(duration)
        if rate is not None and isinstance(rate, (float, int)) and math.isfinite(rate):
            self.rate = max(0, min(rate, 4))
        if state:
            self.state = state.lower()
            if self.state == 'playing':
                self.started = True
                if rate is None:
                    self.rate = 1
            elif self.state in {'paused', 'stopped', 'idle', 'loading', 'seeking'}:
                self.rate = 0
        self.anchor = self.clock()

    def event(self, event):
        params = event.get('params') or {}
        if not isinstance(params, dict):
            return
        name = event.get('name') or params.get('name')
        if event.get('type') == 'playbackState':
            state = params.get('playbackState') or name
            self.update(position=params.get('position'), duration=params.get('duration'), rate=params.get('rate'), state=state if isinstance(state, str) else None)
        elif event.get('type') == 'notification' and name == 'timeJumped':
            payload = params or event
            position = payload.get('position', payload.get('value', payload.get('data')))
            if isinstance(position, dict) and 'position' in position:
                position = position['position']
            self.update(position=position)

    def snapshot(self):
        return {'position': self.current(), 'duration': self.duration, 'paused': self.state == 'paused', 'playbackState': self.state}
