"use client";

import { useState, useCallback, useMemo, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Check, X, Edit3, Loader2, Mail, Sparkles,
  Clock, AlertCircle, SkipForward, Hexagon, ChevronDown, ChevronRight,
} from "lucide-react";
import { approveJob, fetchThreadContent, type Job, type ThreadContent } from "@/lib/api";

interface LiveDraftPanelProps {
  jobs: Job[];
  runId: string;
  draftTokens: Map<string, string>;
  contentCache?: Record<string, ThreadContent>;
  onJobUpdated?: (jobId: string, updates: Partial<Job>) => void;
}

/* ─── Status helpers ─── */

function statusLabel(job: Job): { text: string; icon: React.ReactNode; color: string; bgColor: string } {
  const step = job.current_step;
  const status = job.status;

  if (status === "completed") return { text: "Done", icon: <Check className="w-3 h-3" />, color: "text-success", bgColor: "bg-success/20" };
  if (status === "failed") return { text: "Failed", icon: <AlertCircle className="w-3 h-3" />, color: "text-error", bgColor: "bg-error/20" };
  if (status === "skipped") return { text: "Skipped", icon: <SkipForward className="w-3 h-3" />, color: "text-muted", bgColor: "bg-border/40" };
  if (status === "pending_approval") return { text: "Review", icon: <Edit3 className="w-3 h-3" />, color: "text-warning", bgColor: "bg-warning/30" };
  if (step === "waiting_for_input") return { text: "Input", icon: <Clock className="w-3 h-3" />, color: "text-warning", bgColor: "bg-warning/20" };
  if (step === "fetch_thread") return { text: "Reading", icon: <Mail className="w-3 h-3" />, color: "text-primary", bgColor: "bg-primary/25" };
  if (step === "classify_intent") return { text: "Classify", icon: <Sparkles className="w-3 h-3" />, color: "text-primary", bgColor: "bg-primary/20" };
  if (step === "generate_draft") return { text: "Drafting", icon: <Edit3 className="w-3 h-3" />, color: "text-primary", bgColor: "bg-primary/25" };
  if (step === "save_draft") return { text: "Saving", icon: <Loader2 className="w-3 h-3 animate-spin" />, color: "text-primary", bgColor: "bg-primary/20" };
  if (step === "apply_label") return { text: "Label", icon: <Loader2 className="w-3 h-3 animate-spin" />, color: "text-primary", bgColor: "bg-primary/20" };
  if (status === "queued") return { text: "Queued", icon: <Clock className="w-3 h-3" />, color: "text-muted", bgColor: "bg-border/40" };
  return { text: "Working", icon: <Loader2 className="w-3 h-3 animate-spin" />, color: "text-primary", bgColor: "bg-primary/20" };
}

/* ─── Hexagon coordinate helpers ─── */

function hexSpiralPositions(count: number): { q: number; r: number }[] {
  if (count === 0) return [];
  const positions: { q: number; r: number }[] = [{ q: 0, r: 0 }];
  if (count === 1) return positions;

  const dirs = [
    { q: 0, r: 1 },
    { q: -1, r: 1 },
    { q: -1, r: 0 },
    { q: 0, r: -1 },
    { q: 1, r: -1 },
    { q: 1, r: 0 },
  ];

  let ring = 1;
  while (positions.length < count) {
    let q = ring;
    let r = -ring;
    for (let side = 0; side < 6; side++) {
      for (let step = 0; step < ring; step++) {
        if (positions.length >= count) break;
        positions.push({ q, r });
        q += dirs[side].q;
        r += dirs[side].r;
      }
      if (positions.length >= count) break;
    }
    ring++;
  }
  return positions.slice(0, count);
}

/** Flat-top hex: axial -> pixel. Gap=0 means touching edges. */
function axialToPixel(q: number, r: number, size: number): { x: number; y: number } {
  const x = size * (3 / 2) * q;
  const y = size * (Math.sqrt(3) / 2 * q + Math.sqrt(3) * r);
  return { x, y };
}


/* ─── Detail panel (below honeycomb) ─── */

function HexDetailPanel({
  job, runId, streamText, contentCache, onJobUpdated, onClose,
}: {
  job: Job; runId: string; streamText: string;
  contentCache?: Record<string, ThreadContent>;
  onJobUpdated?: (jobId: string, updates: Partial<Job>) => void;
  onClose: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [approving, setApproving] = useState(false);
  const [showContext, setShowContext] = useState(false);
  const [contextData, setContextData] = useState<ThreadContent | null>(
    (job.thread_id && contentCache?.[job.thread_id]) || null
  );
  const [contextLoading, setContextLoading] = useState(false);

  const { text: statusText, icon: statusIcon, color: statusColor } = statusLabel(job);
  const displayText = job.draft_text || streamText || "";
  const isDrafting = job.current_step === "generate_draft" && job.status === "running";
  const isPending = job.status === "pending_approval";

  const handleApprove = useCallback(async () => {
    setApproving(true);
    try {
      const text = editing ? editText : displayText;
      await approveJob(runId, job.id, "approve", text);
      onJobUpdated?.(job.id, { status: "running", current_step: "save_draft" });
    } catch { /* ignore */ } finally {
      setApproving(false);
      setEditing(false);
    }
  }, [runId, job.id, editing, editText, displayText, onJobUpdated]);

  const handleDiscard = useCallback(async () => {
    setApproving(true);
    try {
      await approveJob(runId, job.id, "discard");
      onJobUpdated?.(job.id, { status: "skipped", current_step: "done" });
    } catch { /* ignore */ } finally {
      setApproving(false);
    }
  }, [runId, job.id, onJobUpdated]);

  const loadContext = useCallback(async () => {
    if (contextData || !job.thread_id) return;
    if (contentCache?.[job.thread_id]) {
      setContextData(contentCache[job.thread_id]);
      return;
    }
    setContextLoading(true);
    try {
      const data = await fetchThreadContent(job.thread_id);
      setContextData(data);
    } catch { /* ignore */ } finally {
      setContextLoading(false);
    }
  }, [job.thread_id, contextData, contentCache]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 8 }}
      transition={{ duration: 0.15 }}
      className="rounded-xl border border-border bg-surface overflow-hidden hover-glow"
    >
      <div className="px-4 py-2.5 border-b border-border/50 flex items-center justify-between bg-surface-2">
        <div className="flex items-center gap-2 text-sm font-medium truncate flex-1 mr-2">
          <Hexagon className="w-3.5 h-3.5 text-primary flex-shrink-0" />
          <span className="truncate">{job.subject || "(loading...)"}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`flex items-center gap-1 text-[10px] font-medium ${statusColor}`}>
            {statusIcon} {statusText}
          </span>
          <button onClick={onClose} className="text-muted hover:text-foreground transition-colors">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      <div className="px-4 py-3 min-h-[80px] max-h-[250px] overflow-y-auto">
        {editing ? (
          <textarea
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            className="w-full min-h-[100px] text-xs font-mono leading-relaxed bg-surface-2 border border-border rounded-lg p-3 outline-none resize-none text-foreground focus:border-primary transition-colors"
          />
        ) : displayText ? (
          <div className="text-xs leading-relaxed whitespace-pre-wrap text-foreground/90 font-mono">
            {displayText}
            {isDrafting && <span className="inline-block w-[2px] h-3.5 bg-honey ml-0.5 animate-honey-pulse align-text-bottom" />}
          </div>
        ) : job.status === "skipped" ? (
          <div className="text-xs text-muted italic">
            {job.intent === "ignore" ? "Newsletter/notification — skipped" :
             job.intent === "escalate" ? "Needs human review — escalated" : "Discarded"}
          </div>
        ) : job.status === "failed" ? (
          <div className="text-xs text-error/80">{job.error_msg || "Unknown error"}</div>
        ) : (
          <div className="flex items-center gap-2 text-xs text-muted">
            <Loader2 className="w-3 h-3 animate-spin text-primary" />
            {statusText}
          </div>
        )}
      </div>

      {/* Email context */}
      <div className="border-t border-border/30">
        <button
          onClick={() => { if (!showContext && !contextData) loadContext(); setShowContext(!showContext); }}
          className="w-full flex items-center gap-1.5 px-4 py-2 text-xs text-muted hover:text-primary transition-colors"
        >
          <Mail className="w-3.5 h-3.5" />
          <span>Original email</span>
          {showContext ? <ChevronDown className="w-3.5 h-3.5 ml-auto" /> : <ChevronRight className="w-3.5 h-3.5 ml-auto" />}
        </button>
        <AnimatePresence>
          {showContext && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.12 }} className="overflow-hidden">
              <div className="px-4 pb-3">
                {contextLoading ? (
                  <div className="flex items-center gap-1.5 text-xs text-muted py-2"><Loader2 className="w-3 h-3 animate-spin text-primary" /> Loading...</div>
                ) : contextData ? (
                  <div className="max-h-[160px] overflow-y-auto rounded-lg bg-surface-2 border border-border/50 p-3 space-y-2">
                    {contextData.messages.map((msg, i) => (
                      <div key={i} className="text-xs">
                        <div className="font-medium text-foreground/80 mb-0.5">{msg.from} <span className="text-muted font-normal ml-1">{msg.date}</span></div>
                        <div className="text-muted whitespace-pre-wrap leading-relaxed">{msg.body.slice(0, 500)}{msg.body.length > 500 && "..."}</div>
                      </div>
                    ))}
                  </div>
                ) : <div className="text-xs text-muted">Could not load content</div>}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {isPending && (
        <div className="px-4 py-2.5 border-t border-border/50 flex items-center gap-2 bg-surface-2">
          {editing ? (
            <>
              <button onClick={handleApprove} disabled={approving} className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-success/10 text-success hover:bg-success/20 transition-colors border border-success/20"><Check className="w-3 h-3" /> Save</button>
              <button onClick={() => setEditing(false)} className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg text-muted hover:bg-surface transition-colors">Cancel</button>
            </>
          ) : (
            <>
              <button onClick={handleApprove} disabled={approving} className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-success/10 text-success hover:bg-success/20 transition-colors border border-success/20">
                {approving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />} Approve
              </button>
              <button onClick={() => { setEditText(displayText); setEditing(true); }} className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg text-muted hover:text-foreground hover:bg-honey-glow transition-colors"><Edit3 className="w-3 h-3" /> Edit</button>
              <button onClick={handleDiscard} disabled={approving} className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg text-error/60 hover:text-error hover:bg-error/10 transition-colors"><X className="w-3 h-3" /> Discard</button>
            </>
          )}
        </div>
      )}

      {job.status === "completed" && job.summary && (
        <div className="px-4 py-1.5 border-t border-border/50 bg-success/5">
          <div className="text-[10px] text-success flex items-center gap-1"><Check className="w-3 h-3" /> {job.summary}</div>
        </div>
      )}
    </motion.div>
  );
}


/* ─── Individual hex cell ─── */

function HexCell({
  job, size, onClick, isSelected, streamText,
}: {
  job: Job; size: number; onClick: () => void; isSelected: boolean; streamText: string;
}) {
  const { text: statusText, icon: statusIcon, color: statusColor, bgColor } = statusLabel(job);
  const isDrafting = job.current_step === "generate_draft" && job.status === "running";
  const isPending = job.status === "pending_approval";

  const w = size * 2;
  const h = size * Math.sqrt(3);

  // Safe subject — guard against undefined thread_id
  const label = job.subject || (job.thread_id ? job.thread_id.slice(0, 8) : "...");

  return (
    <div
      onClick={onClick}
      className="cursor-pointer group absolute"
      style={{ width: w, height: h }}
    >
      {/* Outer hex (border) */}
      <div
        className={`absolute inset-0 hex-cell transition-colors duration-200 ${
          isSelected ? "bg-primary/40" : isPending ? "bg-warning/30" : bgColor
        } group-hover:bg-primary/30`}
        style={{ width: w, height: h }}
      />
      {/* Inner hex (fill) */}
      <div
        className={`absolute hex-cell bg-surface flex flex-col items-center justify-center overflow-hidden transition-colors duration-200 ${
          isDrafting ? "animate-honey-glow" : ""
        }`}
        style={{ width: w - 3, height: h - 3, left: 1.5, top: 1.5 }}
      >
        <div className="flex flex-col items-center justify-center gap-0.5 px-1.5 text-center">
          <div className={statusColor}>{statusIcon}</div>
          <div className="text-[7px] font-medium leading-tight text-foreground/80 max-w-[92%] line-clamp-2 text-center">
            {label}
          </div>
          <div className={`text-[6px] font-medium ${statusColor}`}>{statusText}</div>
        </div>
      </div>
    </div>
  );
}


/* ─── Main panel ─── */

export default function LiveDraftPanel({ jobs, runId, draftTokens, contentCache, onJobUpdated }: LiveDraftPanelProps) {
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(900);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    setContainerWidth(el.clientWidth);
    const obs = new ResizeObserver((entries) => {
      for (const e of entries) setContainerWidth(e.contentRect.width);
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const sorted = useMemo(() => {
    return [...jobs].sort((a, b) => {
      const order: Record<string, number> = {
        pending_approval: 0, waiting_for_input: 0,
        running: 1, queued: 2,
        completed: 3, skipped: 4, failed: 5,
      };
      return (order[a.status] ?? 3) - (order[b.status] ?? 3);
    });
  }, [jobs]);

  // Scale hex so they all fit. Use container width to pick ideal size.
  const hexSize = useMemo(() => {
    const count = sorted.length;
    if (count <= 1) return 70;
    if (count <= 3) return 64;
    if (count <= 7) return 56;
    if (count <= 19) return 46;
    if (count <= 37) return 38;
    return 32;
  }, [sorted.length]);

  const positions = useMemo(() => hexSpiralPositions(sorted.length), [sorted.length]);
  const pixelPositions = useMemo(() => positions.map((p) => axialToPixel(p.q, p.r, hexSize)), [positions, hexSize]);

  const bounds = useMemo(() => {
    if (pixelPositions.length === 0) return { minX: 0, maxX: 0, minY: 0, maxY: 0 };
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const p of pixelPositions) {
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    }
    return { minX, maxX, minY, maxY };
  }, [pixelPositions]);

  const selectedJob = sorted.find((j) => j.id === selectedJobId) ?? null;

  if (jobs.length === 0) return null;

  const cellW = hexSize * 2;
  const cellH = hexSize * Math.sqrt(3);
  const totalW = bounds.maxX - bounds.minX + cellW + 8;
  const totalH = bounds.maxY - bounds.minY + cellH + 8;
  const offsetX = -bounds.minX + hexSize + 4;
  const offsetY = -bounds.minY + cellH / 2 + 4;

  return (
    <div className="space-y-4" ref={containerRef}>
      {/* Honeycomb */}
      <div className="flex justify-center overflow-x-auto py-1">
        <div className="relative" style={{ width: Math.min(totalW, containerWidth), height: totalH }}>
          <div className="absolute" style={{ left: Math.max(0, (Math.min(totalW, containerWidth) - totalW) / 2), top: 0 }}>
            {sorted.map((job, i) => {
              const px = pixelPositions[i];
              if (!px) return null;
              return (
                <HexCell
                  key={job.id}
                  job={job}
                  size={hexSize}
                  onClick={() => setSelectedJobId((prev) => (prev === job.id ? null : job.id))}
                  isSelected={selectedJobId === job.id}
                  streamText={draftTokens.get(job.id) || ""}
                />
              );
            }).map((el, i) => {
              if (!el) return null;
              const px = pixelPositions[i];
              return (
                <div key={sorted[i].id} className="absolute" style={{ left: px.x + offsetX - hexSize, top: px.y + offsetY - cellH / 2 }}>
                  {el}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Detail panel */}
      <AnimatePresence>
        {selectedJob && (
          <HexDetailPanel
            key={selectedJob.id}
            job={selectedJob}
            runId={runId}
            streamText={draftTokens.get(selectedJob.id) || ""}
            contentCache={contentCache}
            onJobUpdated={onJobUpdated}
            onClose={() => setSelectedJobId(null)}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
