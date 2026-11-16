// Artwork is optional: bounded download, JPEG conversion, and no credentials.
export async function fetchArtwork(urls) {
  if (typeof createImageBitmap !== 'function' || typeof OffscreenCanvas !== 'function') return null;
  const deadline = Date.now() + 4500;
  for (const value of [...new Set(urls || [])].slice(0, 3)) {
    let bitmap;
    try {
      const url = new URL(value);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || value.length > 16384) continue;
      const remaining = deadline - Date.now();
      if (remaining <= 0) break;
      const response = await fetch(url.href, {credentials:'omit', referrerPolicy:'no-referrer', signal:AbortSignal.timeout(Math.min(1800, remaining))});
      if (!response.ok || !/^image\/(jpeg|png|webp|avif)(?:;|$)/i.test(response.headers.get('content-type') || '') || Number(response.headers.get('content-length')) > 2000000) { await response.body?.cancel(); continue; }
      const reader = response.body.getReader(), chunks = [];
      let size = 0;
      try {
        while (true) {
          const {done, value: chunk} = await reader.read();
          if (done) break;
          size += chunk.byteLength;
          if (size > 2000000) throw new Error('Artwork too large');
          chunks.push(chunk);
        }
      } finally { await reader.cancel().catch(() => {}); }
      bitmap = await createImageBitmap(new Blob(chunks), {resizeWidth:768, resizeQuality:'high'});
      if (!bitmap.width || !bitmap.height || bitmap.height > 4096) continue;
      const scale = Math.min(1, 1024 / bitmap.height);
      const canvas = new OffscreenCanvas(Math.max(1,Math.round(bitmap.width*scale)),Math.max(1,Math.round(bitmap.height*scale)));
      const context = canvas.getContext('2d');
      context.fillStyle = '#101216'; context.fillRect(0,0,canvas.width,canvas.height);
      context.drawImage(bitmap,0,0,canvas.width,canvas.height);
      for (const quality of [.82,.6,.4]) {
        const jpeg = await canvas.convertToBlob({type:'image/jpeg',quality});
        if (jpeg.type !== 'image/jpeg' || jpeg.size > 180000) continue;
        const bytes = new Uint8Array(await jpeg.arrayBuffer());
        let binary = '';
        for (let i=0; i<bytes.length; i+=8192) binary += String.fromCharCode(...bytes.subarray(i,i+8192));
        return btoa(binary);
      }
    } catch { /* Missing or inaccessible artwork must never prevent casting. */ }
    finally { bitmap?.close(); }
  }
  return null;
}
