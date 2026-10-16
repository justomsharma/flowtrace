import { useEffect, useState } from 'react'

/** The current time, ticking every `ms` (0 = never ticks). */
export function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!ms) return
    const t = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(t)
  }, [ms])
  return now
}

const pad = (n: number) => String(n).padStart(2, '0')

/** "3d 04:12:09 left", or "Time is up" once it has passed. */
export function timeLeft(until: number, now: number): string {
  const s = Math.floor((until - now) / 1000)
  if (s <= 0) return 'Time is up'
  const d = Math.floor(s / 86400)
  const clock = `${pad(Math.floor((s % 86400) / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`
  return `${d > 0 ? `${d}d ` : ''}${clock} left`
}

/** "Fri 23 Oct, 05:29 GMT+5:30": a real-world time (hold ends, decisions) in the viewer's time zone. */
export const untilLabel = (ms: number) =>
  new Date(ms).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })
