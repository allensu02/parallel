"use client";

import { useState, useCallback, useMemo, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Check, X, Edit3, Loader2, Mail, Sparkles,
  Clock, AlertCircle, SkipForward, Hexagon, ChevronDown, ChevronRight,
} from "lucide-react";
import { approveJob, fetchThreadContent, setVisibleJobs, type Job, type ThreadContent } from "@/lib/api";

/* ═══════════════════════════════════════════════════════════════════════════
   Types & Props
   ═══════════════════════════════════════════════════════════════════════════ */

interface LiveDraftPanelProps {
  jobs: Job[];
  runId: string;
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
  if (status === "skipped") return { text: "Skipped", icon: <SkipForward className="w-3 h-3" />, color: "text-muted", bgColor: "bg-border/40" };
  if (status === "pending_approval") return { text: "Review", icon: <Edit3 className="w-3 h-3" />, color: "text-warning", bgColor: "bg-warning/30" };
  if (step === "waiting_for_input") return { text: "Input", icon: <Clock className="w-3 h-3" />, color: "text-warning", bgColor: "bg-warning/20" };
  if (step === "fetch_thread") return { text: "Reading", icon: <Mail className="w-3 h-3" />, color: "text-primary", bgColor: "bg-primary/25" };
  if (step === "classify_intent") return { text: "Classify", icon: <Sparkles className="w-3 h-3" />, color: "text-primary", bgColor: "bg-primary/20" };
  if (step === "generate_draft") return { text: "Drafting", icon: <Edit3 className="w-3 h-3" />, color: "text-primary", bgColor: "bg-primary/25" };
  if (step === "visual_compose") return { text: "Typing", icon: <Edit3 className="w-3 h-3 animate-pulse" />, color: "text-primary", bgColor: "bg-primary/30" };
  if (step === "save_draft") return { text: "Saving", icon: <Loader2 className="w-3 h-3 animate-spin" />, color: "text-primary", bgColor: "bg-primary/20" };
  if (step === "apply_label") return { text: "Label", icon: <Loader2 className="w-3 h-3 animate-spin" />, color: "text-primary", bgColor: "bg-primary/20" };
  if (status === "queued") return { text: "Queued", icon: <Clock className="w-3 h-3" />, color: "text-muted", bgColor: "bg-border/40" };
  return { text: "Working", icon: <Loader2 className="w-3 h-3 animate-spin" />, color: "text-primary", bgColor: "bg-primary/20" };
}

/* ═══════════════════════════════════════════════════════════════════════════
   Axial hex coordinate system
   ═══════════════════════════════════════════════════════════════════════════ */

interface Axial { q: number; r: number }

/** 6 neighbor offsets in axial coords (flat-top hex). */
const HEX_DIRS: Axial[] = [
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

/** Snap a 2D direction vector to the nearest of 6 hex directions. Returns direction index 0-5. */
function snapToHexDirection(dx: number, dy: number): number {
  // Convert to angle and snap to nearest 60° sector
  const angle = Math.atan2(dy, dx); // radians, 0 = right
  // Hex directions in screen-space angles (flat-top):
  // 0: N = -90°, 1: NE = -30°, 2: SE = 30°, 3: S = 90°, 4: SW = 150°, 5: NW = -150°/210°
  const dirAngles = [-Math.PI / 2, -Math.PI / 6, Math.PI / 6, Math.PI / 2, (5 * Math.PI) / 6, -(5 * Math.PI) / 6];
  let best = 0;
  let bestDist = Infinity;
  for (let i = 0; i < 6; i++) {
    let diff = Math.abs(angle - dirAngles[i]);
    if (diff > Math.PI) diff = 2 * Math.PI - diff;
    if (diff < bestDist) { bestDist = diff; best = i; }
  }
  return best;
}


/* ═══════════════════════════════════════════════════════════════════════════
   Expanded browser view (modal overlay on hex click)
   ═══════════════════════════════════════════════════════════════════════════ */

function ExpandedBrowserView({
  job, runId, frame, streamText, contentCache, onJobUpdated, onClose,
}: {
  job: Job; runId: string; frame: string | null; streamText: string;
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
  const hasFrame = !!frame;

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
            <span className="truncate">{job.subject || "(loading...)"}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className={`flex items-center gap-1 text-[10px] font-medium ${statusColor}`}>{statusIcon} {statusText}</span>
            <button onClick={onClose} className="text-muted hover:text-foreground transition-colors"><X className="w-4 h-4" /></button>
          </div>
        </div>

        {/* Live browser frame */}
        {hasFrame && (
          <div className="relative bg-black">
            <img src={`data:image/jpeg;base64,${frame}`} alt="Live browser view" className="w-full h-auto max-h-[450px] object-contain" />
            <div className="absolute top-2 right-2 bg-black/60 text-white text-[9px] px-1.5 py-0.5 rounded font-mono flex items-center gap-1">
              <div className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />LIVE
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
              {job.intent === "ignore" ? "Newsletter/notification — skipped" : job.intent === "escalate" ? "Needs human review — escalated" : "Discarded"}
            </div>
          ) : job.status === "failed" ? (
            <div className="text-xs text-error/80">{job.error_msg || "Unknown error"}</div>
          ) : (
            <div className="flex items-center gap-2 text-xs text-muted"><Loader2 className="w-3 h-3 animate-spin text-primary" />{statusText}</div>
          )}
        </div>

        {/* Email context */}
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
   Individual hex cell — visual hierarchy based on role
   ═══════════════════════════════════════════════════════════════════════════ */

type HexRole = "center" | "neighbor" | "outer";

function HexCell({
  job, size, onClick, role, streamText, frame,
}: {
  job: Job; size: number; onClick: () => void; role: HexRole;
  streamText: string; frame: string | null;
}) {
  const { text: statusText, icon: statusIcon, color: statusColor } = statusLabel(job);
  const isDrafting = job.current_step === "generate_draft" && job.status === "running";
  const isVisualCompose = job.current_step === "visual_compose";
  const isPending = job.status === "pending_approval";
  const hasFrame = !!frame;
  const isCenter = role === "center";
  const isNeighbor = role === "neighbor";

  // Show live typing when we have streaming text
  const displayText = streamText || job.draft_text || "";
  const isTyping = isDrafting && !!streamText;

  const w = size * 2;
  const h = size * Math.sqrt(3);
  const label = job.subject || (job.thread_id ? job.thread_id.slice(0, 8) : "...");

  // Visual hierarchy — opacity/scale/border only, NO box-shadow
  const scaleStyle = isCenter ? 1.04 : isNeighbor ? 1 : 0.88;
  const opacityStyle = isCenter ? 1 : isNeighbor ? 0.85 : 0.25;
  const borderColor = isCenter
    ? "rgba(240, 192, 64, 0.7)"
    : isNeighbor
    ? "rgba(212, 162, 78, 0.25)"
    : "rgba(61, 52, 37, 0.12)";
  const filterStyle = role === "outer" ? "brightness(0.45) saturate(0.5)" : "none";

  return (
    <div
      onClick={onClick}
      className="cursor-pointer group hex-transition"
      style={{
        position: "relative",
        width: w,
        height: h,
        transform: `scale(${scaleStyle})`,
        opacity: opacityStyle,
        filter: filterStyle,
        zIndex: isCenter ? 5 : isNeighbor ? 3 : 1,
      }}
    >
      {/* Outer hex (border) */}
      <div
        className="absolute inset-0 hex-cell"
        style={{
          width: w,
          height: h,
          backgroundColor: borderColor,
        }}
      />
      {/* Inner hex (content) */}
      <div
        className={`absolute hex-cell overflow-hidden ${
          hasFrame ? "bg-black" : `bg-surface ${isDrafting || isVisualCompose ? "animate-honey-glow" : ""}`
        }`}
        style={{
          width: w - (isCenter ? 4 : 3),
          height: h - (isCenter ? 4 : 3),
          left: isCenter ? 2 : 1.5,
          top: isCenter ? 2 : 1.5,
        }}
      >
        {hasFrame ? (
          /* ── Live browser frame from CDP screencast ── */
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
            <div className="absolute top-2 right-2 flex items-center gap-1 bg-black/50 rounded px-1.5 py-0.5">
              <div className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
              <span className="text-[8px] text-white/80 font-mono">LIVE</span>
            </div>
          </div>
        ) : displayText ? (
          /* ── Live compose view: show streaming draft text ── */
          <div className="flex flex-col w-full h-full">
            <div className="flex items-center gap-1.5 px-2.5 py-1 bg-surface-2/80 border-b border-border/20 shrink-0">
              <div className={`${statusColor}`}>{statusIcon}</div>
              <span className="text-[8px] font-medium text-foreground/70 truncate flex-1">{label}</span>
              {isTyping && (
                <div className="flex items-center gap-1">
                  <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                  <span className="text-[7px] text-primary font-mono">typing</span>
                </div>
              )}
              {isPending && <span className="text-[7px] text-warning font-semibold">REVIEW</span>}
            </div>
            <div className="flex-1 px-2.5 py-1.5 overflow-hidden">
              <div className="text-[8px] leading-[1.4] text-foreground/80 font-mono whitespace-pre-wrap">
                {displayText}
                {isTyping && <span className="inline-block w-[4px] h-[10px] bg-primary/80 ml-[1px] animate-pulse" />}
              </div>
            </div>
          </div>
        ) : (
          /* ── Status view: pre-drafting or idle ── */
          <div className="flex flex-col items-center justify-center gap-1 px-3 text-center w-full h-full">
            <div className={`${statusColor} ${isCenter ? "scale-150" : "scale-125"}`}>{statusIcon}</div>
            <div className={`text-[10px] font-medium leading-tight text-foreground/80 max-w-[85%] line-clamp-2 text-center ${isCenter ? "text-[11px]" : ""}`}>
              {label}
            </div>
            <div className={`text-[9px] font-semibold ${statusColor}`}>{statusText}</div>
            {isPending && isCenter && (
              <div className="mt-0.5 text-[8px] text-warning font-medium">Click to review</div>
            )}
          </div>
        )}
      </div>
    </div>
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
      className="absolute bottom-3 right-3 bg-surface/90 backdrop-blur-sm border border-border/40 rounded-lg overflow-hidden z-30"
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

          let fill = "#3d3425";
          if (pos.status === "completed") fill = "#7cb342";
          else if (pos.status === "failed") fill = "#e53935";
          else if (pos.status === "running") fill = "#d4a24e";
          else if (pos.status === "pending_approval") fill = "#f9a825";

          return (
            <circle
              key={`${pos.q},${pos.r}`}
              cx={px.x}
              cy={px.y}
              r={isCurrent ? 3.5 : isVisible ? 2.5 : 2}
              fill={fill}
              opacity={isVisible ? 1 : 0.5}
              stroke={isCurrent ? "#f0c040" : "none"}
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
   Main panel — Spotlight-centered hex camera navigation
   ═══════════════════════════════════════════════════════════════════════════ */

export default function LiveDraftPanel({
  jobs, runId, draftTokens, frameData, contentCache, onJobUpdated,
}: LiveDraftPanelProps) {
  // ── State ──
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [cameraAxial, setCameraAxial] = useState<Axial>({ q: 0, r: 0 }); // target camera position
  const containerRef = useRef<HTMLDivElement>(null);
  const cameraElRef = useRef<HTMLDivElement>(null); // direct DOM ref for rAF
  const [containerSize, setContainerSize] = useState({ w: 900, h: 600 });
  const visibleReportedRef = useRef<string>("");

  // rAF-driven smooth camera interpolation
  const cameraCurrentRef = useRef({ x: 0, y: 0 }); // current animated pixel position
  const cameraTargetRef = useRef({ x: 0, y: 0 }); // target pixel position
  const cameraRafRef = useRef<number>(0);
  const cameraAnimStartRef = useRef(0);

  // Momentum scroll state
  const scrollAccRef = useRef({ dx: 0, dy: 0 });
  const scrollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastStepTimeRef = useRef(0);
  const momentumQueueRef = useRef<number[]>([]);
  const momentumRafRef = useRef<number>(0);
  const cameraAxialRef = useRef<Axial>(cameraAxial);
  cameraAxialRef.current = cameraAxial;

  // ── Measure container ──
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => setContainerSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const obs = new ResizeObserver(() => measure());
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // ── Sort jobs by priority ──
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

  // Quick lookup: hex key → grid entry
  const gridMap = useMemo(() => {
    const m = new Map<string, (typeof jobGrid)[number]>();
    for (const entry of jobGrid) m.set(hexKey(entry.q, entry.r), entry);
    return m;
  }, [jobGrid]);

  // ── Hex sizing: fit 7 hexes (3 wide, 3 tall) ──
  const hexSize = useMemo(() => {
    const padW = 60, padH = 60;
    const availW = containerSize.w - padW;
    const availH = containerSize.h - padH;
    const sizeFromW = availW / 5.5;
    const sizeFromH = availH / (3 * Math.sqrt(3));
    return Math.max(50, Math.min(150, Math.floor(Math.min(sizeFromW, sizeFromH))));
  }, [containerSize]);

  // ── Determine which hexes to render: center + 1-ring neighbors + 2–4 ring buffer ──
  const renderedHexes = useMemo(() => {
    const result: { job: Job; q: number; r: number; role: HexRole }[] = [];
    const centerEntry = gridMap.get(hexKey(cameraAxial.q, cameraAxial.r));

    for (const entry of jobGrid) {
      const dist = hexDistance(entry, cameraAxial);
      if (dist > 4) continue; // Render up to 4 rings for smooth transitions
      let role: HexRole = "outer";
      if (dist === 0) role = "center";
      else if (dist === 1) role = "neighbor";
      result.push({ job: entry.job, q: entry.q, r: entry.r, role });
    }

    // If camera isn't on a valid hex, snap to nearest
    if (!centerEntry && jobGrid.length > 0) {
      // Find the nearest hex
      let nearest = jobGrid[0];
      let bestDist = Infinity;
      for (const entry of jobGrid) {
        const d = hexDistance(entry, cameraAxial);
        if (d < bestDist) { bestDist = d; nearest = entry; }
      }
      // Will be corrected by the snap effect below
    }

    return result;
  }, [cameraAxial, jobGrid, gridMap]);

  // ── Camera pixel target (computed from axial target) ──
  const cameraTargetPixel = useMemo(() => axialToPixel(cameraAxial.q, cameraAxial.r, hexSize), [cameraAxial, hexSize]);

  // ── rAF camera animation loop — smooth easeInOutCubic interpolation ──
  const ANIM_DURATION = 280; // ms — visible, smooth glide
  const STEP_COOLDOWN = 200; // ms between navigation steps

  // Start a new camera animation whenever target changes
  useEffect(() => {
    cameraTargetRef.current = cameraTargetPixel;
    const startPos = { ...cameraCurrentRef.current };
    const startTime = performance.now();
    cameraAnimStartRef.current = startTime;

    const easeInOutCubic = (t: number): number => {
      return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    };

    const animate = (now: number) => {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / ANIM_DURATION, 1);
      const eased = easeInOutCubic(progress);

      const x = startPos.x + (cameraTargetRef.current.x - startPos.x) * eased;
      const y = startPos.y + (cameraTargetRef.current.y - startPos.y) * eased;

      cameraCurrentRef.current = { x, y };

      // Direct DOM update — no React re-render, 60fps smooth
      if (cameraElRef.current) {
        cameraElRef.current.style.transform = `translate(${-x}px, ${-y}px)`;
      }

      if (progress < 1) {
        cameraRafRef.current = requestAnimationFrame(animate);
      }
    };

    if (cameraRafRef.current) cancelAnimationFrame(cameraRafRef.current);
    cameraRafRef.current = requestAnimationFrame(animate);

    return () => { if (cameraRafRef.current) cancelAnimationFrame(cameraRafRef.current); };
  }, [cameraTargetPixel]);

  // ── Navigation: move camera to a hex ──
  const navigateTo = useCallback((target: Axial) => {
    if (gridMap.has(hexKey(target.q, target.r))) {
      lastStepTimeRef.current = performance.now();
      setCameraAxial(target);
    }
  }, [gridMap]);

  const navigateDirection = useCallback((dirIdx: number) => {
    const cur = cameraAxialRef.current;
    const dir = HEX_DIRS[dirIdx];
    const target: Axial = { q: cur.q + dir.q, r: cur.r + dir.r };
    if (gridMap.has(hexKey(target.q, target.r))) {
      navigateTo(target);
      return true;
    }
    return false;
  }, [gridMap, navigateTo]);

  // ── Momentum step processor ──
  const processMomentumQueue = useCallback(() => {
    const queue = momentumQueueRef.current;
    if (queue.length === 0) return;

    const now = performance.now();
    const elapsed = now - lastStepTimeRef.current;
    if (elapsed >= STEP_COOLDOWN) {
      const dirIdx = queue.shift()!;
      navigateDirection(dirIdx);
    }

    if (queue.length > 0) {
      momentumRafRef.current = requestAnimationFrame(processMomentumQueue);
    }
  }, [navigateDirection]);

  // Cleanup rAF on unmount
  useEffect(() => {
    return () => {
      if (momentumRafRef.current) cancelAnimationFrame(momentumRafRef.current);
      if (cameraRafRef.current) cancelAnimationFrame(cameraRafRef.current);
    };
  }, []);

  // ── Scroll → direction snap with momentum ──
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const THRESHOLD = 28; // px — lower for trackpad sensitivity
    const MOMENTUM_VELOCITY_THRESHOLD = 300; // px/s to trigger extra steps

    let lastScrollTime = 0;
    let scrollVelocity = 0;

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();

      const now = performance.now();
      const dt = now - lastScrollTime;
      lastScrollTime = now;

      // Negate for natural/drag-like feel: swipe right → camera moves right → see right hex
      scrollAccRef.current.dx -= e.deltaX;
      scrollAccRef.current.dy -= e.deltaY;

      // Track velocity (exponential moving average)
      const instantVel = Math.sqrt(e.deltaX ** 2 + e.deltaY ** 2) / Math.max(dt, 8) * 1000;
      scrollVelocity = scrollVelocity * 0.3 + instantVel * 0.7;

      const mag = Math.sqrt(scrollAccRef.current.dx ** 2 + scrollAccRef.current.dy ** 2);
      if (mag >= THRESHOLD) {
        const timeSinceStep = now - lastStepTimeRef.current;
        // Allow step if cooldown has passed or if it's the first step
        if (timeSinceStep >= STEP_COOLDOWN * 0.7) {
          const dirIdx = snapToHexDirection(scrollAccRef.current.dx, scrollAccRef.current.dy);
          navigateDirection(dirIdx);

          // Momentum: if velocity is high, queue additional steps with decay
          if (scrollVelocity > MOMENTUM_VELOCITY_THRESHOLD) {
            const extraSteps = Math.min(Math.floor(scrollVelocity / MOMENTUM_VELOCITY_THRESHOLD), 4);
            momentumQueueRef.current = [];
            for (let i = 0; i < extraSteps; i++) {
              momentumQueueRef.current.push(dirIdx);
            }
            if (momentumRafRef.current) cancelAnimationFrame(momentumRafRef.current);
            momentumRafRef.current = requestAnimationFrame(processMomentumQueue);
          }
        }

        scrollAccRef.current = { dx: 0, dy: 0 };
        scrollVelocity = 0;
      }

      // Reset accumulator after idle
      if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
      scrollTimeoutRef.current = setTimeout(() => {
        scrollAccRef.current = { dx: 0, dy: 0 };
        scrollVelocity = 0;
        momentumQueueRef.current = [];
      }, 250);
    };

    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, [navigateDirection, processMomentumQueue]);

  // ── Keyboard navigation (6 directions) ──
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      let dirIdx = -1;
      // W/E/D/S/A/Q hex-style mapping
      switch (e.key.toLowerCase()) {
        case "w": dirIdx = 0; break; // N
        case "e": dirIdx = 1; break; // NE
        case "d": dirIdx = 2; break; // SE
        case "s": dirIdx = 3; break; // S
        case "a": dirIdx = 4; break; // SW
        case "q": dirIdx = 5; break; // NW
        case "arrowup": dirIdx = 0; break;
        case "arrowright": dirIdx = 2; break;
        case "arrowdown": dirIdx = 3; break;
        case "arrowleft": dirIdx = 5; break;
        case "escape": setSelectedJobId(null); return;
        default: return;
      }
      if (dirIdx >= 0) {
        e.preventDefault();
        navigateDirection(dirIdx);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [navigateDirection]);

  // ── Snap camera to nearest valid hex if current position has no hex ──
  useEffect(() => {
    if (jobGrid.length === 0) return;
    if (gridMap.has(hexKey(cameraAxial.q, cameraAxial.r))) return;
    // Find nearest
    let nearest = jobGrid[0];
    let bestDist = Infinity;
    for (const entry of jobGrid) {
      const d = hexDistance(entry, cameraAxial);
      if (d < bestDist) { bestDist = d; nearest = entry; }
    }
    setCameraAxial({ q: nearest.q, r: nearest.r });
  }, [jobGrid, gridMap, cameraAxial]);

  // ── Report visible job IDs to backend (debounced) ──
  const visibleDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!runId || renderedHexes.length === 0) return;
    const visibleIds = renderedHexes
      .filter((h) => h.role === "center" || h.role === "neighbor")
      .map((h) => h.job.id);
    const key = visibleIds.join(",");
    if (key === visibleReportedRef.current) return;

    // Debounce: wait 300ms before reporting to avoid spam during navigation
    if (visibleDebounceRef.current) clearTimeout(visibleDebounceRef.current);
    visibleDebounceRef.current = setTimeout(() => {
      if (key !== visibleReportedRef.current) {
        visibleReportedRef.current = key;
        setVisibleJobs(runId, visibleIds).catch(() => {});
      }
    }, 300);
  }, [runId, renderedHexes]);

  // ── Click-to-jump: any visible hex ──
  const handleHexClick = useCallback((q: number, r: number, jobId: string) => {
    const dist = hexDistance({ q, r }, cameraAxial);
    if (dist === 0) {
      // Center hex: open detail view
      setSelectedJobId((prev) => (prev === jobId ? null : jobId));
    } else {
      // Non-center: navigate to it
      navigateTo({ q, r });
    }
  }, [cameraAxial, navigateTo]);

  // ── Minimap jump ──
  const handleMinimapJump = useCallback((q: number, r: number) => {
    navigateTo({ q, r });
  }, [navigateTo]);

  // ── Derived ──
  const selectedJob = sorted.find((j) => j.id === selectedJobId) ?? null;
  const totalJobs = sorted.length;
  const centerEntry = gridMap.get(hexKey(cameraAxial.q, cameraAxial.r));
  const centerLabel = centerEntry?.job.subject || `(${cameraAxial.q}, ${cameraAxial.r})`;
  const cellW = hexSize * 2;
  const cellH = hexSize * Math.sqrt(3);

  // Minimap positions
  const minimapPositions = useMemo(() =>
    jobGrid.map((e) => ({ q: e.q, r: e.r, jobId: e.job.id, status: e.job.status })),
    [jobGrid]
  );

  if (jobs.length === 0) return null;

  return (
    <div className="flex flex-col h-full" ref={containerRef}>
      {/* Navigation header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border/30">
        <div className="text-xs text-muted flex items-center gap-2">
          <Hexagon className="w-3.5 h-3.5 text-primary" />
          <span>
            Focused on <strong className="text-foreground truncate max-w-[200px] inline-block align-bottom">{centerLabel}</strong>
          </span>
          <span className="text-muted/60">
            ({renderedHexes.filter((h) => h.role === "center" || h.role === "neighbor").length} of {totalJobs} visible)
          </span>
        </div>
        <div className="flex items-center gap-3 text-[10px] text-muted">
          <span className="opacity-60">Scroll or WASDQE to navigate</span>
        </div>
      </div>

      {/* ── Hexagonal viewport ── */}
      <div className="flex-1 relative overflow-hidden min-h-[400px]">
        {/* Global warm spotlight — fixed to viewport center, lights the focused area */}
        <div className="absolute inset-0 hive-spotlight z-10" />

        {/* Camera container — rAF drives transform directly on this element */}
        <div
          ref={cameraElRef}
          className="absolute inset-0 hive-camera hive-ambient"
          style={{
            transform: `translate(${-cameraTargetPixel.x}px, ${-cameraTargetPixel.y}px)`,
          }}
        >
          {/* All hex cells positioned in world-space */}
          {renderedHexes.map((hex) => {
            const px = axialToPixel(hex.q, hex.r, hexSize);
            return (
              <div
                key={hex.job.id}
                className="absolute hex-transition"
                style={{
                  left: `calc(50% + ${px.x - hexSize}px)`,
                  top: `calc(50% + ${px.y - cellH / 2}px)`,
                  // GPU-accelerated positioning — no reflow
                  willChange: "opacity, transform",
                }}
              >
                <HexCell
                  job={hex.job}
                  size={hexSize}
                  onClick={() => handleHexClick(hex.q, hex.r, hex.job.id)}
                  role={hex.role}
                  streamText={draftTokens.get(hex.job.id) || ""}
                  frame={frameData?.get(hex.job.id) || null}
                />
              </div>
            );
          })}
        </div>

        {/* Minimap */}
        {totalJobs > 7 && (
          <HiveMinimap
            positions={minimapPositions}
            centerAxial={cameraAxial}
            onJump={handleMinimapJump}
          />
        )}

        {/* Coordinate indicator */}
        <div className="absolute bottom-3 left-3 text-[10px] text-muted/40 font-mono z-30">
          ({cameraAxial.q}, {cameraAxial.r})
        </div>
      </div>

      {/* ── Expanded detail panel (modal overlay) ── */}
      <AnimatePresence>
        {selectedJob && (
          <ExpandedBrowserView
            key={selectedJob.id}
            job={selectedJob}
            runId={runId}
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
