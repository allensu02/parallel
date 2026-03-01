"use client";

import { useState, useMemo, useRef, useCallback, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Globe,
  Hexagon,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Eye,
  X,
  ExternalLink,
  Loader2,
} from "lucide-react";
import type { SwarmAgent } from "@/lib/api";

export type AgentLogEntry = { time: string; event: string; detail: string };

/* ─── Trajectory log panel ─── */

function AgentTrajectory({
  agent,
  logs,
}: {
  agent: SwarmAgent;
  logs: AgentLogEntry[];
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs.length]);

  const eventColor: Record<string, string> = {
    "agent.started": "text-primary",
    "agent.action": "text-foreground",
    "agent.completed": "text-success",
    "agent.failed": "text-error",
  };

  return (
    <div className="w-72 flex-shrink-0 border-l border-border bg-surface-2 flex flex-col">
      {/* Header */}
      <div className="px-3 py-2 border-b border-border">
        <h4 className="text-xs font-semibold text-muted uppercase tracking-wide">
          Agent Trajectory
        </h4>
        <p className="text-[10px] text-muted mt-0.5 truncate">
          {agent.task_instruction}
        </p>
      </div>

      {/* Log entries */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5">
        {logs.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-xs text-muted/50">
              {agent.status === "queued" ? "Waiting to start..." : "Listening for events..."}
            </p>
          </div>
        ) : (
          logs.map((log, i) => (
            <div key={i} className="flex gap-2 text-[11px] leading-relaxed">
              <span className="text-muted/50 flex-shrink-0 font-mono">
                {log.time.split(" ")[0]}
              </span>
              <div className="flex items-start gap-1.5 min-w-0">
                <span
                  className={`w-1.5 h-1.5 rounded-full flex-shrink-0 mt-1.5 ${
                    log.event === "agent.completed"
                      ? "bg-success"
                      : log.event === "agent.failed"
                      ? "bg-error"
                      : log.event === "agent.started"
                      ? "bg-primary"
                      : "bg-primary-light"
                  }`}
                />
                <span className={`${eventColor[log.event] ?? "text-muted"} break-words`}>
                  {log.detail}
                </span>
              </div>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      {/* Footer stats */}
      <div className="px-3 py-2 border-t border-border flex items-center justify-between text-[10px] text-muted">
        <span>{logs.length} events</span>
        <span>{agent.actions_taken} actions</span>
      </div>
    </div>
  );
}

interface SwarmGridProps {
  agents: SwarmAgent[];
  onSelectAgent?: (agent: SwarmAgent) => void;
  agentLogs?: Map<string, AgentLogEntry[]>;
}

const TERMINAL_STATUSES = new Set(["completed", "failed", "skipped"]);

function isAgentActive(agent: SwarmAgent): boolean {
  return !TERMINAL_STATUSES.has(agent.status);
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  running: <Loader2 className="w-4 h-4 text-primary-light animate-spin" />,
  completed: <CheckCircle2 className="w-4 h-4 text-success" />,
  failed: <AlertTriangle className="w-4 h-4 text-error" />,
  queued: <Clock className="w-4 h-4 text-muted" />,
  skipped: <AlertTriangle className="w-4 h-4 text-warning" />,
};

export default function SwarmGrid({ agents, onSelectAgent, agentLogs }: SwarmGridProps) {
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 9;

  // Keep selected agent in sync with latest data from parent
  const selectedAgent = useMemo(
    () => (selectedAgentId ? agents.find((a) => a.id === selectedAgentId) ?? null : null),
    [agents, selectedAgentId]
  );

  const displayAgents = useMemo(() => {
    const sorted = [...agents].sort((a, b) => {
      const order: Record<string, number> = {
        running: 0,
        queued: 1,
        failed: 2,
        completed: 3,
        skipped: 4,
      };
      return (order[a.status] ?? 5) - (order[b.status] ?? 5);
    });
    const start = page * PAGE_SIZE;
    return sorted.slice(start, start + PAGE_SIZE);
  }, [agents, page]);

  const totalPages = Math.ceil(agents.length / PAGE_SIZE);

  // Pad to 9 cells for the grid
  const cells = [...displayAgents];
  while (cells.length < Math.min(9, agents.length || 1)) {
    cells.push(null as unknown as SwarmAgent);
  }

  const handleAgentClick = (agent: SwarmAgent) => {
    setSelectedAgentId(agent.id);
    onSelectAgent?.(agent);
    if (isAgentActive(agent) && agent.live_view_url) {
      window.open(agent.live_view_url, `agent-${agent.id}`);
    }
  };

  const domain = (url: string) => {
    if (!url) return "autonomous";
    try {
      return new URL(url).hostname;
    } catch {
      return url || "autonomous";
    }
  };

  return (
    <div className="space-y-4">
      {/* Live View Modal */}
      <AnimatePresence>
        {selectedAgent && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
            onClick={() => setSelectedAgentId(null)}
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              className="relative w-[90vw] h-[85vh] bg-surface rounded-2xl border border-border overflow-hidden"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Header */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-surface-2">
                <div className="flex items-center gap-3">
                  {STATUS_ICON[selectedAgent.status]}
                  <div>
                    <span className="text-sm font-medium">
                      Agent {selectedAgent.id.slice(0, 8)}
                    </span>
                    <span className="text-xs text-muted ml-2">
                      {domain(selectedAgent.task_url)}
                    </span>
                  </div>
                  {selectedAgent.current_action && (
                    <span className="text-xs text-primary-light bg-primary/10 px-2 py-0.5 rounded-full">
                      {selectedAgent.current_action}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {selectedAgent.live_view_url && isAgentActive(selectedAgent) && (
                    <button
                      onClick={() => window.open(selectedAgent.live_view_url, `agent-${selectedAgent.id}`)}
                      className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] text-muted hover:text-primary hover:bg-primary/10 transition-colors"
                      title="Open in new tab if embedded view disconnects"
                    >
                      <ExternalLink className="w-3 h-3" />
                      Pop out
                    </button>
                  )}
                  <button
                    onClick={() => setSelectedAgentId(null)}
                    className="p-1.5 rounded-lg hover:bg-surface transition-colors text-muted hover:text-foreground"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* Content: browser stream + trajectory */}
              <div className="flex h-[calc(100%-52px)]">
                {/* Left: embedded browser or result */}
                <div className="flex-1 min-w-0 flex flex-col">
                  {isAgentActive(selectedAgent) && selectedAgent.live_view_url ? (
                    <iframe
                      src={selectedAgent.live_view_url}
                      className="w-full flex-1"
                      allow="clipboard-read; clipboard-write"
                    />
                  ) : (
                    <div className="flex-1 flex flex-col items-center justify-center p-8 text-center gap-4">
                      {selectedAgent.status === "completed" ? (
                        <>
                          <CheckCircle2 className="w-10 h-10 text-success" />
                          <h3 className="text-lg font-semibold text-foreground">Task Completed</h3>
                          <p className="text-sm text-muted max-w-md">{selectedAgent.task_instruction}</p>
                          {selectedAgent.result && (
                            <pre className="text-xs text-left bg-surface-2 border border-border rounded-xl p-4 max-w-xl w-full overflow-auto max-h-48">
                              {typeof selectedAgent.result === "string"
                                ? (() => { try { const p = JSON.parse(selectedAgent.result); return p.message || JSON.stringify(p, null, 2); } catch { return selectedAgent.result; } })()
                                : JSON.stringify(selectedAgent.result, null, 2)}
                            </pre>
                          )}
                          <p className="text-xs text-muted">{selectedAgent.actions_taken} actions taken</p>
                        </>
                      ) : selectedAgent.status === "failed" ? (
                        <>
                          <AlertTriangle className="w-10 h-10 text-error" />
                          <h3 className="text-lg font-semibold text-foreground">Task Failed</h3>
                          <p className="text-sm text-muted max-w-md">{selectedAgent.task_instruction}</p>
                          {selectedAgent.error_msg && (
                            <p className="text-sm text-error bg-error/10 rounded-lg px-4 py-2 max-w-lg">{selectedAgent.error_msg}</p>
                          )}
                        </>
                      ) : (
                        <>
                          <Loader2 className="w-8 h-8 animate-spin text-primary" />
                          <p className="text-sm text-muted">Waiting for browser...</p>
                        </>
                      )}
                    </div>
                  )}
                </div>

                {/* Right: trajectory log */}
                <AgentTrajectory
                  agent={selectedAgent}
                  logs={agentLogs?.get(selectedAgent.id) ?? []}
                />
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Agent Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <AnimatePresence mode="popLayout">
          {cells.map((agent, i) => (
            <motion.div
              key={agent?.id ?? `empty-${i}`}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ delay: i * 0.04, duration: 0.25, ease: "easeOut" }}
              className={`rounded-xl border overflow-hidden min-h-[160px] transition-all duration-300 ${
                agent
                  ? "bg-surface border-border hover:border-primary/50 cursor-pointer hover-glow"
                  : "bg-surface-2/50 border-border/50"
              }`}
              onClick={() => agent && handleAgentClick(agent)}
            >
              {agent ? (
                <div className="flex flex-col h-full p-4 gap-3">
                  {/* Top: status + domain */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      {STATUS_ICON[agent.status]}
                      <span className="text-xs font-mono text-muted truncate">
                        {domain(agent.task_url)}
                      </span>
                    </div>
                    {agent.live_view_url && agent.status === "running" && (
                      <span className="flex items-center gap-1 text-[10px] text-primary-light bg-primary/10 px-2 py-0.5 rounded-full">
                        <Eye className="w-3 h-3" />
                        Live
                      </span>
                    )}
                  </div>

                  {/* Instruction */}
                  <p className="text-xs text-foreground line-clamp-2 leading-relaxed">
                    {agent.task_instruction}
                  </p>

                  {/* Current action */}
                  {agent.current_action &&
                    agent.status === "running" && (
                      <div className="flex items-center gap-2 text-[10px] text-muted">
                        <div className="w-1.5 h-1.5 rounded-full bg-primary-light animate-pulse" />
                        <span className="truncate">{agent.current_action}</span>
                      </div>
                    )}

                  {/* Bottom stats */}
                  <div className="mt-auto flex items-center justify-between text-[10px] text-muted">
                    <span>
                      {agent.actions_taken > 0
                        ? `${agent.actions_taken} actions`
                        : agent.status === "queued"
                        ? "Waiting..."
                        : "Starting..."}
                    </span>
                    {agent.error_msg && (
                      <span className="text-error truncate max-w-[60%]">
                        {agent.error_msg}
                      </span>
                    )}
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-center h-full text-muted/20">
                  <Globe className="w-6 h-6" />
                </div>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          {Array.from({ length: Math.min(totalPages, 10) }).map((_, i) => (
            <button
              key={i}
              onClick={() => setPage(i)}
              className={`w-2 h-2 rounded-full transition-all duration-300 ${
                i === page ? "bg-primary w-6" : "bg-border hover:bg-muted"
              }`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
