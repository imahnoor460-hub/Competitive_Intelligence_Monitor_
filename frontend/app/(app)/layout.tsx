"use client";

import { WorkspaceProvider, useWorkspaceContext } from "@/lib/workspace-context";
import { ToastProvider } from "@/components/ui/Toast";
import { BriefingJobsProvider } from "@/lib/briefing-jobs-context";
import { BattlecardJobsProvider } from "@/lib/battlecard-jobs-context";
import { CompetitorDiscoveryJobsProvider } from "@/lib/competitor-discovery-jobs-context";
import { CheckJobsProvider } from "@/lib/check-jobs-context";
import ActiveJobsRehydrator from "@/lib/active-jobs-rehydrator";
import Sidebar from "@/components/shell/Sidebar";
import Header from "@/components/shell/Header";

function Shell({ children }: { children: React.ReactNode }) {
  const { ready, error, workspaces } = useWorkspaceContext();

  if (!ready) return null;

  return (
    <div className="flex min-h-screen bg-[var(--bg-page)] text-[var(--text-primary)]">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col">
        <Header />
        <div className="flex-1">
          {error && (
            <p className="mx-[34px] mt-6 rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}
          {workspaces.length === 0 ? (
            <div className="px-[34px] py-10">
              <p className="text-sm text-[var(--text-muted)]">
                No workspace yet — create one from the sidebar to get started.
              </p>
            </div>
          ) : (
            children
          )}
        </div>
      </main>
    </div>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <WorkspaceProvider>
      <ToastProvider>
        <BriefingJobsProvider>
          <BattlecardJobsProvider>
            <CompetitorDiscoveryJobsProvider>
              <CheckJobsProvider>
                {/* Innermost, so it can reach every provider's track function
                    and re-attach pollers to jobs this tab did not start. */}
                <ActiveJobsRehydrator />
                <Shell>{children}</Shell>
              </CheckJobsProvider>
            </CompetitorDiscoveryJobsProvider>
          </BattlecardJobsProvider>
        </BriefingJobsProvider>
      </ToastProvider>
    </WorkspaceProvider>
  );
}
