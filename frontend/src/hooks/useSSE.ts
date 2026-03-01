"use client";

import { useEffect, useRef, useCallback, useState } from "react";

export interface SSEMessage {
  event: string;
  data: Record<string, unknown>;
}

type SSEHandler = (msg: SSEMessage) => void;

/**
 * Subscribe to a Server-Sent Events endpoint.
 * Re-connects automatically on disconnect.
 */
export function useSSE(url: string | null, onMessage: SSEHandler) {
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!url) return;

    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let alive = true;

    function connect() {
      if (!alive) return;
      es = new EventSource(url!);

      es.onopen = () => setConnected(true);

      es.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          handlerRef.current({ event: "message", data });
        } catch {
          // ignore malformed
        }
      };

      // Listen for named events
      const eventTypes = [
        "run.started", "run.threads_loaded", "run.jobs_queued",
        "run.completed", "run.failed",
        "job.started", "job.context", "job.classified",
        "job.completed", "job.failed", "job.skipped", "job.retrying",
        "step.started", "step.completed", "step.failed",
        "keepalive",
      ];
      for (const evType of eventTypes) {
        es.addEventListener(evType, (ev: MessageEvent) => {
          try {
            const data = JSON.parse(ev.data);
            handlerRef.current({ event: evType, data });
          } catch {
            // ignore
          }
        });
      }

      es.onerror = () => {
        setConnected(false);
        es?.close();
        // Reconnect after a short delay
        reconnectTimer = setTimeout(connect, 2000);
      };
    }

    connect();

    return () => {
      alive = false;
      clearTimeout(reconnectTimer);
      es?.close();
      setConnected(false);
    };
  }, [url]);

  return { connected };
}
