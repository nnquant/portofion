export type Num = number | null;

export interface Me {
  id: number;
  email: string;
  name: string;
  is_superuser: boolean;
  workspaces: { slug: string; name: string; kind: string; role: Role; implicit?: boolean }[];
}
export type Role = "admin" | "member" | "viewer";

export interface WsSettings {
  periods_per_year: number;
  rf: number;
  var_level: number;
  outlier_abs: number;
  stale_days: number;
  up_color: "red" | "green";
}
export interface WsInfo {
  slug: string;
  name: string;
  kind: string;
  role: Role;
  settings: WsSettings;
}

export interface Metrics {
  n: number;
  first_date: string | null;
  last_date: string | null;
  total_ret: Num;
  ann_ret: Num;
  cagr: Num;
  ann_vol: Num;
  sharpe: Num;
  sortino: Num;
  max_dd: Num;
  max_dd_days: Num;
  longest_dd_days: Num;
  cur_dd: Num;
  cur_dd_days: Num;
  calmar: Num;
  win_rate: Num;
  profit_factor: Num;
  skew: Num;
  kurt: Num;
  var: Num;
  cvar: Num;
  best: Num;
  worst: Num;
}

export interface LibRow {
  strategy: string;
  name: string;
  kind: "strategy" | "benchmark";
  tags: string[];
  branch: string;
  is_default: boolean;
  is_head: boolean;
  head_locked: boolean;
  version: string;
  version_id: number;
  seq: number;
  n_versions: number;
  stage: Stage;
  live_since: string | null;
  code_ref: string | null;
  created_at: string;
  archived: boolean;
  m: Metrics;
  spark: number[];
  ret_1d: Num;
  ret_mtd: Num;
  ret_ytd: Num;
  ret_1y: Num;
  flags: number;
  revisions: number;
  last_report_at: string | null;
  scalars: Record<string, number>;
}
export type Stage = "research" | "paper" | "live" | "retired";

export interface Library {
  level: "branch" | "version";
  window: string;
  rows: LibRow[];
  scalar_names: string[];
  tags: string[];
  tag_colors: Record<string, string | null>;
  settings: WsSettings;
  today: string;
}

export interface VersionInfo {
  id: number;
  label: string;
  seq: number;
  stage: Stage;
  live_since: string | null;
  code_ref: string | null;
  code_repo: string | null;
  params: Record<string, unknown>;
  description: string;
  meta: Record<string, unknown>;
  created_at: string;
  archived: boolean;
  metrics: Metrics | null;
  summary: { spark: number[]; ret_ytd: Num; n_open_flags: number; n_revisions: number; last_report_at: string | null } | null;
  series: { name: string; agg: string; unit: string | null; n_points: number; first_date: string | null; last_date: string | null }[];
  scalars: Record<string, { value: number; as_of: string | null }>;
}
export interface BranchInfo {
  id: number;
  name: string;
  head_version_id: number | null;
  head_locked: boolean;
  forked_from_version_id: number | null;
  archived: boolean;
  is_default: boolean;
  versions: VersionInfo[];
}
export interface StrategyDetail {
  strategy: {
    id: number;
    slug: string;
    name: string;
    kind: string;
    description: string;
    tags: string[];
    tag_colors: Record<string, string | null>;
    periods_per_year: number | null;
    effective_periods_per_year: number;
    default_branch_id: number | null;
    archived: boolean;
  };
  branches: BranchInfo[];
  settings: WsSettings;
}

export interface Episode {
  peak: string;
  trough: string;
  recovery: string | null;
  depth: number;
  days: number;
}
export interface Monthly {
  years: number[];
  rows: (number | null)[][];
}
export interface Analytics {
  version_id: number;
  dates: string[];
  returns: Num[];
  nav: Num[];
  drawdown: Num[];
  rolling_sharpe: Num[];
  rolling_vol: Num[];
  metrics: Metrics;
  monthly: Monthly;
  top_dd: Episode[];
  relative: { n_overlap: number; beta: Num; alpha: Num; te: Num; ir: Num; corr: Num } | null;
}
export interface SeriesData {
  dates: string[];
  series: Record<string, Num[]>;
  meta: Record<string, { agg: string; unit: string | null }>;
}

export interface CompareData {
  keys: string[];
  dates: string[];
  returns: Record<string, Num[]>;
  common_start: string | null;
  common_end: string | null;
  metrics_common: Record<string, Metrics>;
  metrics_own: Record<string, Metrics>;
  corr: { keys: string[]; corr: Num[][]; n: number[][] };
  rolling_corr: Record<string, Num[]>;
  periods: Record<string, number>;
}

export interface PortfolioSpec {
  spec_version?: number;
  scheme: "fixed" | "equal" | "inverse_vol" | "risk_parity" | "reported";
  rebalance: "daily" | "weekly" | "monthly" | "quarterly" | "none";
  lookback: number;
  min_obs?: number | null;
  start: string;
  cost_bps: number;
  gross: number;
  renormalize: boolean;
  stale_days?: number | null;
  shrinkage: number;
}
export interface PfListItem {
  slug: string;
  name: string;
  description: string;
  scheme: string;
  rebalance: string;
  n_members: number;
  updated_at: string;
  status: string | null;
  stale: boolean;
  asof: string | null;
  metrics: Partial<Metrics> & { ret_ytd?: Num; ret_mtd?: Num; ret_1y?: Num };
  spark: number[];
}
export interface PfDetail {
  slug: string;
  name: string;
  description: string;
  spec: PortfolioSpec;
  spec_rev: number;
  weights_rev: number;
  n_weight_dates: number;
  parents: string[];
  members: { key: string; ref_kind: string; weight: number | null }[];
}
export interface PfMemberResult {
  key: string;
  ref_kind: string;
  label: string;
  resolved: string | null;
  weight: number | null;
  current_weight: number | null;
  contribution: number | null;
  risk_contrib: number | null;
  lagging: boolean;
  excluded: boolean;
}
export interface PfResult {
  spec: PortfolioSpec;
  start: string | null;
  asof: string | null;
  periods_per_year: number;
  dates: string[];
  returns: Num[];
  nav: Num[];
  drawdown: Num[];
  metrics: Metrics;
  trailing: { ret_1d: Num; ret_mtd: Num; ret_ytd: Num; ret_1y: Num };
  monthly: Monthly;
  top_dd: Episode[];
  members: PfMemberResult[];
  member_nav: { dates: string[]; series: Record<string, Num[]> };
  weights: { dates: string[]; series: Record<string, Num[]> };
  rebalances: string[];
  turnover: Num[];
  warnings: { code: string; [k: string]: unknown }[];
  lagging: string[];
  computed_at?: string;
  cached?: boolean;
  fingerprint?: string;
}

export interface SearchHit {
  kind: "strategy" | "benchmark" | "branch" | "version" | "portfolio";
  ref: string;
  label: string;
}
