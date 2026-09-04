"use client";

import { createContext, useContext, useCallback, useRef, useState, ReactNode } from "react";
import { useRouter } from "next/navigation";

type ToastTone = "success" | "error";

interface Toast {
  id: number;
  message: string;
  tone: ToastTone;
  href?: string;
}

interface ToastInput {
  message: string;
  tone: ToastTone;
  href?: string;
}

interface ToastContextValue {
  push: (toast: ToastInput) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within ToastProvider");
  }
  return ctx;
}

const AUTO_DISMISS_MS = 6000;

const TONE_STYLES: Record<ToastTone, { border: string; dot: string }> = {
  success: { border: "var(--teal)", dot: "var(--teal)" },
  error: { border: "var(--red)", dot: "var(--red)" },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (toast: ToastInput) => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, ...toast }]);
      setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    },
    [dismiss]
  );

  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div className="pointer-events-none fixed left-4 right-4 top-4 z-50 flex flex-col gap-2.5 sm:left-auto sm:right-5 sm:top-5 sm:max-w-[340px]">
        {toasts.map((toast) => {
          const style = TONE_STYLES[toast.tone];
          const content = (
            <div
              className="pointer-events-auto flex items-start gap-2.5 rounded-[12px] border bg-[var(--bg-card)] px-4 py-3 shadow-lg"
              style={{ borderColor: style.border }}
            >
              <span
                className="mt-[5px] h-2 w-2 flex-shrink-0 rounded-full"
                style={{ background: style.dot }}
              />
              <p className="m-0 flex-1 text-[13px] leading-[1.5] text-[var(--text-primary)]">
                {toast.message}
              </p>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  dismiss(toast.id);
                }}
                className="flex-shrink-0 text-[13px] leading-[1] text-[var(--text-faint)] hover:text-[var(--text-secondary)]"
                aria-label="Dismiss"
              >
                ✕
              </button>
            </div>
          );

          return (
            <div
              key={toast.id}
              onClick={
                toast.href
                  ? () => {
                      dismiss(toast.id);
                      router.push(toast.href!);
                    }
                  : undefined
              }
              className={toast.href ? "cursor-pointer" : undefined}
            >
              {content}
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
