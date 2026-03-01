"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Globe,
  ArrowLeft,
  Loader2,
  OctagonX,
  Plus,
  Trash2,
  Play,
  Clock,
  CheckCircle2,
  AlertTriangle,
  LogIn,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import Header from "@/components/Header";
import SwarmGrid from "@/components/SwarmGrid";
import { useSSE, type SSEMessage } from "@/hooks/useSSE";
import {
  type Swarm,
  type SwarmAgent,
  type SwarmTask,
  createSwarm,
  listSwarms,
  listSwarmAgents,
  getSwarm,
  cancelSwarm,
  getSwarmSSEUrl,
  getSwarmAuthStatus,
} from "@/lib/api";

/* ─── Task input row ─── */

interface TaskRow {
  id: string;
  instruction: string;
}

function TaskInput({
  task,
  onChange,
  onRemove,
}: {
  task: TaskRow;
  onChange: (id: string, value: string) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, height: 0 }}
      className="flex items-start gap-2"
    >
      <input
        type="text"
        placeholder="What should this agent do? e.g. &quot;Find the best flights from SFO to JFK on March 1&quot;"
        value={task.instruction}
        onChange={(e) => onChange(task.id, e.target.value)}
        className="flex-1 px-3 py-2 rounded-lg border border-border bg-surface-2 text-sm text-foreground placeholder:text-muted/50 focus:outline-none focus:border-primary/50 transition-colors"
      />
      <button
        onClick={() => onRemove(task.id)}
        className="p-2 rounded-lg text-muted hover:text-error hover:bg-error/10 transition-colors"
      >
        <Trash2 className="w-4 h-4" />
      </button>
    </motion.div>
  );
}

/* ─── Main page ─── */

export default function SwarmPage() {
  // Task builder state
  const [tasks, setTasks] = useState<TaskRow[]>([
    { id: "1", instruction: "" },
  ]);
  const [launching, setLaunching] = useState(false);

  // Swarm state
  const [swarms, setSwarms] = useState<Swarm[]>([]);
  const [activeSwarm, setActiveSwarm] = useState<Swarm | null>(null);
  const [agents, setAgents] = useState<SwarmAgent[]>([]);
  const [cancelling, setCancelling] = useState(false);

  // Agent trajectory log: agentId → list of events
  const [agentLogs, setAgentLogs] = useState<Map<string, { time: string; event: string; detail: string }[]>>(new Map());

  // Auth state
  const [browserAuth, setBrowserAuth] = useState(false);

  // Load past swarms + auth status
  useEffect(() => {
    listSwarms().then(setSwarms).catch(() => {});
    getSwarmAuthStatus().then((s) => setBrowserAuth(s.authenticated)).catch(() => {});
  }, []);

  // Poll active swarm
  useEffect(() => {
    if (!activeSwarm) return;
    const terminal = ["completed", "failed", "cancelled"];
    if (terminal.includes(activeSwarm.status)) return;

    const timer = setInterval(async () => {
      try {
        const [updatedSwarm, updatedAgents] = await Promise.all([
          getSwarm(activeSwarm.id),
          listSwarmAgents(activeSwarm.id),
        ]);
        setActiveSwarm(updatedSwarm);
        setAgents(updatedAgents);
      } catch {
        // ignore
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [activeSwarm]);

  // SSE for live updates
  const sseUrl = activeSwarm ? getSwarmSSEUrl(activeSwarm.id) : null;

  const handleSSE = useCallback((msg: SSEMessage) => {
    const { event, data } = msg;

    if (event.startsWith("swarm.")) {
      setActiveSwarm((prev) => {
        if (!prev) return prev;
        return { ...prev, ...data } as Swarm;
      });
    }

    if (event.startsWith("agent.")) {
      const agentId = data.agent_id as string;
      setAgents((prev) => {
        const idx = prev.findIndex((a) => a.id === agentId);
        if (idx === -1) {
          return [...prev, { ...data, id: agentId } as unknown as SwarmAgent];
        }
        const updated = [...prev];
        updated[idx] = { ...updated[idx], ...data } as SwarmAgent;
        return updated;
      });

      // Collect trajectory log
      const time = new Date().toLocaleTimeString();
      let detail = "";
      if (event === "agent.started") detail = `Session started — ${(data.task_instruction as string) || ""}`;
      else if (event === "agent.action") detail = (data.action as string) || "";
      else if (event === "agent.completed") detail = `Completed — ${data.actions_taken ?? 0} actions`;
      else if (event === "agent.failed") detail = `Failed — ${(data.error as string) || "unknown error"}`;

      if (detail) {
        setAgentLogs((prev) => {
          const next = new Map(prev);
          const existing = next.get(agentId) ?? [];
          next.set(agentId, [...existing, { time, event, detail }]);
          return next;
        });
      }
    }
  }, []);

  useSSE(sseUrl, handleSSE);

  // Task builder handlers
  const addTask = () => {
    setTasks((prev) => [
      ...prev,
      { id: String(Date.now()), instruction: "" },
    ]);
  };

  const removeTask = (id: string) => {
    setTasks((prev) => (prev.length <= 1 ? prev : prev.filter((t) => t.id !== id)));
  };

  const updateTask = (id: string, value: string) => {
    setTasks((prev) =>
      prev.map((t) => (t.id === id ? { ...t, instruction: value } : t))
    );
  };

  const validTasks = useMemo(
    () => tasks.filter((t) => t.instruction.trim()),
    [tasks]
  );

  const handleLaunch = async () => {
    if (validTasks.length === 0 || launching) return;
    setLaunching(true);
    try {
      const swarmTasks: SwarmTask[] = validTasks.map((t) => ({
        instruction: t.instruction.trim(),
      }));
      const swarm = await createSwarm(swarmTasks);
      setActiveSwarm(swarm);
      setAgents([]);
      setSwarms((prev) => [swarm, ...prev]);

      // Fetch agents after short delay
      setTimeout(async () => {
        try {
          const a = await listSwarmAgents(swarm.id);
          setAgents(a);
        } catch {}
      }, 500);
    } catch (e) {
      console.error("Failed to launch swarm:", e);
    } finally {
      setLaunching(false);
    }
  };

  const handleCancel = async () => {
    if (!activeSwarm || cancelling) return;
    setCancelling(true);
    try {
      await cancelSwarm(activeSwarm.id);
      setActiveSwarm((prev) =>
        prev ? { ...prev, status: "cancelled" } : prev
      );
      setAgents((prev) =>
        prev.map((a) =>
          ["queued", "running"].includes(a.status)
            ? { ...a, status: "skipped" }
            : a
        )
      );
    } catch {
    } finally {
      setCancelling(false);
    }
  };

  const handleBack = () => {
    setActiveSwarm(null);
    setAgents([]);
  };

  const isSwarmActive =
    activeSwarm &&
    !["completed", "failed", "cancelled"].includes(activeSwarm.status);

  return (
    <div className="min-h-screen flex flex-col">
      <Header onAuthChange={() => {}} />

      <main className="flex-1 max-w-5xl mx-auto w-full px-6 py-6 space-y-6">
        {activeSwarm ? (
          <>
            {/* Active swarm view */}
            <div className="flex items-center gap-4">
              <button
                onClick={handleBack}
                className="flex items-center gap-1.5 text-sm text-muted hover:text-primary transition-colors"
              >
                <ArrowLeft className="w-4 h-4" />
                Back
              </button>

              <div className="flex-1 flex items-center gap-3">
                <Globe className="w-5 h-5 text-primary" />
                <h2 className="text-sm font-semibold text-golden uppercase tracking-wide">
                  Browser Swarm
                </h2>
                <span
                  className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                    activeSwarm.status === "running"
                      ? "bg-primary/10 text-primary border-primary/20"
                      : activeSwarm.status === "completed"
                      ? "bg-success/10 text-success border-success/20"
                      : activeSwarm.status === "failed"
                      ? "bg-error/10 text-error border-error/20"
                      : "bg-surface-2 text-muted border-border"
                  }`}
                >
                  {activeSwarm.status}
                </span>
                {activeSwarm.total_agents > 0 && (
                  <span className="text-xs text-muted">
                    {activeSwarm.completed_agents}/{activeSwarm.total_agents} done
                  </span>
                )}
              </div>

              {isSwarmActive && (
                <button
                  onClick={handleCancel}
                  disabled={cancelling}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                    bg-error/10 text-error border border-error/25
                    hover:bg-error/20 hover:border-error/40
                    disabled:opacity-50 disabled:cursor-not-allowed
                    transition-all duration-150"
                >
                  {cancelling ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <OctagonX className="w-3.5 h-3.5" />
                  )}
                  {cancelling ? "Stopping..." : "Kill Swarm"}
                </button>
              )}
            </div>

            {/* Agent grid with live views */}
            {agents.length > 0 ? (
              <SwarmGrid agents={agents} agentLogs={agentLogs} />
            ) : (
              <div className="text-center py-16">
                <Loader2 className="w-6 h-6 animate-spin text-primary mx-auto mb-3" />
                <p className="text-sm text-muted">
                  Launching browser agents...
                </p>
              </div>
            )}
          </>
        ) : (
          <>
            {/* Task builder */}
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <Globe className="w-5 h-5 text-primary" />
                <h2 className="text-lg font-bold text-foreground">
                  Browser Swarm
                </h2>
              </div>
              <p className="text-sm text-muted">
                Launch parallel browser agents. Each agent gets its own cloud
                browser and autonomously completes the task. Click any agent
                to watch it work live.
              </p>

              {/* Browser Auth Link */}
              <div className="flex items-center gap-3">
                {browserAuth ? (
                  <>
                    <div className="flex items-center gap-2 text-xs text-success">
                      <ShieldCheck className="w-4 h-4" />
                      <span>Browser login saved — agents will use your session</span>
                    </div>
                    <a
                      href="/swarm/login"
                      className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-muted border border-border hover:border-primary/50 hover:text-primary transition-colors"
                    >
                      <LogIn className="w-3 h-3" />
                      Re-login
                    </a>
                  </>
                ) : (
                  <a
                    href="/swarm/login"
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-border text-muted hover:border-primary/50 hover:text-primary transition-colors"
                  >
                    <LogIn className="w-3.5 h-3.5" />
                    Log in to Google (optional)
                  </a>
                )}
              </div>

              <div className="space-y-2">
                <AnimatePresence>
                  {tasks.map((task) => (
                    <TaskInput
                      key={task.id}
                      task={task}
                      onChange={updateTask}
                      onRemove={removeTask}
                    />
                  ))}
                </AnimatePresence>
              </div>

              <div className="flex items-center gap-3">
                <button
                  onClick={addTask}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium
                    border border-dashed border-border text-muted
                    hover:border-primary/50 hover:text-primary transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  Add task
                </button>
                <div className="flex-1" />
                <button
                  onClick={handleLaunch}
                  disabled={validTasks.length === 0 || launching}
                  className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold
                    bg-primary text-background
                    hover:bg-primary-light
                    disabled:opacity-40 disabled:cursor-not-allowed
                    transition-all duration-200 shadow-lg shadow-primary/20"
                >
                  {launching ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Play className="w-4 h-4" />
                  )}
                  Launch {validTasks.length} Agent
                  {validTasks.length !== 1 ? "s" : ""}
                </button>
              </div>
            </div>

            {/* Past swarms */}
            {swarms.length > 0 && (
              <div className="space-y-3 mt-8">
                <div className="flex items-center gap-2">
                  <Clock className="w-4 h-4 text-muted" />
                  <h3 className="text-sm font-semibold text-muted uppercase tracking-wide">
                    Recent Swarms
                  </h3>
                </div>
                <div className="space-y-1.5">
                  {swarms.slice(0, 10).map((swarm, i) => (
                    <motion.button
                      key={swarm.id}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: i * 0.03 }}
                      onClick={async () => {
                        setActiveSwarm(swarm);
                        try {
                          const a = await listSwarmAgents(swarm.id);
                          setAgents(a);
                        } catch {
                          setAgents([]);
                        }
                      }}
                      className="w-full text-left flex items-center gap-3 px-4 py-2.5 rounded-xl border border-border bg-surface hover:border-primary/30 transition-all duration-200 hover-glow"
                    >
                      <Globe className="w-3.5 h-3.5 text-primary/50 flex-shrink-0" />
                      <span className="text-xs text-muted flex-shrink-0">
                        {new Date(swarm.created_at).toLocaleDateString(
                          undefined,
                          { month: "short", day: "numeric" }
                        )}
                      </span>
                      <span className="text-sm text-foreground/80 flex-1 truncate">
                        {swarm.total_agents} agent
                        {swarm.total_agents !== 1 ? "s" : ""}
                        {swarm.completed_agents > 0 &&
                          ` · ${swarm.completed_agents} completed`}
                        {swarm.failed_agents > 0 &&
                          ` · ${swarm.failed_agents} failed`}
                      </span>
                      <span
                        className={`text-[10px] font-medium px-2 py-0.5 rounded-lg border ${
                          swarm.status === "completed"
                            ? "bg-success/10 text-success border-success/20"
                            : swarm.status === "failed"
                            ? "bg-error/10 text-error border-error/20"
                            : swarm.status === "running"
                            ? "bg-primary/10 text-primary border-primary/20"
                            : "bg-surface-2 text-muted border-border"
                        }`}
                      >
                        {swarm.status}
                      </span>
                    </motion.button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
