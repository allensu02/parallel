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
        "job.draft_token", "job.draft_complete", "job.pending_approval",
        "job.question", "job.screenshot",
        "job.frame", "job.visual_step",
        "job.agent_started", "job.agent_action", "job.artifacts",
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
        // Reconnect quickly
        reconnectTimer = setTimeout(connect, 500);
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
