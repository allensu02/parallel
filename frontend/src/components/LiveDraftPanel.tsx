"use client";

import { useState, useCallback, useMemo, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Check, X, Edit3, Loader2, Mail, Sparkles,
  Clock, AlertCircle, SkipForward, Hexagon, ChevronDown, ChevronRight,
  Globe,
} from "lucide-react";
import { approveJob, fetchThreadContent, setVisibleJobs, type Job, type ThreadContent } from "@/lib/api";

/* ═══════════════════════════════════════════════════════════════════════════
   Pipeline helpers
   ═══════════════════════════════════════════════════════════════════════════ */

const PIPELINE_COLORS: Record<string, string> = {
  gmail: "bg-red-400/20 text-red-300 border-red-400/30",
  slides: "bg-amber-400/20 text-amber-200 border-amber-400/30",
  sheets: "bg-emerald-400/20 text-emerald-300 border-emerald-400/30",
  docs: "bg-sky-400/20 text-sky-300 border-sky-400/30",
  forms: "bg-violet-400/20 text-violet-300 border-violet-400/30",
  drive: "bg-teal-400/20 text-teal-300 border-teal-400/30",
  slack: "bg-pink-400/20 text-pink-300 border-pink-400/30",
  generic: "bg-amber-400/15 text-amber-200 border-amber-400/20",
};

function pipelineBadgeClass(pt: string): string {
  return PIPELINE_COLORS[pt] || PIPELINE_COLORS.generic;
}

/** Get the display label for a job — uses subject for gmail, task_instruction for others. */
function jobLabel(job: Job): string {
  if (job.pipeline_type === "gmail" || !job.pipeline_type) {
    return job.subject || (job.thread_id ? job.thread_id.slice(0, 8) : "...");
  }
  return job.task_instruction || job.subject || "Task";
}

/** Whether this job is a gmail-type job with email context. */
function isGmailJob(job: Job): boolean {
  return (job.pipeline_type || "gmail") === "gmail";
}

/* ═══════════════════════════════════════════════════════════════════════════
   Types & Props
   ═══════════════════════════════════════════════════════════════════════════ */

interface LiveDraftPanelProps {
  jobs: Job[];
  runId?: string;            // Optional — used for visible-jobs reporting. If omitted, uses job.run_id.
  draftTokens: Map<string, string>;
  frameData: Map<string, string>;
  contentCache?: Record<string, ThreadContent>;
  onJobUpdated?: (jobId: string, updates: Partial<Job>) => void;
  fullscreen?: boolean;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Status helpers
   ═══════════════════════════════════════════════════════════════════════════ */

function statusLabel(job: Job): { text: string; icon: React.ReactNode; color: string; bgColor: string } {
  const step = job.current_step;
  const status = job.status;
  if (status === "completed") return { text: "Done", icon: <Check className="w-3 h-3" />, color: "text-success", bgColor: "bg-success/20" };
  if (status === "failed") return { text: "Failed", icon: <AlertCircle className="w-3 h-3" />, color: "text-error", bgColor: "bg-error/20" };
  if (status === "skipped") return { text: "Skipped", icon: <SkipForward className="w-3 h-3" />, color: "text-muted", bgColor: "bg-surface-2" };
  if (status === "pending_approval") return { text: "Review", icon: <Edit3 className="w-3 h-3" />, color: "text-warning", bgColor: "bg-warning/25" };
  if (step === "waiting_for_input") return { text: "Input", icon: <Clock className="w-3 h-3" />, color: "text-warning", bgColor: "bg-warning/20" };
  if (step === "fetch_thread") return { text: "Reading", icon: <Mail className="w-3 h-3" />, color: "text-honey", bgColor: "bg-honey/15" };
  if (step === "classify_intent") return { text: "Classify", icon: <Sparkles className="w-3 h-3" />, color: "text-honey", bgColor: "bg-honey/15" };
  if (step === "generate_draft") return { text: "Drafting", icon: <Edit3 className="w-3 h-3" />, color: "text-honey", bgColor: "bg-honey/15" };
  if (step === "visual_compose") return { text: "Typing", icon: <Edit3 className="w-3 h-3 animate-pulse" />, color: "text-honey", bgColor: "bg-honey/20" };
  if (step === "save_draft") return { text: "Saving", icon: <Loader2 className="w-3 h-3 animate-spin" />, color: "text-honey", bgColor: "bg-honey/15" };
  if (step === "apply_label") return { text: "Label", icon: <Loader2 className="w-3 h-3 animate-spin" />, color: "text-honey", bgColor: "bg-honey/15" };
  if (status === "queued") return { text: "Queued", icon: <Clock className="w-3 h-3" />, color: "text-honey/50", bgColor: "bg-honey/8" };
  return { text: "Working", icon: <Loader2 className="w-3 h-3 animate-spin" />, color: "text-honey", bgColor: "bg-honey/15" };
}

/* ═══════════════════════════════════════════════════════════════════════════
   Axial hex coordinate system
   ═══════════════════════════════════════════════════════════════════════════ */

interface Axial { q: number; r: number }

/** Axial → pixel position for flat-top hex layout. */
function axialToPixel(q: number, r: number, size: number): { x: number; y: number } {
  const x = size * (3 / 2) * q;
  const y = size * (Math.sqrt(3) / 2 * q + Math.sqrt(3) * r);
  return { x, y };
}

/** Generate hex spiral positions for N items. */
function hexSpiralPositions(count: number): Axial[] {
  if (count === 0) return [];
  const positions: Axial[] = [{ q: 0, r: 0 }];
  if (count === 1) return positions;
  const dirs: Axial[] = [
    { q: 0, r: 1 }, { q: -1, r: 1 }, { q: -1, r: 0 },
    { q: 0, r: -1 }, { q: 1, r: -1 }, { q: 1, r: 0 },
  ];
  let ring = 1;
  while (positions.length < count) {
    let q = ring, r = -ring;
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

function hexKey(q: number, r: number): string { return `${q},${r}`; }

/** Axial hex distance. */
function hexDistance(a: Axial, b: Axial): number {
  return (Math.abs(a.q - b.q) + Math.abs(a.q + a.r - b.q - b.r) + Math.abs(a.r - b.r)) / 2;
}

/** Convert pixel position back to nearest axial hex coordinate. */
function pixelToNearestAxial(px: number, py: number, size: number): Axial {
  // Inverse of axialToPixel for flat-top hex
  const q = (2 / 3 * px) / size;
  const r = (-1 / 3 * px + Math.sqrt(3) / 3 * py) / size;
  // Round to nearest hex (cube rounding)
  const s = -q - r;
  let rq = Math.round(q), rr = Math.round(r), rs = Math.round(s);
  const dq = Math.abs(rq - q), dr = Math.abs(rr - r), ds = Math.abs(rs - s);
  if (dq > dr && dq > ds) rq = -rr - rs;
  else if (dr > ds) rr = -rq - rs;
  return { q: rq, r: rr };
}


/* ═══════════════════════════════════════════════════════════════════════════
   Idle floating bees — ambient hexagons drifting around
   ═══════════════════════════════════════════════════════════════════════════ */

const IDLE_BEE_COUNT = 14;

interface IdleBeeData {
  x: number;      // % position
  y: number;      // % position
  size: number;   // px (half-width of the hex)
  opacity: number;
  delay: number;  // animation-delay in seconds
}

function IdleBees() {
  const [bees, setBees] = useState<IdleBeeData[]>([]);

  // Generate bee positions client-side only to avoid hydration mismatch
  useEffect(() => {
    const generated: IdleBeeData[] = [];
    for (let i = 0; i < IDLE_BEE_COUNT; i++) {
      generated.push({
        x: Math.random() * 84 + 8,
        y: Math.random() * 84 + 8,
        size: 36 + Math.random() * 30, // 36-66px half-width → 72-132px full hex
        opacity: 0.35 + Math.random() * 0.3,
        delay: Math.random() * 12,
      });
    }
    setBees(generated);
  }, []);

  if (bees.length === 0) return null;

  return (
    <div className="absolute inset-0 pointer-events-none overflow-hidden z-[1]">
      {bees.map((bee, i) => {
        const w = bee.size * 2;
        const h = bee.size * Math.sqrt(3);
        return (
          <div
            key={i}
            className="idle-bee-wrapper"
            style={{
              position: "absolute",
              left: `${bee.x}%`,
              top: `${bee.y}%`,
              width: w,
              height: h,
              opacity: bee.opacity,
              animationDelay: `${bee.delay}s`,
            }}
          >
            {/* Outer hex border — same as worker bees */}
            <div
              className="absolute inset-0 hex-cell"
              style={{
                width: w,
                height: h,
                backgroundColor: "rgba(212, 148, 10, 0.30)",
              }}
            />
            {/* Inner hex surface — same as worker bees */}
            <div
              className="absolute hex-cell flex items-center justify-center"
              style={{
                width: w - 4,
                height: h - 4,
                left: 2,
                top: 2,
                backgroundColor: "rgba(45, 33, 17, 0.6)",
              }}
            >
              <Hexagon
                className="text-honey/20"
                style={{ width: bee.size * 0.4, height: bee.size * 0.4 }}
                strokeWidth={1.5}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}


/* ═══════════════════════════════════════════════════════════════════════════
   Expanded browser view (modal overlay on hex click)
   ═══════════════════════════════════════════════════════════════════════════ */

function ExpandedBrowserView({
  job, frame, streamText, contentCache, onJobUpdated, onClose,
}: {
  job: Job; frame: string | null; streamText: string;
  contentCache?: Record<string, ThreadContent>;
  onJobUpdated?: (jobId: string, updates: Partial<Job>) => void;
  onClose: () => void;
}) {
  const runId = job.run_id;
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
  const hasFrame = !!frame;
  const hasLiveUrl = !!job.live_view_url;
  const isGmail = isGmailJob(job);
  const label = jobLabel(job);
  const pipelineType = job.pipeline_type || "gmail";

  const handleApprove = useCallback(async () => {
    setApproving(true);
    try {
      const text = editing ? editText : displayText;
      await approveJob(runId, job.id, "approve", text);
      onJobUpdated?.(job.id, { status: "running", current_step: "save_draft" });
    } catch { /* ignore */ } finally { setApproving(false); setEditing(false); }
  }, [runId, job.id, editing, editText, displayText, onJobUpdated]);

  const handleDiscard = useCallback(async () => {
    setApproving(true);
    try {
      await approveJob(runId, job.id, "discard");
      onJobUpdated?.(job.id, { status: "skipped", current_step: "done" });
    } catch { /* ignore */ } finally { setApproving(false); }
  }, [runId, job.id, onJobUpdated]);

  const loadContext = useCallback(async () => {
    if (contextData || !job.thread_id) return;
    if (contentCache?.[job.thread_id]) { setContextData(contentCache[job.thread_id]); return; }
    setContextLoading(true);
    try { const data = await fetchThreadContent(job.thread_id); setContextData(data); }
    catch { /* ignore */ } finally { setContextLoading(false); }
  }, [job.thread_id, contextData, contentCache]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-6"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <motion.div
        initial={{ scale: 0.92, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.92, opacity: 0 }}
        transition={{ duration: 0.18, ease: [0.25, 0.46, 0.45, 0.94] }}
        className="glass-panel overflow-hidden hover-glow max-w-2xl w-full max-h-[85vh] overflow-y-auto shadow-2xl"
      >
        {/* Header */}
        <div className="px-4 py-2.5 border-b border-border/50 flex items-center justify-between bg-surface-2/60 sticky top-0 z-10">
          <div className="flex items-center gap-2 text-sm font-medium truncate flex-1 mr-2">
            <Hexagon className="w-3.5 h-3.5 text-primary flex-shrink-0" />
            <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border ${pipelineBadgeClass(pipelineType)}`}>{pipelineType}</span>
            <span className="truncate">{label || "(loading...)"}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className={`flex items-center gap-1 text-[10px] font-medium ${statusColor}`}>{statusIcon} {statusText}</span>
            <button onClick={onClose} className="text-muted hover:text-foreground transition-colors"><X className="w-4 h-4" /></button>
          </div>
        </div>

        {/* Live browser frame (CDP screencast) */}
        {hasFrame && (
          <div className="relative bg-black">
            <img src={`data:image/jpeg;base64,${frame}`} alt="Live browser view" className="w-full h-auto max-h-[450px] object-contain" />
            <div className="absolute top-2 right-2 bg-black/60 text-white text-[9px] px-1.5 py-0.5 rounded font-mono flex items-center gap-1">
              <div className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />LIVE
            </div>
          </div>
        )}

        {/* Browserbase live view (iframe) */}
        {!hasFrame && hasLiveUrl && (
          <div className="relative bg-black">
            <iframe
              src={job.live_view_url}
              title="Live browser view"
              className="w-full border-0"
              style={{ height: 400 }}
              allow="clipboard-read; clipboard-write"
              sandbox="allow-scripts allow-same-origin allow-popups"
            />
            <div className="absolute top-2 right-2 bg-black/60 text-white text-[9px] px-1.5 py-0.5 rounded font-mono flex items-center gap-1">
              <Globe className="w-3 h-3" />
              <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />REMOTE
            </div>
          </div>
        )}

        {/* Draft text */}
        <div className="px-4 py-3 min-h-[60px] max-h-[300px] overflow-y-auto">
          {editing ? (
            <textarea value={editText} onChange={(e) => setEditText(e.target.value)} className="w-full min-h-[100px] text-xs font-mono leading-relaxed bg-surface-2 border border-border rounded-lg p-3 outline-none resize-none text-foreground focus:border-primary transition-colors" />
          ) : displayText ? (
            <div className="text-xs leading-relaxed whitespace-pre-wrap text-foreground/90 font-mono">
              {displayText}
              {isDrafting && <span className="inline-block w-[2px] h-3.5 bg-honey ml-0.5 animate-honey-pulse align-text-bottom" />}
            </div>
          ) : job.status === "skipped" ? (
            <div className="text-xs text-muted italic">
              {job.intent === "ignore" ? "Newsletter/notification — skipped" : job.intent === "escalate" ? "Needs human review — escalated" : isGmail ? "Discarded" : "Task interrupted — try again"}
            </div>
          ) : job.status === "failed" ? (
            <div className="text-xs text-error/80">{job.error_msg || "Unknown error"}</div>
          ) : (
            <div className="flex items-center gap-2 text-xs text-muted"><Loader2 className="w-3 h-3 animate-spin text-primary" />{statusText}</div>
          )}
        </div>

        {/* Email context (gmail only) */}
        {isGmail && (
        <div className="border-t border-border/30">
          <button onClick={() => { if (!showContext && !contextData) loadContext(); setShowContext(!showContext); }} className="w-full flex items-center gap-1.5 px-4 py-2 text-xs text-muted hover:text-primary transition-colors">
            <Mail className="w-3.5 h-3.5" /><span>Original email</span>
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
                      {contextData.messages.map((msg: { from: string; date: string; body: string }, i: number) => (
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
        )}

        {/* Task description (non-gmail) */}
        {!isGmail && job.task_instruction && (
          <div className="border-t border-border/30 px-4 py-2">
            <div className="text-[10px] text-muted uppercase font-semibold mb-1">Task</div>
            <div className="text-xs text-foreground/80">{job.task_instruction}</div>
          </div>
        )}

        {/* Approval buttons */}
        {isPending && (
          <div className="px-4 py-2.5 border-t border-border/50 flex items-center gap-2 bg-surface-2/60">
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
    </motion.div>
  );
}


/* ═══════════════════════════════════════════════════════════════════════════
   HexSlot — positions a hex cell in the grid.
   Snaps instantly on mount / hexSize changes.
   Animates smoothly (2.5s) only for gap-fill repositioning.
   ═══════════════════════════════════════════════════════════════════════════ */

function HexSlot({ targetX, targetY, children }: {
  targetX: number; targetY: number; children: React.ReactNode;
}) {
  // Track the previous target to distinguish gap-fills from resize/mount
  const prevTarget = useRef<{ x: number; y: number } | null>(null);
  const [isGapFill, setIsGapFill] = useState(false);

  useEffect(() => {
    const prev = prevTarget.current;
    if (prev !== null) {
      // Position changed AFTER initial placement — this is a gap-fill
      const dx = Math.abs(prev.x - targetX);
      const dy = Math.abs(prev.y - targetY);
      // Only treat as gap-fill if the hex grid position actually changed
      // (not just a hexSize/resize recalculation — those change ALL positions)
      if (dx > 5 || dy > 5) {
        setIsGapFill(true);
        const timer = setTimeout(() => setIsGapFill(false), 2600);
        return () => clearTimeout(timer);
      }
    }
    prevTarget.current = { x: targetX, y: targetY };
  }, [targetX, targetY]);

  // Update ref without triggering gap-fill on first render
  if (prevTarget.current === null) {
    prevTarget.current = { x: targetX, y: targetY };
  }

  return (
    <div
      className="absolute"
      style={{
        left: '50%',
        top: '50%',
        transform: `translate(${targetX}px, ${targetY}px)`,
        transition: isGapFill ? 'transform 2.5s cubic-bezier(0.25, 0.1, 0.25, 1.0)' : 'none',
      }}
    >
      {children}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Individual hex cell — with sinusoidal bee-like flight paths
   ═══════════════════════════════════════════════════════════════════════════ */

const DONE_STATUSES = ["completed", "skipped", "failed"];

/**
 * Generate a smooth arcing flight path between two points.
 * Uses a single sine arc (not multiple oscillations) to create a
 * gentle, rounded curve — like a real bee lazily flying from A to B.
 */
function generateArcPath(
  startX: number, startY: number,
  endX: number, endY: number,
  steps: number,
  arcHeight: number
): { x: number[]; y: number[] } {
  const xKeys: number[] = [startX];
  const yKeys: number[] = [startY];
  const dx = endX - startX;
  const dy = endY - startY;
  const angle = Math.atan2(dy, dx);
  // Perpendicular direction for the arc bulge
  const perpX = -Math.sin(angle);
  const perpY = Math.cos(angle);

  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    // Smooth ease-in-out progress along the line
    const easedT = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    const baseX = startX + dx * easedT;
    const baseY = startY + dy * easedT;
    // Single smooth arc: bulge peaks at the midpoint, zero at start and end
    const arc = Math.sin(t * Math.PI) * arcHeight;
    xKeys.push(baseX + arc * perpX);
    yKeys.push(baseY + arc * perpY);
  }
  return { x: xKeys, y: yKeys };
}

function HexCell({
  job, size, onClick, streamText, frame, onFlyAway,
}: {
  job: Job; size: number; onClick: () => void;
  streamText: string; frame: string | null;
  onFlyAway?: (jobId: string) => void;
}) {
  const [flyingAway, setFlyingAway] = useState(false);
  const [hasArrived, setHasArrived] = useState(false);
  // Once departure is committed, it NEVER reverses — prevents ghost re-entries
  const departedRef = useRef(false);

  // Trigger fly-away when job reaches a done status.
  // Uses a 2s debounce: the done status must persist for 2s before we commit.
  // Once committed (departedRef=true), it's permanent regardless of future status changes.
  useEffect(() => {
    if (departedRef.current) return; // already committed — nothing to do
    if (!DONE_STATUSES.includes(job.status)) return; // not done yet

    const timer = setTimeout(() => {
      // Double-check we haven't been cancelled and job is still done
      if (departedRef.current) return;
      departedRef.current = true;
      setFlyingAway(true);
      // Wait for the full slow fly-out before removing from grid
      setTimeout(() => onFlyAway?.(job.id), 5000);
    }, 2000); // 2s debounce: must stay "done" for 2 full seconds

    return () => clearTimeout(timer);
  }, [job.status, job.id, onFlyAway]);

  const { text: statusText, icon: statusIcon, color: statusColor } = statusLabel(job);
  const isDrafting = job.current_step === "generate_draft" && job.status === "running";
  const isPending = job.status === "pending_approval";
  const hasFrame = !!frame;
  const pipelineType = job.pipeline_type || "gmail";

  const displayText = streamText || job.draft_text || "";
  const isTyping = isDrafting && !!streamText;

  const w = size * 2;
  const h = size * Math.sqrt(3);
  const label = jobLabel(job);

  // ALL random values in a single ref — computed once on mount, stable across re-renders
  const rand = useRef({
    flyAngle: (Math.random() - 0.5) * 120,       // wider spread for exit direction
    flyDist: 600 + Math.random() * 400,           // moderate exit distance
    flyRot: (Math.random() - 0.5) * 180,          // gentle rotation on exit
    entryAngle: Math.random() * Math.PI * 2,
    entryDist: 500 + Math.random() * 400,
    entryRot: (Math.random() - 0.5) * 90,
    entryDelay: 0.1 + Math.random() * 0.5,
    exitWobble: 50 + Math.random() * 40,          // per-bee wobble amplitude on exit
  });

  // Stable entry position (computed once from ref)
  const entryX = useRef(Math.cos(rand.current.entryAngle) * rand.current.entryDist);
  const entryY = useRef(Math.sin(rand.current.entryAngle) * rand.current.entryDist);

  // Stable exit: pick a random direction (any angle), drift gently outward
  const exitAngleRad = useRef(rand.current.flyAngle * Math.PI / 180);
  const exitX = useRef(Math.sin(exitAngleRad.current) * rand.current.flyDist);
  const exitY = useRef(-Math.cos(exitAngleRad.current) * rand.current.flyDist);

  // Stable fly-IN waypoints — single smooth arc from random edge to center
  const flyInWaypoints = useRef(
    generateArcPath(entryX.current, entryY.current, 0, 0, 10, 60 + Math.random() * 60)
  );
  const flyInTimes = useRef(
    Array.from({ length: 11 }, (_, i) => i / 10)
  );

  // Stable fly-OUT waypoints — single smooth arc drifting away
  const flyOutWaypoints = useRef(
    generateArcPath(0, 0, exitX.current, exitY.current, 12, rand.current.exitWobble)
  );

  const flyOutTimes = useRef(
    Array.from({ length: 13 }, (_, i) => i / 12)
  );

  // ── STABLE keyframe arrays for Framer Motion (prevent animation restart on re-render) ──
  // These MUST be refs so the same array reference is used across renders.
  // If these were inline arrays, every re-render would create new array references,
  // causing Framer Motion to restart the keyframe animation from the beginning.
  const flyOutScale = useRef([1, 1, 1, 0.98, 0.95, 0.90, 0.82, 0.70, 0.55, 0.38, 0.20, 0.08, 0]);
  const flyOutOpacity = useRef([1, 1, 1, 1, 0.98, 0.94, 0.87, 0.75, 0.58, 0.38, 0.18, 0.06, 0]);
  const flyInScale = useRef([0.12, 0.25, 0.42, 0.58, 0.72, 0.83, 0.91, 0.96, 0.99, 1.0, 1.0]);
  const flyInOpacity = useRef([0, 0.20, 0.42, 0.60, 0.75, 0.86, 0.93, 0.97, 0.99, 1.0, 1.0]);
  const flyInRotate = useRef(
    [rand.current.entryRot, ...Array.from({ length: 10 }, (_, i) => rand.current.entryRot * (1 - (i + 1) / 10))]
  );
  const exitScale = useRef([1, 1, 0.97, 0.92, 0.84, 0.73, 0.60, 0.45, 0.30, 0.17, 0.07, 0.02, 0]);
  const exitOpacity = useRef([1, 1, 0.97, 0.92, 0.84, 0.73, 0.60, 0.45, 0.30, 0.17, 0.07, 0.02, 0]);

  // ── Stable animate objects — same reference across renders ──
  const flyOutAnimate = useRef({
    x: flyOutWaypoints.current.x,
    y: flyOutWaypoints.current.y,
    scale: flyOutScale.current,
    opacity: flyOutOpacity.current,
    rotate: rand.current.flyRot,
  });
  const restingAnimate = useRef({ x: 0, y: 0, scale: 1, opacity: 1, rotate: 0 });
  const flyInAnimate = useRef({
    x: flyInWaypoints.current.x,
    y: flyInWaypoints.current.y,
    scale: flyInScale.current,
    opacity: flyInOpacity.current,
    rotate: flyInRotate.current,
  });
  const exitAnimate = useRef({
    x: flyOutWaypoints.current.x,
    y: flyOutWaypoints.current.y,
    scale: exitScale.current,
    opacity: exitOpacity.current,
    rotate: rand.current.flyRot,
  });

  // ── Stable transition objects ──
  const flyOutTransition = useRef({
    duration: 4.5,
    times: flyOutTimes.current,
    ease: [0.4, 0.0, 0.2, 1.0] as const,
  });
  const restingTransition = useRef({
    type: "tween" as const,
    duration: 0.4,
    ease: [0.25, 0.1, 0.25, 1.0] as const,
  });
  const flyInTransition = useRef({
    duration: 2.5,
    times: flyInTimes.current,
    ease: [0.4, 0.0, 0.2, 1.0] as const,
    delay: rand.current.entryDelay,
  });

  // Mark as arrived once the entry transition completes
  const handleAnimComplete = useCallback(() => {
    if (!flyingAway && !hasArrived) setHasArrived(true);
  }, [flyingAway, hasArrived]);

  return (
    <motion.div
      layout={false}
      initial={{
        x: entryX.current,
        y: entryY.current,
        scale: 0.15,
        opacity: 0,
        rotate: rand.current.entryRot,
      }}
      animate={flyingAway ? flyOutAnimate.current
        : hasArrived ? restingAnimate.current
        : flyInAnimate.current}
      exit={exitAnimate.current}
      transition={flyingAway ? flyOutTransition.current
        : hasArrived ? restingTransition.current
        : flyInTransition.current}
      onAnimationComplete={handleAnimComplete}
      onClick={onClick}
      className={`cursor-pointer group ${flyingAway ? "bee-fly-away" : hasArrived ? "" : "bee-fly-in"}`}
      style={{
        position: "relative",
        width: w,
        height: h,
      }}
    >
      {/* Outer hex (border) — amber honey tones */}
      <div
        className={`absolute inset-0 hex-cell transition-all duration-300 group-hover:brightness-130 ${
          job.status === "running" ? "animate-honey-glow" : ""
        }`}
        style={{
          width: w,
          height: h,
          backgroundColor: job.status === "running"
            ? "rgba(232, 163, 23, 0.55)"
            : job.status === "completed"
            ? "rgba(160, 217, 17, 0.45)"
            : job.status === "failed"
            ? "rgba(255, 82, 82, 0.35)"
            : "rgba(212, 148, 10, 0.35)",
        }}
      />
      {/* Inner hex (content) */}
      <div
        className={`absolute hex-cell overflow-hidden ${
          hasFrame ? "bg-black" : "bg-surface"
        } ${job.status === "running" ? "animate-waggle" : ""}`}
        style={{
          width: w - 4,
          height: h - 4,
          left: 2,
          top: 2,
        }}
      >
        {hasFrame ? (
          <div className="w-full h-full relative">
            <img
              src={`data:image/jpeg;base64,${frame}`}
              alt=""
              className="w-full h-full object-cover"
              style={{ imageRendering: "auto" }}
            />
            <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent px-2 pb-1.5 pt-4">
              <div className="text-[9px] font-medium text-white/90 truncate mb-0.5 drop-shadow">{label}</div>
              <div className="flex items-center gap-1">
                <div className={`${statusColor} drop-shadow`}>{statusIcon}</div>
                <span className="text-[8px] font-semibold text-white/80 drop-shadow">{statusText}</span>
              </div>
            </div>
            <div className="absolute top-2 right-2 flex items-center gap-1 bg-black/60 backdrop-blur-sm rounded-full px-2 py-0.5 border border-honey/20">
              <div className="w-1.5 h-1.5 rounded-full bg-honey animate-pulse" />
              <span className="text-[8px] text-honey font-mono font-bold">LIVE</span>
            </div>
          </div>
        ) : displayText ? (
          <div className="flex flex-col w-full h-full">
            <div className="flex items-center gap-1.5 px-2.5 py-1 bg-surface-2/60 border-b border-primary/10 shrink-0">
              <div className={`${statusColor}`}>{statusIcon}</div>
              <span className="text-[8px] font-medium text-foreground/70 truncate flex-1">{label}</span>
              {isTyping && (
                <div className="flex items-center gap-1">
                  <div className="w-1.5 h-1.5 rounded-full bg-honey animate-pulse" />
                  <span className="text-[7px] text-honey font-mono font-bold">typing</span>
                </div>
              )}
              {isPending && <span className="text-[7px] text-warning font-bold">REVIEW</span>}
            </div>
            <div className="flex-1 px-2.5 py-1.5 overflow-hidden">
              <div className="text-[8px] leading-[1.4] text-foreground/80 font-mono whitespace-pre-wrap">
                {displayText}
                {isTyping && <span className="inline-block w-[4px] h-[10px] bg-primary/80 ml-[1px] animate-pulse" />}
              </div>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center gap-1 px-3 text-center w-full h-full">
            {pipelineType !== "gmail" && (
              <span className={`text-[7px] font-bold uppercase px-1.5 py-0.5 rounded border mb-0.5 ${pipelineBadgeClass(pipelineType)}`}>{pipelineType}</span>
            )}
            <div className={`${statusColor} scale-125`}>{statusIcon}</div>
            <div className="text-[10px] font-medium leading-tight text-foreground/80 max-w-[85%] line-clamp-2 text-center">
              {label}
            </div>
            <div className={`text-[9px] font-semibold ${statusColor}`}>{statusText}</div>
            {isPending && (
              <div className="mt-0.5 text-[8px] text-warning font-medium">Click to review</div>
            )}
          </div>
        )}
      </div>
    </motion.div>
  );
}


/* ═══════════════════════════════════════════════════════════════════════════
   Minimap
   ═══════════════════════════════════════════════════════════════════════════ */

function HiveMinimap({
  positions, centerAxial, onJump,
}: {
  positions: { q: number; r: number; jobId: string; status: string }[];
  centerAxial: Axial;
  onJump: (q: number, r: number) => void;
}) {
  const mmSize = 4; // px per hex in minimap
  const padding = 16;

  // Compute bounds
  const allPx = positions.map((p) => axialToPixel(p.q, p.r, mmSize));
  const minX = Math.min(...allPx.map((p) => p.x)) - padding;
  const maxX = Math.max(...allPx.map((p) => p.x)) + padding;
  const minY = Math.min(...allPx.map((p) => p.y)) - padding;
  const maxY = Math.max(...allPx.map((p) => p.y)) + padding;
  const w = maxX - minX;
  const h = maxY - minY;

  const clampedW = Math.min(w, 120);
  const clampedH = Math.min(h, 80);

  return (
    <div
      className="absolute bottom-3 right-3 glass-panel overflow-hidden z-30 shadow-lg shadow-honey/10"
      style={{ width: clampedW + 8, height: clampedH + 8 }}
    >
      <svg
        viewBox={`${minX} ${minY} ${w} ${h}`}
        width={clampedW}
        height={clampedH}
        className="m-1"
        style={{ display: "block" }}
      >
        {positions.map((pos) => {
          const px = axialToPixel(pos.q, pos.r, mmSize);
          const isCurrent = pos.q === centerAxial.q && pos.r === centerAxial.r;
          const dist = hexDistance(pos, centerAxial);
          const isVisible = dist <= 1;

          let fill = "#6b5530";
          if (pos.status === "completed") fill = "#a0d911";
          else if (pos.status === "failed") fill = "#ff5252";
          else if (pos.status === "running") fill = "#e8a317";
          else if (pos.status === "pending_approval") fill = "#e8a317";

          return (
            <circle
              key={`${pos.q},${pos.r}`}
              cx={px.x}
              cy={px.y}
              r={isCurrent ? 3.5 : isVisible ? 2.5 : 2}
              fill={fill}
              opacity={isVisible ? 1 : 0.5}
              stroke={isCurrent ? "#e8a317" : "none"}
              strokeWidth={isCurrent ? 1.5 : 0}
              className="cursor-pointer"
              onClick={() => onJump(pos.q, pos.r)}
            />
          );
        })}
      </svg>
    </div>
  );
}


/* ═══════════════════════════════════════════════════════════════════════════
   Main panel — Full-screen hive background with smooth scrolling
   ═══════════════════════════════════════════════════════════════════════════ */

export default function LiveDraftPanel({
  jobs, runId, draftTokens, frameData, contentCache, onJobUpdated, fullscreen,
}: LiveDraftPanelProps) {
  // ── State ──
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const gridElRef = useRef<HTMLDivElement>(null);
  const [containerSize, setContainerSize] = useState({ w: 900, h: 600 });
  const visibleReportedRef = useRef<string>("");

  // Camera position in world-space pixels — continuously updated via rAF
  // Start offset upward so the hex center is in the lower visible area (below floating UI)
  const cameraRef = useRef({ x: 0, y: fullscreen ? -120 : 0 });
  const cameraVelRef = useRef({ vx: 0, vy: 0 });
  // For spring-based smooth-scroll-to-target
  const cameraTargetRef = useRef<{ x: number; y: number } | null>(null);
  const rafRef = useRef<number>(0);
  const animatingRef = useRef(false);

  // The axial hex nearest to camera center (for header display + minimap)
  const [focusAxial, setFocusAxial] = useState<Axial>({ q: 0, r: 0 });

  // ── Measure viewport ──
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const measure = () => setContainerSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const obs = new ResizeObserver(() => measure());
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // Track which jobs have flown away (so they remain hidden until filter changes)
  const [flownAway, setFlownAway] = useState<Set<string>>(new Set());

  // ── Stable position map: jobId → Axial (persists across re-renders) ──
  const stablePositionMap = useRef<Map<string, Axial>>(new Map());
  // Version counter — incremented when positions change (gap-fills) to force recompute
  const [positionVersion, setPositionVersion] = useState(0);

  const handleFlyAway = useCallback((jobId: string) => {
    // Record the vacant position before removing from stable map
    const pos = stablePositionMap.current.get(jobId);
    if (pos) {
      stablePositionMap.current.delete(jobId);
    }

    setFlownAway(prev => {
      const next = new Set(prev);
      next.add(jobId);
      return next;
    });

    // After a delay (let the fly-out play out a bit), find the outermost bee to fill the gap
    if (pos) {
      setTimeout(() => {
        // Find the outermost occupied bee (furthest from center)
        let outermostId: string | null = null;
        let outermostDist = -1;
        for (const [id, axial] of stablePositionMap.current) {
          const dist = hexDistance(axial, { q: 0, r: 0 });
          if (dist > outermostDist) {
            outermostDist = dist;
            outermostId = id;
          }
        }

        // Only fill if the vacant position is more central than the outermost bee
        const vacantDist = hexDistance(pos, { q: 0, r: 0 });
        if (outermostId && outermostDist > vacantDist) {
          // Move outermost bee to the vacant position
          stablePositionMap.current.set(outermostId, pos);
          // Bump version so jobGrid recomputes with the new position
          setPositionVersion(v => v + 1);
        }
      }, 800);
    }
  }, []);

  // Clean up stale entries when jobs disappear from the list (e.g. filter change).
  // When a job is removed from the list, clean up its position and flownAway entry.
  // When a job that was previously flown-away comes back (was removed then re-added),
  // it will naturally get a fresh position since its map entry was cleaned up.
  const currentJobIds = useMemo(() => new Set(jobs.map(j => j.id)), [jobs]);
  const prevJobIdsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const prev = prevJobIdsRef.current;

    // Clean up positions for jobs that left the list
    for (const id of stablePositionMap.current.keys()) {
      if (!currentJobIds.has(id)) {
        stablePositionMap.current.delete(id);
      }
    }

    // Clean up flownAway for jobs that left the list
    setFlownAway(fa => {
      let changed = false;
      const next = new Set(fa);
      for (const id of fa) {
        if (!currentJobIds.has(id)) {
          next.delete(id);
          changed = true;
        }
      }
      return changed ? next : fa;
    });

    // Jobs that were absent last render but are now present: ensure they're not in flownAway
    // (handles filter toggle bringing back previously-removed jobs)
    if (prev.size > 0) {
      const reappeared: string[] = [];
      for (const id of currentJobIds) {
        if (!prev.has(id)) reappeared.push(id);
      }
      if (reappeared.length > 0) {
        setFlownAway(fa => {
          let changed = false;
          const next = new Set(fa);
          for (const id of reappeared) {
            if (next.has(id)) {
              next.delete(id);
              stablePositionMap.current.delete(id);
              changed = true;
            }
          }
          return changed ? next : fa;
        });
      }
    }

    prevJobIdsRef.current = currentJobIds;
  }, [currentJobIds]);

  // ── Sort jobs by priority (for initial assignment order only) ──
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

  // ── Assign stable positions: each job keeps its position forever ──
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const jobGrid = useMemo(() => {
    const map = stablePositionMap.current;
    const usedKeys = new Set<string>();
    // Collect all currently used keys
    for (const [, axial] of map) {
      usedKeys.add(hexKey(axial.q, axial.r));
    }

    // Generate enough spiral positions
    const spiral = hexSpiralPositions(Math.max(sorted.length + 10, map.size + 20));

    // Assign new jobs to the first available spiral position
    for (const job of sorted) {
      if (!map.has(job.id)) {
        for (const pos of spiral) {
          const key = hexKey(pos.q, pos.r);
          if (!usedKeys.has(key)) {
            map.set(job.id, { q: pos.q, r: pos.r });
            usedKeys.add(key);
            break;
          }
        }
      }
    }

    // Build the grid from stable positions
    return sorted.map((job) => {
      const pos = map.get(job.id) || { q: 0, r: 0 };
      return { job, q: pos.q, r: pos.r };
    });
  }, [sorted, positionVersion]);

  const gridMap = useMemo(() => {
    const m = new Map<string, (typeof jobGrid)[number]>();
    for (const entry of jobGrid) m.set(hexKey(entry.q, entry.r), entry);
    return m;
  }, [jobGrid]);

  // ── Hex sizing ──
  const hexSize = useMemo(() => {
    const padW = 60, padH = 60;
    const availW = containerSize.w - padW;
    const availH = containerSize.h - padH;
    const sizeFromW = availW / 5.5;
    const sizeFromH = availH / (3 * Math.sqrt(3));
    return Math.max(50, Math.min(150, Math.floor(Math.min(sizeFromW, sizeFromH))));
  }, [containerSize]);

  const cellH = hexSize * Math.sqrt(3);

  // ── Apply camera transform (called every rAF frame) ──
  const applyCamera = useCallback(() => {
    if (gridElRef.current) {
      gridElRef.current.style.transform = `translate(${-cameraRef.current.x}px, ${-cameraRef.current.y}px)`;
    }
  }, []);

  // ── Update focus hex for header + minimap ──
  const updateFocusHex = useCallback(() => {
    if (hexSize === 0) return;
    const nearest = pixelToNearestAxial(cameraRef.current.x, cameraRef.current.y, hexSize);
    setFocusAxial((prev) => {
      if (prev.q === nearest.q && prev.r === nearest.r) return prev;
      return nearest;
    });
  }, [hexSize]);

  // ── Compute world-space bounds for camera clamping ──
  const worldBounds = useMemo(() => {
    if (jobGrid.length === 0) return { minX: 0, maxX: 0, minY: 0, maxY: 0 };
    const positions = jobGrid.map(e => axialToPixel(e.q, e.r, hexSize));
    const padding = hexSize * 1.5;
    return {
      minX: Math.min(...positions.map(p => p.x)) - padding,
      maxX: Math.max(...positions.map(p => p.x)) + padding,
      minY: Math.min(...positions.map(p => p.y)) - padding,
      maxY: Math.max(...positions.map(p => p.y)) + padding,
    };
  }, [jobGrid, hexSize]);

  /** Clamp camera to world bounds. */
  const clampCamera = useCallback((cam: { x: number; y: number }) => {
    cam.x = Math.max(worldBounds.minX, Math.min(worldBounds.maxX, cam.x));
    cam.y = Math.max(worldBounds.minY, Math.min(worldBounds.maxY, cam.y));
  }, [worldBounds]);

  /** Find the nearest occupied hex center to a pixel position. */
  const snapToNearestHex = useCallback((px: number, py: number): { x: number; y: number } => {
    if (jobGrid.length === 0) return { x: 0, y: 0 };
      let bestDist = Infinity;
    let bestPos = { x: 0, y: 0 };
      for (const entry of jobGrid) {
      const p = axialToPixel(entry.q, entry.r, hexSize);
      const d = (p.x - px) ** 2 + (p.y - py) ** 2;
      if (d < bestDist) {
        bestDist = d;
        bestPos = p;
      }
    }
    return bestPos;
  }, [jobGrid, hexSize]);

  // ── Core animation loop — handles both momentum AND spring-to-target ──
  const FRICTION = 0.90;
  const MIN_VEL = 0.3;
  const SPRING_STIFFNESS = 0.12;
  const SPRING_DAMPING = 0.78;

  const tick = useCallback(() => {
    const cam = cameraRef.current;
    const vel = cameraVelRef.current;
    const target = cameraTargetRef.current;

    if (target) {
      // ── Spring mode: smoothly approach target ──
      const dx = target.x - cam.x;
      const dy = target.y - cam.y;

      vel.vx = (vel.vx + dx * SPRING_STIFFNESS) * SPRING_DAMPING;
      vel.vy = (vel.vy + dy * SPRING_STIFFNESS) * SPRING_DAMPING;

      cam.x += vel.vx;
      cam.y += vel.vy;
      clampCamera(cam);

      // Close enough? Snap and stop.
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(vel.vx) < 0.2 && Math.abs(vel.vy) < 0.2) {
        cam.x = target.x;
        cam.y = target.y;
        clampCamera(cam);
        vel.vx = 0;
        vel.vy = 0;
        cameraTargetRef.current = null;
        applyCamera();
        updateFocusHex();
        animatingRef.current = false;
        return;
      }
    } else {
      // ── Momentum mode: coast with friction ──
      if (Math.abs(vel.vx) < MIN_VEL && Math.abs(vel.vy) < MIN_VEL) {
        // Momentum exhausted — snap to nearest hex center
        vel.vx = 0;
        vel.vy = 0;
        const nearest = snapToNearestHex(cam.x, cam.y);
        // Start a spring toward the nearest hex
        cameraTargetRef.current = nearest;
        applyCamera();
        updateFocusHex();
        // Keep animating — the spring will complete
        rafRef.current = requestAnimationFrame(tick);
        return;
      }

      cam.x += vel.vx;
      cam.y += vel.vy;
      clampCamera(cam);
      vel.vx *= FRICTION;
      vel.vy *= FRICTION;
    }

    applyCamera();
    updateFocusHex();

    rafRef.current = requestAnimationFrame(tick);
  }, [applyCamera, updateFocusHex, clampCamera, snapToNearestHex]);

  // ── Start/ensure the animation loop is running ──
  const ensureAnimating = useCallback(() => {
    if (!animatingRef.current) {
      animatingRef.current = true;
      rafRef.current = requestAnimationFrame(tick);
    }
  }, [tick]);

  // ── Smooth scroll handling — attached to viewport div only ──
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();

      // Cancel any spring target — user is manually scrolling
      cameraTargetRef.current = null;

      // Direct camera movement
      const speed = 1.2;
      cameraRef.current.x += e.deltaX * speed;
      cameraRef.current.y += e.deltaY * speed;
      clampCamera(cameraRef.current);

      // Set velocity for momentum coast
      cameraVelRef.current.vx = e.deltaX * speed * 0.35;
      cameraVelRef.current.vy = e.deltaY * speed * 0.35;

      applyCamera();
      updateFocusHex();

      // Start animation for momentum decay (will snap to hex when done)
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      animatingRef.current = true;
      rafRef.current = requestAnimationFrame(tick);
    };

    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, [applyCamera, updateFocusHex, tick, clampCamera]);

  // Keyboard: Escape to close detail view
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelectedJobId(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Cleanup rAF on unmount
  useEffect(() => {
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, []);

  // ── Report visible job IDs to backend (debounced, grouped by run) ──
  const visibleDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (jobGrid.length === 0) return;
    const visibleEntries = jobGrid.filter((e) => hexDistance(e, focusAxial) <= 1);
    const key = visibleEntries.map((e) => e.job.id).join(",");
    if (key === visibleReportedRef.current) return;

    if (visibleDebounceRef.current) clearTimeout(visibleDebounceRef.current);
    visibleDebounceRef.current = setTimeout(() => {
      if (key !== visibleReportedRef.current) {
        visibleReportedRef.current = key;
        // Group visible jobs by their run_id and report each group
        const byRun = new Map<string, string[]>();
        for (const e of visibleEntries) {
          const rid = e.job.run_id || runId || "";
          if (!rid) continue;
          const arr = byRun.get(rid) || [];
          arr.push(e.job.id);
          byRun.set(rid, arr);
        }
        for (const [rid, ids] of byRun) {
          setVisibleJobs(rid, ids).catch(() => {});
        }
      }
    }, 300);
  }, [runId, jobGrid, focusAxial]);

  // ── Click hex: center opens detail, others spring-scroll to it ──
  const handleHexClick = useCallback((q: number, r: number, jobId: string) => {
    const dist = hexDistance({ q, r }, focusAxial);
    if (dist === 0) {
      setSelectedJobId((prev) => (prev === jobId ? null : jobId));
    } else {
      // Set spring target — the animation loop will smoothly glide there
      const target = axialToPixel(q, r, hexSize);
      cameraTargetRef.current = { x: target.x, y: target.y };
      // Give it an initial nudge velocity toward target for snappy feel
      const dx = target.x - cameraRef.current.x;
      const dy = target.y - cameraRef.current.y;
      cameraVelRef.current = { vx: dx * 0.08, vy: dy * 0.08 };
      ensureAnimating();
    }
  }, [focusAxial, hexSize, ensureAnimating]);

  // ── Minimap jump: spring-scroll to that hex ──
  const handleMinimapJump = useCallback((q: number, r: number) => {
    const target = axialToPixel(q, r, hexSize);
    cameraTargetRef.current = { x: target.x, y: target.y };
    const dx = target.x - cameraRef.current.x;
    const dy = target.y - cameraRef.current.y;
    cameraVelRef.current = { vx: dx * 0.06, vy: dy * 0.06 };
    ensureAnimating();
  }, [hexSize, ensureAnimating]);

  // ── Derived ──
  const selectedJob = sorted.find((j) => j.id === selectedJobId) ?? null;
  const totalJobs = sorted.length;

  // Minimap positions
  const minimapPositions = useMemo(() =>
    jobGrid.map((e) => ({ q: e.q, r: e.r, jobId: e.job.id, status: e.job.status })),
    [jobGrid]
  );

  // In fullscreen mode, always render (even with no jobs — shows idle bees)
  // In non-fullscreen mode, don't render if no jobs
  if (!fullscreen && jobs.length === 0) return null;

  return (
    <div className={fullscreen ? "fixed inset-0 z-0" : "flex flex-col"}>
      {/* ── Hexagonal viewport ── */}
      <div
        ref={viewportRef}
        className={`relative overflow-hidden honeycomb-bg ${fullscreen ? "w-full h-full" : "rounded-b-xl"}`}
        style={fullscreen ? undefined : { height: "min(60vh, 550px)" }}
      >
        {/* Idle floating bees — ambient life across the entire screen */}
        <IdleBees />

        {/* Floating honey particles — spread across full screen */}
        <div className="absolute inset-0 z-[2] pointer-events-none overflow-hidden">
          {Array.from({ length: fullscreen ? 30 : 20 }).map((_, i) => (
            <div
              key={i}
              className="honey-particle"
              style={{
                left: `${3 + (i * 3.2) % 94}%`,
                bottom: `${-5 + (i * 5.3) % 30}%`,
                animationDelay: `${i * 0.3}s`,
                opacity: 0,
              }}
            />
          ))}
        </div>

        {/* Fixed warm spotlight — stays at viewport center, hexes move under it */}
        <div className="absolute inset-0 hive-spotlight z-[5] pointer-events-none" />

        {/* Grid container — smooth-scrolled via rAF transform, with optional goo filter */}
        <div
          ref={gridElRef}
          className="absolute inset-0 hive-grid"
        >
          {/* All hex cells in world-space — AnimatePresence for exit animations */}
          <AnimatePresence>
            {jobGrid.map((entry) => {
              if (flownAway.has(entry.job.id)) return null;
              const px = axialToPixel(entry.q, entry.r, hexSize);
            return (
              <HexSlot
                key={entry.job.id}
                targetX={px.x - hexSize}
                targetY={px.y - cellH / 2}
              >
                <HexCell
                    job={entry.job}
                  size={hexSize}
                    onClick={() => handleHexClick(entry.q, entry.r, entry.job.id)}
                    streamText={draftTokens.get(entry.job.id) || ""}
                    frame={frameData?.get(entry.job.id) || null}
                    onFlyAway={handleFlyAway}
                />
              </HexSlot>
            );
          })}
          </AnimatePresence>
        </div>

        {/* Minimap */}
        {totalJobs > 7 && (
          <HiveMinimap
            positions={minimapPositions}
            centerAxial={focusAxial}
            onJump={handleMinimapJump}
          />
        )}
      </div>

      {/* ── Expanded detail panel (modal overlay) ── */}
      <AnimatePresence>
        {selectedJob && (
          <ExpandedBrowserView
            key={selectedJob.id}
            job={selectedJob}
            frame={frameData?.get(selectedJob.id) || null}
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
