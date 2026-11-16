import test from "node:test";
import assert from "node:assert/strict";
import {mediaKind, rank, labelFor, inspectPage} from "../extension/detection.js";

test("recognizes signed HLS, subtitles and extensionless MIME types", () => {
  assert.equal(mediaKind("https://cdn.example/master.m3u8?token=abc"), "hls");
  assert.equal(mediaKind("https://cdn.example/api/media", "application/vnd.apple.mpegurl; charset=utf-8"), "hls");
  assert.equal(mediaKind("https://cdn.example/file", "video/x-matroska"), "mkv");
  assert.equal(mediaKind("https://cdn.example/fr.srt"), "subtitle");
  assert.equal(mediaKind("https://cdn.example/captions/fr/index.m3u8"), "subtitle-hls");
  assert.equal(mediaKind("blob:https://example/abc"), null);
});

test("ignores elementary fragments and prioritizes master video over audio", () => {
  assert.equal(mediaKind("https://cdn.example/init-000.mp4"), null);
  assert.equal(mediaKind("https://cdn.example/segment-003.mp4"), null);
  assert.equal(mediaKind("https://cdn.example/video-003.m4s"), null);
  assert.ok(rank({kind: "hls", url: "https://cdn/master.m3u8"}) > rank({kind: "hls", url: "https://cdn/audio/index.m3u8"}));
});

test("labels omit query tokens and remain bounded", () => {
  assert.equal(labelFor("https://cdn/video.mp4?secret=token", 0), "video.mp4");
  assert.ok(labelFor("https://cdn/" + "a".repeat(200), 0).length <= 72);
});

test("extracts WebVTT from array-like browser cue lists", () => {
  const previous = globalThis.document;
  const track = {kind: "subtitles", language: "fr", label: "Français", mode: "showing", cues: {0: {startTime: 1.25, endTime: 3.5, text: "Bonjour"}, length: 1}};
  const video = {currentSrc: "blob:https://page/id", currentTime: 10, duration: 60, paused: false, videoWidth: 320, videoHeight: 180, querySelectorAll: () => [], textTracks: {0: track, length: 1}};
  globalThis.document = {title: "Test", querySelectorAll: () => [video]};
  try {
    const result = inspectPage();
    assert.equal(result.videos[0].tracks[0].selected, true);
    assert.match(result.videos[0].tracks[0].content, /00:00:01\.250 --> 00:00:03\.500\nBonjour/);
  } finally { globalThis.document = previous; }
});
