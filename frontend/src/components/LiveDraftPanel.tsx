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

/** Whether this job uses a local Playwright browser (screencast frames). */
function usesLocalBrowser(job: Job): boolean {
  const local = ["gmail", "slides", "sheets", "docs", "forms", "drive"];
  return local.includes(job.pipeline_type || "gmail");
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

/** 6 neighbor offsets in axial coords (flat-top hex). */
const _HEX_DIRS: Axial[] = [
  { q: 0, r: -1 },   // 0: N   (up)
  { q: 1, r: -1 },   // 1: NE  (upper-right)
  { q: 1, r: 0 },    // 2: SE  (lower-right)
  { q: 0, r: 1 },    // 3: S   (down)
  { q: -1, r: 1 },   // 4: SW  (lower-left)
  { q: -1, r: 0 },   // 5: NW  (upper-left)
];

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
        className="rounded-xl border border-border bg-surface overflow-hidden hover-glow max-w-2xl w-full max-h-[85vh] overflow-y-auto shadow-2xl"
      >
        {/* Header */}
        <div className="px-4 py-2.5 border-b border-border/50 flex items-center justify-between bg-surface-2 sticky top-0 z-10">
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
    </motion.div>
  );
}


/* ═══════════════════════════════════════════════════════════════════════════
   Individual hex cell — uniform styling, spotlight overlay creates hierarchy
   With fly-away animation for completed tasks
   ═══════════════════════════════════════════════════════════════════════════ */

/** Track previous status to detect completion transitions */
function usePrevious<T>(value: T): T | undefined {
  const ref = useRef<T | undefined>(undefined);
  useEffect(() => { ref.current = value; });
  return ref.current;
}

const DONE_STATUSES = ["completed", "skipped", "failed"];

function HexCell({
  job, size, onClick, streamText, frame, onFlyAway,
}: {
  job: Job; size: number; onClick: () => void;
  streamText: string; frame: string | null;
  onFlyAway?: (jobId: string) => void;
}) {
  const prevStatus = usePrevious(job.status);
  const [flyingAway, setFlyingAway] = useState(false);

  // Detect transition TO a done status — trigger fly-away
  useEffect(() => {
    if (
      prevStatus &&
      !DONE_STATUSES.includes(prevStatus) &&
      DONE_STATUSES.includes(job.status)
    ) {
      // Brief flash of final state, then dramatic fly-away
      const timer = setTimeout(() => {
        setFlyingAway(true);
        // After fly-away animation completes (1.4s), notify parent to remove from grid
        setTimeout(() => onFlyAway?.(job.id), 1500);
      }, 1200);
      return () => clearTimeout(timer);
    }
  }, [job.status, prevStatus, job.id, onFlyAway]);
  const { text: statusText, icon: statusIcon, color: statusColor } = statusLabel(job);
  const isDrafting = job.current_step === "generate_draft" && job.status === "running";
  const isVisualCompose = job.current_step === "visual_compose";
  const isPending = job.status === "pending_approval";
  const hasFrame = !!frame;
  const pipelineType = job.pipeline_type || "gmail";

  const displayText = streamText || job.draft_text || "";
  const isTyping = isDrafting && !!streamText;

  const w = size * 2;
  const h = size * Math.sqrt(3);
  const label = jobLabel(job);

  // ── Dramatic fly-away (upward, big arc) ──
  const flyAngle = useRef((Math.random() - 0.5) * 100); // -50° to +50°
  const flyDistance = useRef(800 + Math.random() * 600);  // 800–1400px away
  const flyX = Math.sin(flyAngle.current * Math.PI / 180) * flyDistance.current;
  const flyY = -(flyDistance.current * 0.8 + Math.random() * 300);
  const flyRotation = useRef((Math.random() - 0.5) * 720); // up to ±360° spin

  // ── Dramatic fly-in (from far offscreen below/sides) ──
  const entryDirection = useRef(Math.random()); // 0-1: picks random cardinal-ish direction
  const entryDistance = useRef(600 + Math.random() * 500); // 600–1100px
  const entryAngleRad = useRef(
    entryDirection.current < 0.3 ? Math.PI * 0.5 + (Math.random() - 0.5) * 0.8  // from below
    : entryDirection.current < 0.6 ? (Math.random() - 0.5) * 1.2  // from above-ish
    : entryDirection.current < 0.8 ? Math.PI * 0.25 + Math.random() * 0.5  // from bottom-right
    : Math.PI * 0.75 + Math.random() * 0.5  // from bottom-left
  );
  const entryX = Math.cos(entryAngleRad.current) * entryDistance.current * (Math.random() > 0.5 ? 1 : -1);
  const entryY = Math.sin(entryAngleRad.current) * entryDistance.current;
  const entryRotation = useRef((Math.random() - 0.5) * 180);
  const entryDelay = useRef(Math.random() * 0.4);

  return (
    <motion.div
      initial={{
        x: entryX,
        y: entryY,
        scale: 0.1,
        opacity: 0,
        rotate: entryRotation.current,
      }}
      animate={flyingAway ? {
        x: flyX,
        y: flyY,
        scale: 0.1,
        opacity: 0,
        rotate: flyRotation.current,
      } : {
        x: 0,
        y: 0,
        scale: 1,
        opacity: 1,
        rotate: 0,
      }}
      exit={{
        x: flyX,
        y: flyY,
        scale: 0.1,
        opacity: 0,
        rotate: flyRotation.current,
      }}
      transition={flyingAway ? {
        duration: 1.4,
        ease: [0.22, 0, 0.36, 0],
      } : {
        type: "spring",
        stiffness: 60,
        damping: 10,
        mass: 1.2,
        delay: entryDelay.current,
      }}
      onClick={onClick}
      className={`cursor-pointer group ${flyingAway ? "bee-fly-away" : "bee-fly-in"}`}
      style={{
        position: "relative",
        width: w,
        height: h,
      }}
    >
      {/* Outer hex (border) — bright golden */}
      <div
        className={`absolute inset-0 hex-cell transition-all duration-300 group-hover:brightness-130 ${
          job.status === "running" ? "animate-honey-glow" : ""
        }`}
        style={{
          width: w,
          height: h,
          backgroundColor: job.status === "running"
            ? "rgba(255, 224, 102, 0.55)"
            : job.status === "completed"
            ? "rgba(160, 217, 17, 0.45)"
            : job.status === "failed"
            ? "rgba(255, 82, 82, 0.35)"
            : "rgba(255, 184, 0, 0.35)",
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
  const scale = Math.min(clampedW / w, clampedH / h, 1);

  return (
    <div
      className="absolute bottom-3 right-3 bg-surface/85 backdrop-blur-md border border-honey/20 rounded-lg overflow-hidden z-30 shadow-lg shadow-honey/10"
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

          let fill = "#5a4828";
          if (pos.status === "completed") fill = "#a0d911";
          else if (pos.status === "failed") fill = "#ff5252";
          else if (pos.status === "running") fill = "#ffe066";
          else if (pos.status === "pending_approval") fill = "#ffab00";

          return (
            <circle
              key={`${pos.q},${pos.r}`}
              cx={px.x}
              cy={px.y}
              r={isCurrent ? 3.5 : isVisible ? 2.5 : 2}
              fill={fill}
              opacity={isVisible ? 1 : 0.5}
              stroke={isCurrent ? "#ffe066" : "none"}
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
   Main panel — Smooth continuous scrolling, fixed warm spotlight
   ═══════════════════════════════════════════════════════════════════════════ */

export default function LiveDraftPanel({
  jobs, runId, draftTokens, frameData, contentCache, onJobUpdated,
}: LiveDraftPanelProps) {
  // ── State ──
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const gridElRef = useRef<HTMLDivElement>(null);
  const [containerSize, setContainerSize] = useState({ w: 900, h: 600 });
  const visibleReportedRef = useRef<string>("");

  // Camera position in world-space pixels — continuously updated via rAF
  const cameraRef = useRef({ x: 0, y: 0 });
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

  const handleFlyAway = useCallback((jobId: string) => {
    setFlownAway(prev => {
      const next = new Set(prev);
      next.add(jobId);
      return next;
    });
  }, []);

  // Reset fly-away tracking when jobs list changes (filter change brings them back)
  const jobIdsKey = useMemo(() => jobs.map(j => j.id).sort().join(","), [jobs]);
  useEffect(() => {
    setFlownAway(new Set());
  }, [jobIdsKey]);

  // ── Sort jobs by priority (already sorted by parent now, but keep as safety) ──
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

  // ── Assign every job a hex position on the spiral ──
  const allPositions = useMemo(() => hexSpiralPositions(sorted.length), [sorted.length]);
  const jobGrid = useMemo(() => {
    return sorted.map((job, i) => ({
      job,
      q: allPositions[i]?.q ?? 0,
      r: allPositions[i]?.r ?? 0,
    }));
  }, [sorted, allPositions]);

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
  const centerEntry = gridMap.get(hexKey(focusAxial.q, focusAxial.r));
  const centerLabel = centerEntry ? jobLabel(centerEntry.job) : "";

  // Minimap positions
  const minimapPositions = useMemo(() =>
    jobGrid.map((e) => ({ q: e.q, r: e.r, jobId: e.job.id, status: e.job.status })),
    [jobGrid]
  );

  if (jobs.length === 0) return null;

  return (
    <div className="flex flex-col">
      {/* Navigation header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-honey/20 bg-gradient-to-r from-surface-2/70 via-surface/80 to-surface-2/70 backdrop-blur-sm">
        <div className="text-xs text-foreground/70 flex items-center gap-2">
          <motion.div
            animate={{ rotate: [0, 8, -8, 0] }}
            transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
            className="w-6 h-6 hex-badge bg-gradient-to-br from-honey to-primary flex items-center justify-center"
          >
            <Hexagon className="w-3.5 h-3.5 text-background" />
          </motion.div>
          {centerLabel && (
            <span>
              Focused on <strong className="text-golden truncate max-w-[200px] inline-block align-bottom">{centerLabel}</strong>
            </span>
          )}
          <span className="text-honey/60 font-semibold">
            {totalJobs} bee{totalJobs !== 1 ? "s" : ""}
          </span>
        </div>
        <div className="flex items-center gap-3 text-[10px] text-honey/40 font-medium">
          <span>Scroll to navigate the hive</span>
        </div>
      </div>

      {/* ── Hexagonal viewport — explicit height, captures wheel events ── */}
      <div ref={viewportRef} className="relative overflow-hidden rounded-b-xl honeycomb-bg" style={{ height: "min(60vh, 550px)" }}>
        {/* Floating honey particles — lots of ambient life */}
        <div className="absolute inset-0 z-5 pointer-events-none overflow-hidden">
          {Array.from({ length: 20 }).map((_, i) => (
            <div
              key={i}
              className="honey-particle"
              style={{
                left: `${5 + (i * 4.7) % 90}%`,
                bottom: `${-8 + (i * 7) % 25}%`,
                animationDelay: `${i * 0.35}s`,
                opacity: 0,
              }}
            />
          ))}
        </div>
        {/* Fixed warm spotlight — stays at viewport center, hexes move under it */}
        <div className="absolute inset-0 hive-spotlight z-10 pointer-events-none" />

        {/* Grid container — smooth-scrolled via rAF transform */}
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
                <div
                  key={entry.job.id}
                  className="absolute"
                  style={{
                    left: `calc(50% + ${px.x - hexSize}px)`,
                    top: `calc(50% + ${px.y - cellH / 2}px)`,
                  }}
                >
                  <HexCell
                    job={entry.job}
                    size={hexSize}
                    onClick={() => handleHexClick(entry.q, entry.r, entry.job.id)}
                    streamText={draftTokens.get(entry.job.id) || ""}
                    frame={frameData?.get(entry.job.id) || null}
                    onFlyAway={handleFlyAway}
                  />
                </div>
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
