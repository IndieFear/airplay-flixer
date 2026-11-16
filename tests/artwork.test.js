import test from 'node:test';
import assert from 'node:assert/strict';
import {fetchArtwork} from '../extension/artwork.js';

test('optional artwork skips unavailable canvas support', async () => {
  assert.equal(await fetchArtwork(['https://images.test/poster.jpg']),null);
});

test('artwork rejects non-images and oversized responses without credentials', async () => {
  const prior = {fetch:globalThis.fetch, bitmap:globalThis.createImageBitmap, canvas:globalThis.OffscreenCanvas};
  const calls=[];
  globalThis.createImageBitmap=()=>assert.fail('must not decode rejected content');
  globalThis.OffscreenCanvas=class {};
  globalThis.fetch=async(url,options)=>{calls.push(options);return new Response('bad',{headers:{'content-type':'text/html'}});};
  try {
    assert.equal(await fetchArtwork(['https://images.test/poster.jpg']),null);
    assert.equal(calls[0].credentials,'omit');
    assert.equal(calls[0].referrerPolicy,'no-referrer');
    globalThis.fetch=async()=>new Response('bad',{headers:{'content-type':'image/jpeg','content-length':'2000001'}});
    assert.equal(await fetchArtwork(['https://images.test/huge.jpg']),null);
    assert.equal(await fetchArtwork(['file:///private/image','https://user:pass@images.test/poster.jpg']),null);
  } finally {globalThis.fetch=prior.fetch;globalThis.createImageBitmap=prior.bitmap;globalThis.OffscreenCanvas=prior.canvas;}
});
