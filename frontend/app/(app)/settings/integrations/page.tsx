"use client";

import { useEffect, useState, useCallback, FormEvent } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { WorkspaceIntegration } from "@/lib/types";

export default function IntegrationsSettingsPage() {
  const { workspaceId, ready: contextReady, canEdit } = useWorkspaceContext();

  const [integrations, setIntegrations] = useState<WorkspaceIntegration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});

  const [webhookUrl, setWebhookUrl] = useState("");
  const [toEmail, setToEmail] = useState("");
  const [saving, setSaving] = useState<Record<string, boolean>>({});

  const load = useCallback(async (wsId: number) => {
    try {
      const items = await apiFetch(`/workspaces/${wsId}/integrations/`);
      setIntegrations(items);

      const slack = items.find((i: WorkspaceIntegration) => i.provider === "slack");
      if (slack) setWebhookUrl(slack.config.webhook_url ?? "");
      const email = items.find((i: WorkspaceIntegration) => i.provider === "email");
      if (email) setToEmail(email.config.to_email ?? "");
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (workspaceId) {
      setLoading(true);
      load(workspaceId);
    }
  }, [workspaceId, load]);

  async function handleSaveSlack(e: FormEvent) {
    e.preventDefault();
    if (!workspaceId || !webhookUrl.trim()) return;

    setSaving((prev) => ({ ...prev, slack: true }));
    setError(null);
    try {
      await apiFetch(`/workspaces/${workspaceId}/integrations/`, {
        method: "PUT",
        body: JSON.stringify({
          provider: "slack",
          config: { webhook_url: webhookUrl },
          enabled: true,
        }),
      });
      await load(workspaceId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save Slack integration");
    } finally {
      setSaving((prev) => ({ ...prev, slack: false }));
    }
  }

  async function handleSaveEmail(e: FormEvent) {
    e.preventDefault();
    if (!workspaceId || !toEmail.trim()) return;

    setSaving((prev) => ({ ...prev, email: true }));
    setError(null);
    try {
      await apiFetch(`/workspaces/${workspaceId}/integrations/`, {
        method: "PUT",
        body: JSON.stringify({
          provider: "email",
          config: { to_email: toEmail },
          enabled: true,
        }),
      });
      await load(workspaceId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save email integration");
    } finally {
      setSaving((prev) => ({ ...prev, email: false }));
    }
  }

  async function handleTestSend(provider: string) {
    if (!workspaceId) return;

    try {
      const result = await apiFetch(`/workspaces/${workspaceId}/integrations/${provider}/test-send`, {
        method: "POST",
      });
      setTestResult((prev) => ({
        ...prev,
        [provider]: result.success ? "Test message sent" : `Failed: ${result.detail}`,
      }));
    } catch (err) {
      setTestResult((prev) => ({
        ...prev,
        [provider]: err instanceof ApiError ? err.message : "Test send failed",
      }));
    }
  }

  async function handleRemove(provider: string) {
    if (!workspaceId) return;
    try {
      await apiFetch(`/workspaces/${workspaceId}/integrations/${provider}`, { method: "DELETE" });
      await load(workspaceId);
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
    }
  }

  function findIntegration(provider: string) {
    return integrations.find((i) => i.provider === provider);
  }

  if (!contextReady || loading) return null;

  if (!canEdit) {
    return (
      <div className="flex flex-col gap-[18px] px-4 py-5 pb-10 sm:px-[34px] sm:py-[30px] sm:pb-[44px]" style={{ maxWidth: 780 }}>
        <div className="flex flex-col gap-[7px]">
          <h1 className="m-0 text-[22px] font-semibold tracking-[-0.025em] sm:text-[26px]">Integrations</h1>
          <p className="m-0 max-w-[560px] text-[13.5px] text-[var(--text-muted)]">
            Deliver approved briefings to Slack and email.
          </p>
        </div>
        <p className="text-sm text-[var(--text-faint)]">
          Only workspace owners and editors can manage integrations.
        </p>
      </div>
    );
  }

  const slackIntegration = findIntegration("slack");
  const emailIntegration = findIntegration("email");

  return (
    <div className="flex flex-col gap-[18px] px-4 py-5 pb-10 sm:px-[34px] sm:py-[30px] sm:pb-[44px]" style={{ maxWidth: 780 }}>
      <div className="flex flex-col gap-[7px]">
        <h1 className="m-0 text-[22px] font-semibold tracking-[-0.025em] sm:text-[26px]">Integrations</h1>
        <p className="m-0 max-w-[560px] text-[13.5px] text-[var(--text-muted)]">
          Deliver approved briefings to Slack and email. Nothing sends until it clears the approval
          queue.
        </p>
      </div>

      {error && (
        <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>
      )}

      <div className="flex flex-col gap-4 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11px] uppercase tracking-[.13em] text-[var(--text-faint)]">
            Slack
          </span>
          <StatusPill enabled={!!slackIntegration?.enabled} />
        </div>
        <form onSubmit={handleSaveSlack} className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)]">
              Incoming webhook URL
            </label>
            <input
              value={webhookUrl}
              onChange={(e) => setWebhookUrl(e.target.value)}
              placeholder="https://hooks.slack.com/services/..."
              className="rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-xs text-[var(--text-secondary)]"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="submit"
              disabled={saving.slack}
              className="h-8 rounded-lg bg-[var(--accent)] px-3 text-xs font-semibold text-[var(--accent-on)] disabled:opacity-50"
            >
              {saving.slack ? "Saving..." : "Save"}
            </button>
            {slackIntegration && (
              <>
                <button
                  type="button"
                  onClick={() => handleTestSend("slack")}
                  className="h-8 rounded-lg border border-[var(--border-input)] bg-[var(--bg-card)] px-3 text-xs font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)]"
                >
                  Send test message
                </button>
                <button
                  type="button"
                  onClick={() => handleRemove("slack")}
                  className="h-8 rounded-lg border border-[var(--red)]/40 px-3 text-xs font-medium text-[var(--red)]"
                >
                  Remove
                </button>
              </>
            )}
          </div>
          {testResult.slack && (
            <p className="text-xs text-[var(--text-faint)]">{testResult.slack}</p>
          )}
        </form>
      </div>

      <div className="flex flex-col gap-4 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11px] uppercase tracking-[.13em] text-[var(--text-faint)]">
            Email
          </span>
          <StatusPill enabled={!!emailIntegration?.enabled} />
        </div>
        <form onSubmit={handleSaveEmail} className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)]">
              Recipient email
            </label>
            <input
              type="email"
              value={toEmail}
              onChange={(e) => setToEmail(e.target.value)}
              placeholder="team@company.com"
              className="rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-xs text-[var(--text-secondary)]"
            />
            <p className="m-0 text-[11.5px] text-[var(--text-faint)]">
              SMTP server is configured via the backend environment, not here.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="submit"
              disabled={saving.email}
              className="h-8 rounded-lg bg-[var(--accent)] px-3 text-xs font-semibold text-[var(--accent-on)] disabled:opacity-50"
            >
              {saving.email ? "Saving..." : "Save"}
            </button>
            {emailIntegration && (
              <>
                <button
                  type="button"
                  onClick={() => handleTestSend("email")}
                  className="h-8 rounded-lg border border-[var(--border-input)] bg-[var(--bg-card)] px-3 text-xs font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)]"
                >
                  Send test message
                </button>
                <button
                  type="button"
                  onClick={() => handleRemove("email")}
                  className="h-8 rounded-lg border border-[var(--red)]/40 px-3 text-xs font-medium text-[var(--red)]"
                >
                  Remove
                </button>
              </>
            )}
          </div>
          {testResult.email && (
            <p className="text-xs text-[var(--text-faint)]">{testResult.email}</p>
          )}
        </form>
      </div>
    </div>
  );
}

function StatusPill({ enabled }: { enabled: boolean }) {
  return (
    <span
      className="rounded-md px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide"
      style={{
        color: enabled ? "var(--teal)" : "var(--text-dim)",
        background: enabled ? "rgba(53,214,164,0.12)" : "var(--bg-track)",
      }}
    >
      {enabled ? "Enabled" : "Disabled"}
    </span>
  );
}
