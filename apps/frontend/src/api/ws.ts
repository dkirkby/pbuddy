import { useEffect, useRef } from 'react'
import type { WsEvent } from '../types/api'

export function useProjectWebSocket(
  projectId: string | null,
  onMessage: (msg: WsEvent) => void
) {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  useEffect(() => {
    if (!projectId) return

    let destroyed = false
    let delay = 1000

    function connect() {
      if (destroyed) return
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${protocol}://${window.location.host}/ws/projects/${projectId}`)
      wsRef.current = ws

      ws.onmessage = (e) => {
        try {
          const msg: WsEvent = JSON.parse(e.data)
          onMessageRef.current(msg)
        } catch {}
      }

      ws.onclose = () => {
        if (destroyed) return
        reconnectTimer.current = setTimeout(() => {
          delay = Math.min(delay * 2, 16000)
          connect()
        }, delay)
      }

      ws.onopen = () => {
        delay = 1000
      }
    }

    connect()

    return () => {
      destroyed = true
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [projectId])
}
