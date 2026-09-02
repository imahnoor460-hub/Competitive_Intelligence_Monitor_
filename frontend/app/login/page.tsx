"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { apiFetch, setToken, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const data = await apiFetch("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setToken(data.access_token);
      router.push("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  // No credentials here, and none anywhere else in the client: the demo email
  // and password live only in the backend's environment, so this is a bare
  // POST with no body. Nothing to read in the bundle, in devtools, or in a
  // NEXT_PUBLIC_* variable.
  async function handleDemo() {
    setError(null);
    setDemoLoading(true);

    try {
      const data = await apiFetch("/auth/demo-login", { method: "POST" });
      setToken(data.access_token);
      router.push("/");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "The demo is not available right now"
      );
    } finally {
      setDemoLoading(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center bg-[var(--bg-page)] px-4">
      <form
        onSubmit={handleSubmit}
        className="flex w-full max-w-sm flex-col gap-5 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] p-7"
      >
        <div className="flex items-center gap-[11px]">
          <div className="flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded-[9px] bg-[var(--accent)]">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <circle cx="8" cy="8" r="2.4" fill="var(--accent-on)" />
              <path
                d="M8 1.2v2.2M8 12.6v2.2M1.2 8h2.2M12.6 8h2.2"
                stroke="var(--accent-on)"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
              <circle cx="8" cy="8" r="5.6" stroke="var(--accent-on)" strokeWidth="1.2" opacity=".55" />
            </svg>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="text-[13px] font-semibold tracking-tight text-[var(--text-primary)]">
              Intelligence Monitor
            </span>
            <span className="font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dim)]">
              CI Monitor v1.0
            </span>
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <h1 className="m-0 text-[20px] font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
            Log in
          </h1>
          <p className="m-0 text-[13px] text-[var(--text-muted)]">
            Sign in to see what your competitors moved on.
          </p>
        </div>

        {error && (
          <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>
        )}

        <div className="flex flex-col gap-1.5">
          <label className="font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dim)]" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-sm text-[var(--text-primary)]"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label
            className="font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dim)]"
            htmlFor="password"
          >
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-sm text-[var(--text-primary)]"
          />
        </div>

        <button
          type="submit"
          disabled={loading}
          className="h-9 w-full rounded-lg bg-[var(--accent)] text-sm font-semibold text-[var(--accent-on)] disabled:opacity-50"
        >
          {loading ? "Logging in..." : "Log in"}
        </button>

        <div className="flex items-center gap-3">
          <span className="h-px flex-1 bg-[var(--border-default)]" />
          <span className="font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dim)]">
            or
          </span>
          <span className="h-px flex-1 bg-[var(--border-default)]" />
        </div>

        <button
          type="button"
          onClick={handleDemo}
          disabled={demoLoading || loading}
          className="h-9 w-full rounded-lg border border-[var(--border-default)] bg-transparent text-sm font-semibold text-[var(--text-primary)] hover:border-[var(--accent-border)] hover:text-[var(--accent)] disabled:opacity-50"
        >
          {demoLoading ? "Opening demo..." : "Try the demo"}
        </button>

        <p className="m-0 text-sm text-[var(--text-muted)]">
          No account?{" "}
          <Link href="/register" className="font-medium text-[var(--accent)] hover:text-[var(--accent-hover)]">
            Register
          </Link>
        </p>
      </form>
    </div>
  );
}
