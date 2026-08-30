const API_URL = process.env.NEXT_PUBLIC_API_URL || "https://site--competitive-intelligence-monitor--xsc9z5w2fjrg.code.run";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("token");
}

export function setToken(token: string) {
  localStorage.setItem("token", token);
}

export function clearToken() {
  localStorage.removeItem("token");
}

export function getActiveWorkspaceId(): number | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem("activeWorkspaceId");
  return raw ? Number(raw) : null;
}

export function setActiveWorkspaceId(id: number) {
  localStorage.setItem("activeWorkspaceId", String(id));
}

export function clearActiveWorkspaceId() {
  localStorage.removeItem("activeWorkspaceId");
}

export class ApiError extends Error {}

export async function apiFetch(path: string, options: RequestInit = {}) {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });

  if (res.status === 401) {
    clearToken();
    clearActiveWorkspaceId();
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new ApiError("Not authenticated");
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") {
        detail = data.detail;
      } else if (Array.isArray(data.detail)) {
        detail = data.detail.map((d: { msg: string }) => d.msg).join(", ");
      }
    } catch {
      // response had no JSON body
    }
    throw new ApiError(detail);
  }

  if (res.status === 204) return null;
  return res.json();
}
