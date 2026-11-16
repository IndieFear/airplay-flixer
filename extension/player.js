export function formatTime(value) {
  const seconds = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
  const minutes = Math.floor(seconds / 60), remainder = String(seconds % 60).padStart(2, "0");
  return minutes >= 60 ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${remainder}` : `${minutes}:${remainder}`;
}
export function progress(position, duration) {
  return Number.isFinite(position) && Number.isFinite(duration) && duration > 0 ? Math.max(0, Math.min(100, position / duration * 100)) : 0;
}
