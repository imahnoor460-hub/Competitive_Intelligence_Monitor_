"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import {
  apiFetch,
  getToken,
  getActiveWorkspaceId,
  setActiveWorkspaceId,
  ApiError,
} from "@/lib/api";
import { CurrentUser, WorkspaceRole, WorkspaceWithRole } from "@/lib/types";

interface WorkspaceContextValue {
  ready: boolean;
  user: CurrentUser | null;
  workspaces: WorkspaceWithRole[];
  workspace: WorkspaceWithRole | null;
  workspaceId: number | null;
  role: WorkspaceRole | undefined;
  /** Whether this workspace is the public demo — used for labelling. */
  isDemo: boolean;
  /** Whether this user may not write here. True only for the shared demo
   * account in the demo workspace; an admin curating the demo sees false.
   * This is what gates actions — see the backend's per-caller `read_only`. */
  isReadOnly: boolean;
  canEdit: boolean;
  canApprove: boolean;
  pendingApprovalsCount: number;
  switchWorkspace: (id: number) => void;
  createWorkspace: (name: string) => Promise<void>;
  refreshPendingApprovals: () => Promise<void>;
  error: string | null;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function useWorkspaceContext(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    throw new Error("useWorkspaceContext must be used within WorkspaceProvider");
  }
  return ctx;
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceWithRole[]>([]);
  const [workspaceId, setWorkspaceIdState] = useState<number | null>(null);
  const [pendingApprovalsCount, setPendingApprovalsCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const loadPendingApprovals = useCallback(async (wsId: number) => {
    try {
      const items = await apiFetch(`/workspaces/${wsId}/approvals/?status=pending`);
      setPendingApprovalsCount(items.length);
    } catch {
      // Non-fatal — the sidebar badge just stays at its last known count.
    }
  }, []);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }

    (async () => {
      try {
        const [me, list] = await Promise.all([
          apiFetch("/auth/me"),
          apiFetch("/workspaces/"),
        ]);
        setUser(me);
        setWorkspaces(list);

        const stored = getActiveWorkspaceId();
        const chosen = list.find((w: WorkspaceWithRole) => w.id === stored) ?? list[0];

        if (chosen) {
          setActiveWorkspaceId(chosen.id);
          setWorkspaceIdState(chosen.id);
          await loadPendingApprovals(chosen.id);
        }
      } catch (err) {
        if (err instanceof ApiError) setError(err.message);
      } finally {
        setReady(true);
      }
    })();
  }, [router, loadPendingApprovals]);

  function switchWorkspace(id: number) {
    setActiveWorkspaceId(id);
    setWorkspaceIdState(id);
    loadPendingApprovals(id);
  }

  async function createWorkspace(name: string) {
    const created = await apiFetch("/workspaces/", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    const list = await apiFetch("/workspaces/");
    setWorkspaces(list);
    switchWorkspace(created.id);
  }

  const workspace = workspaces.find((w) => w.id === workspaceId) ?? null;
  const role = workspace?.role;
  // Gated on read_only, not is_demo: the server computes it per caller, so the
  // demo workspace is writable for an admin curating it and read-only for the
  // shared demo session. Role alone would offer every action and have the API
  // refuse each one, since the demo account is an `owner` there. Folding it
  // into the two capability booleans hides them everywhere those are already
  // consulted. Presentation only — dependencies.require_writable_workspace is
  // what actually enforces it.
  const isDemo = workspace?.is_demo === true;
  const isReadOnly = workspace?.read_only === true;
  const canEdit = !isReadOnly && (role === "owner" || role === "editor");
  const canApprove = !isReadOnly && (role === "owner" || role === "reviewer");

  return (
    <WorkspaceContext.Provider
      value={{
        ready,
        user,
        workspaces,
        workspace,
        workspaceId,
        role,
        isDemo,
        isReadOnly,
        canEdit,
        canApprove,
        pendingApprovalsCount,
        switchWorkspace,
        createWorkspace,
        refreshPendingApprovals: () =>
          workspaceId ? loadPendingApprovals(workspaceId) : Promise.resolve(),
        error,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}
