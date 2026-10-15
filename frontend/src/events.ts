import { useEffect, useRef } from "react";

export interface VillageEvent {
  type: string;
  ts: string;
  run_id?: string;
  agent?: string;
  from_agent?: string;
  to_agent?: string;
  [k: string]: unknown;
}

/** 連到後端 /ws，斷線自動重連。 */
export function useVillageEvents(onEvent: (e: VillageEvent) => void) {
  const cb = useRef(onEvent);
  cb.current = onEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry: number | undefined;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onmessage = (m) => cb.current(JSON.parse(m.data));
      ws.onclose = () => {
        if (!closed) retry = window.setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      ws?.close();
    };
  }, []);
}
