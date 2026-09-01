export type WorkspaceRole = "owner" | "editor" | "reviewer";

export interface Workspace {
  id: number;
  name: string;
  slug: string;
  created_at: string;
}

export interface WorkspaceWithRole extends Workspace {
  role: WorkspaceRole;
}

export interface WorkspaceMember {
  id: number;
  user_id: number;
  email: string;
  full_name: string | null;
  role: WorkspaceRole;
  created_at: string;
}

export type SurfaceType = "pricing" | "product" | "changelog" | "blog" | "jobs" | "other";

export interface Surface {
  id: number;
  competitor_id: number;
  surface_type: SurfaceType;
  name: string | null;
  url: string;
  check_frequency: string;
  capture_visual: boolean;
  is_active: boolean;
  last_checked_at: string | null;
}

export interface BaselineFact {
  label: string;
  value: string;
}

export interface Snapshot {
  id: number;
  surface_id: number;
  text_content: string | null;
  summary: string | null;
  highlights: string[] | null;
  headline: string | null;
  facts: BaselineFact[] | null;
  created_at: string | null;
}

export interface Competitor {
  id: number;
  name: string;
  is_own_site?: boolean;
  created_at: string | null;
  surfaces_discovered?: number;
  // Only set by POST /competitors, and only when a website_url was given.
  // Page discovery runs as a background job, so the create response hands
  // back a job to poll rather than a finished count.
  discovery_job_id?: number | null;
}

export type CompetitorDiscoveryJobStatus = "queued" | "running" | "success" | "failed";

export interface CompetitorDiscoveryJob {
  id: number;
  status: CompetitorDiscoveryJobStatus;
  surfaces_discovered: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export type ChangeItemType = "price_drop" | "price_increase" | "new" | "removed" | "policy" | "other";

export interface ChangeItem {
  item: string;
  before: string | null;
  after: string;
  change_type: ChangeItemType;
  change_pct: number | null;
}

export interface ChangeLog {
  id: number;
  competitor_id: number;
  surface_id: number;
  diff: string | null;
  materiality_score: number | null;
  classification: string | null;
  rationale: string | null;
  highlights: string[] | null;
  headline: string | null;
  items: ChangeItem[] | null;
  created_at: string;
}

export interface CurrentUser {
  id: number;
  email: string;
  full_name: string;
}

export type BriefingAudience = "exec" | "sales" | "product" | "all";
export type BriefingDigestType = "urgent" | "daily" | "weekly";
export type BriefingStatus = "draft" | "pending_approval" | "approved" | "rejected" | "delivered";

export interface Briefing {
  id: number;
  workspace_id: number;
  audience: BriefingAudience;
  digest_type: BriefingDigestType;
  title: string;
  body_markdown: string;
  status: BriefingStatus;
  created_at: string;
  decided_at: string | null;
  delivered_at: string | null;
}

export type BriefingJobStatus = "queued" | "running" | "success" | "failed";

export interface BriefingJob {
  id: number;
  status: BriefingJobStatus;
  briefing_id: number | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export type ApprovalItemType = "briefing" | "battlecard_update" | "crm_note";
export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface ApprovalItem {
  id: number;
  workspace_id: number;
  item_type: ApprovalItemType;
  item_id: number;
  status: ApprovalStatus;
  requested_at: string;
  decided_by: number | null;
  decided_at: string | null;
  decision_notes: string | null;
}

export interface AuditLogEntry {
  id: number;
  actor_user_id: number | null;
  action: string;
  entity_type: string;
  entity_id: number | null;
  extra_data: Record<string, unknown> | null;
  created_at: string;
}

export interface Battlecard {
  id: number;
  workspace_id: number;
  competitor_id: number;
  title: string;
  content_markdown: string;
  version: number;
  updated_at: string | null;
}

export interface BattlecardUpdate {
  id: number;
  battlecard_id: number;
  proposed_content_markdown: string;
  change_summary: string | null;
  status: ApprovalStatus;
  created_at: string;
  decided_at: string | null;
}

export type BattlecardUpdateJobStatus = "queued" | "running" | "success" | "failed";

export interface BattlecardUpdateJob {
  id: number;
  status: BattlecardUpdateJobStatus;
  battlecard_update_id: number | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface ResponseLibraryItem {
  id: number;
  workspace_id: number;
  competitor_id: number | null;
  title: string;
  body_markdown: string;
  tags: string[] | null;
  created_at: string;
  updated_at: string | null;
}

export type IntegrationProvider = "slack" | "email" | "crm";

export interface WorkspaceIntegration {
  id: number;
  workspace_id: number;
  provider: IntegrationProvider;
  config: Record<string, string>;
  enabled: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface KeyPerson {
  name: string;
  title: string;
}

export interface CompanyProfile {
  id: number;
  competitor_id: number;
  industry: string | null;
  hq_location: string | null;
  employee_range: string | null;
  funding_stage: string | null;
  key_people: KeyPerson[] | null;
  notes_markdown: string | null;
  website_domain: string | null;
  updated_at: string | null;
}

export interface WorkspaceBudget {
  workspace_id: number;
  monthly_cap_usd: number | null;
  period_start: string;
  alert_threshold_pct: number | null;
  estimated_spend_usd: number;
  spend_by_purpose: Record<string, number>;
}

export interface OwnSite {
  competitor_id: number;
  surface_id: number;
  url: string;
  check_frequency: string;
  last_checked_at: string | null;
}

export interface TrafficSnapshot {
  id: number;
  competitor_id: number;
  domain: string;
  month: string;
  visits: number | null;
  source: string;
  fetched_at: string | null;
}

export interface CompetitorSummary {
  total_changes: number;
  material_count: number;
  avg_materiality: number | null;
  classification_counts: Record<string, number>;
  trend: { date: string; detected: number; material: number }[];
  last_change_at: string | null;
}

export interface BenchmarkComparison {
  competitor: Competitor;
  change_summary: CompetitorSummary;
  traffic: TrafficSnapshot[] | null;
}

export interface ComparisonResponse {
  competitor: Competitor;
  profile: CompanyProfile | null;
  battlecard: Battlecard | null;
  change_summary: CompetitorSummary;
  traffic: TrafficSnapshot[] | null;
  benchmark: BenchmarkComparison | null;
}

export interface SiteSummary {
  competitor_id: number;
  categories: string[];
  current_offers: string[];
  generated_at: string | null;
}

export interface CategoryPriceStats {
  category: string;
  listing_url: string | null;
  prices_found: number;
  min_price: number | null;
  max_price: number | null;
  avg_price: number | null;
  currency: string | null;
}

export type CheckRunStatus = "queued" | "running" | "success" | "failed";

export interface CheckRun {
  id: number;
  surface_id: number;
  status: CheckRunStatus;
  // Set when this run belongs to a "Run check now" sweep rather than a
  // single-surface check.
  sweep_id: number | null;
  // What the check concluded once it succeeded: baseline_captured | no_change
  // | change_detected. Null while queued or running, and on a failure.
  // `status` alone only says the run finished, which is all a worker-executed
  // check could otherwise report.
  outcome: string | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

/**
 * POST .../surfaces/{id}/check returns one shape whether the backend ran the
 * check inline or queued it for a worker (see routers/surfaces.py).
 *
 * `status` is `queued` when a worker will do the work — the descriptive
 * outcome is not known yet and arrives by polling the run. Otherwise it is
 * the finished outcome (`baseline_captured` | `no_change` | `change_detected`)
 * or `already_running`.
 */
export interface SurfaceCheckResult {
  status: string;
  check_run_id: number;
}

export type CheckSweepStatus = "queued" | "running" | "success" | "failed";

/**
 * One "check every surface in this workspace" request.
 *
 * A sweep is `success` even when some surfaces failed — `failed_count` is
 * what carries that, so "28 of 30 checked" is reportable without treating a
 * partial failure as a failed sweep. Only an all-failed sweep is `failed`.
 */
export interface CheckSweep {
  id: number;
  workspace_id: number;
  status: CheckSweepStatus;
  total: number;
  finished: number;
  failed_count: number;
  created_at: string | null;
  finished_at: string | null;
}

/** A job whose poll URL is nested under its competitor, so the id alone is
 * not enough to rebuild it. */
export interface CompetitorJobRef {
  id: number;
  competitor_id: number;
}

/**
 * Everything still in flight in the workspace, fetched once on mount so a
 * page reload can re-attach pollers to work it did not start.
 *
 * Poll state lives in React memory; without this a refresh orphaned every
 * running job — the work carried on server-side but the UI never learned it
 * had finished.
 */
export interface ActiveJobs {
  check_runs: CheckRun[];
  check_sweeps: CheckSweep[];
  briefing_job_ids: number[];
  battlecard_update_jobs: CompetitorJobRef[];
  competitor_discovery_jobs: CompetitorJobRef[];
}
