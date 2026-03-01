"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { motion } from "framer-motion";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";

function CallbackContent() {
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");

  useEffect(() => {
    // The backend redirects here with ?auth=success after OAuth completes
    const auth = searchParams.get("auth");
    if (auth === "success") {
      setStatus("success");
      setTimeout(() => {
        window.location.href = "/";
      }, 1500);
    } else {
      // If this page was hit with an OAuth code, forward it to the backend
      const code = searchParams.get("code");
      if (code) {
        // Redirect to backend callback with the code
        window.location.href = `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/auth/callback?code=${encodeURIComponent(code)}`;
      } else {
        setStatus("error");
      }
    }
  }, [searchParams]);

  return (
    <div className="min-h-screen flex items-center justify-center">
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        className="text-center space-y-4"
      >
        {status === "loading" && (
          <>
            <Loader2 className="w-12 h-12 text-primary animate-spin mx-auto" />
            <p className="text-muted">Completing authentication...</p>
          </>
        )}
        {status === "success" && (
          <>
            <CheckCircle2 className="w-12 h-12 text-success mx-auto" />
            <p className="text-success font-semibold">Gmail connected!</p>
            <p className="text-sm text-muted">Redirecting to dashboard...</p>
          </>
        )}
        {status === "error" && (
          <>
            <XCircle className="w-12 h-12 text-error mx-auto" />
            <p className="text-error font-semibold">Authentication failed</p>
            <a href="/" className="text-sm text-primary hover:underline">
              Back to dashboard
            </a>
          </>
        )}
      </motion.div>
    </div>
  );
}

export default function AuthCallbackPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="w-12 h-12 text-primary animate-spin" />
      </div>
    }>
      <CallbackContent />
    </Suspense>
  );
}
