// Admin-controlled theme (Part 11-12, expanded Step 10G). Mirrors
// backend/app/schemas/theme.py exactly — colors are strict `#RRGGBB` hex, validated
// server-side; surface_mode is 'light'|'dark'.
export interface AdvancedThemeTokens {
  bg: string;
  surface: string;
  surface_elevated: string;
  input_bg: string;
  modal_bg: string;
  text: string;
  text_secondary: string;
  text_muted: string;
  text_heading: string;
  label_text: string;
  text_disabled: string;
  placeholder: string;
  border: string;
  border_strong: string;
  primary_btn_text: string;
  secondary_btn_text: string;
  danger_btn_text: string;
  link: string;
  link_hover: string;
  focus: string;
  sidebar_bg: string;
  sidebar_text: string;
  sidebar_muted: string;
  sidebar_active_bg: string;
  sidebar_active_text: string;
  sidebar_hover_bg: string;
  table_header_bg: string;
  table_header_text: string;
  table_row_hover: string;
  badge_bg: string;
  badge_text: string;
  success_bg: string;
  warning_bg: string;
  danger_bg: string;
  info_bg: string;
  info_text: string;
}

export interface ThemeConfig {
  id: string;
  primary_color: string;
  secondary_color: string;
  accent_color: string;
  success_color: string;
  warning_color: string;
  danger_color: string;
  surface_mode: "light" | "dark";
  preset_name: string | null;
  advanced_tokens: AdvancedThemeTokens;
  updated_by_user_id: string | null;
}

export interface ThemeConfigInput {
  primary_color: string;
  secondary_color: string;
  accent_color: string;
  success_color: string;
  warning_color: string;
  danger_color: string;
  surface_mode: "light" | "dark";
  preset_name: string | null;
  advanced_tokens: AdvancedThemeTokens;
}

// Base brand colors per preset — advanced_tokens is derived from these via
// deriveDefaultAdvancedTokens() (lib/theming.ts) rather than hand-duplicated here,
// so every preset always has a complete, contrast-checked token set.
export const THEME_PRESET_BASE: Record<string, Omit<ThemeConfigInput, "advanced_tokens">> = {
  "Clinical Blue": {
    primary_color: "#2563EB",
    secondary_color: "#0EA5E9",
    accent_color: "#7C3AED",
    success_color: "#16A34A",
    warning_color: "#D97706",
    danger_color: "#DC2626",
    surface_mode: "light",
    preset_name: "Clinical Blue",
  },
  "Biomedical Teal": {
    primary_color: "#0F766E",
    secondary_color: "#14B8A6",
    accent_color: "#0EA5E9",
    success_color: "#15803D",
    warning_color: "#B45309",
    danger_color: "#B91C1C",
    surface_mode: "light",
    preset_name: "Biomedical Teal",
  },
  "Professional Navy": {
    primary_color: "#163A5F",
    secondary_color: "#3B5A80",
    accent_color: "#0F766E",
    success_color: "#147A45",
    warning_color: "#92650A",
    danger_color: "#A3231F",
    surface_mode: "light",
    preset_name: "Professional Navy",
  },
  "Clean Green": {
    primary_color: "#15803D",
    secondary_color: "#22C55E",
    accent_color: "#0D9488",
    success_color: "#16A34A",
    warning_color: "#CA8A04",
    danger_color: "#DC2626",
    surface_mode: "light",
    preset_name: "Clean Green",
  },
  "Dark Command Center": {
    primary_color: "#3B82F6",
    secondary_color: "#38BDF8",
    accent_color: "#A78BFA",
    success_color: "#4ADE80",
    warning_color: "#FBBF24",
    danger_color: "#F87171",
    surface_mode: "dark",
    preset_name: "Dark Command Center",
  },
};

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface CurrentUser {
  id: string;
  email: string;
  name: string;
  is_active: boolean;
  roles: string[];
  permissions: string[];
  hospital_scope_id: string | null;
  /** General contact number. Kept separate from `whatsapp_number`, which is the
   * WhatsApp delivery address communication_service actually sends to. */
  phone: string | null;
  whatsapp_number: string | null;
  department_id: string | null;
  notification_preference: string;
}

export interface Branch {
  id: string;
  network_id: string | null;
  code: string | null;
  name: string;
  source_name: string | null;
  region: string | null;
  state: string | null;
  city: string | null;
  address: string | null;
  // Legacy free-text fields — display fallback only, no longer the source of truth.
  unit_head: string | null;
  tl: string | null;
  // Real branch user hierarchy (final hardening pass).
  unit_head_user_id: string | null;
  branch_email: string | null;
  branch_phone: string | null;
  branch_whatsapp: string | null;
  status: string;
  inventory_mapping_status: string;
  environment: string;
  is_central_warehouse: boolean;
}

export interface BranchTL {
  id: string;
  branch_id: string;
  user_id: string;
}

export interface HospitalNetwork {
  id: string;
  code: string | null;
  name: string;
}

// Central department catalog (Physiotherapy, ICU, ...) — see DepartmentMaster's
// backend docstring. Departments (below) are branch assignments of one of these.
export interface DepartmentMaster {
  id: string;
  name: string;
  code: string | null;
  status: string;
}

export interface Department {
  id: string;
  branch_id: string;
  floor_id: string | null;
  department_master_id: string | null;
  // Kept for backward compatibility with every existing reader (Requirements/
  // Incidents/Users/Locations pages) — always synced from DepartmentMaster once
  // linked, never independently editable. See routers/hospitals.py.
  code: string | null;
  name: string;
  status: string;
}

export interface Location {
  id: string;
  department_id: string;
  code: string | null;
  name: string;
  room: string | null;
  bed: string | null;
  status: string;
}

export interface DeviceCategory {
  id: string;
  code: string | null;
  name: string;
}

export interface DeviceModel {
  id: string;
  category_id: string | null;
  name: string;
  manufacturer: string | null;
  model_number: string | null;
  category: string;
  record_type: string;
  model_status: string | null;
  firmware_version: string | null;
  criticality_default: string | null;
  known_quantity: number | null;
  notes: string | null;
  data_source: string | null;
  model_information_status: string;
  catalog_match_status: string;
  environment: string;
  is_active: boolean;
  // Device Catalog / Allocate-to-Branch unification pass — live counts computed by
  // GET /api/v1/device-models (never stored), so a catalog picker can show stock
  // levels, including zero, without a per-row round trip.
  total_units: number;
  available_units: number;
  allocated_units: number;
}

export interface Asset {
  id: string;
  asset_number: string | null;
  serial_number: string | null;
  device_model_id: string;
  branch_id: string;
  department_id: string | null;
  location_id: string | null;
  status: string;
  criticality: string | null;
  purchase_date: string | null;
  installation_date: string | null;
  operational_since: string | null;
  environment: string;
}

// Restructure consolidation pass, Part C — canonical device-category vocabulary.
// Seeded server-side (migration 0008); listed here only as the create-form suggestion
// list, matching the pattern LIFECYCLE_STATUSES already uses for Asset.status.
export const DEVICE_CATEGORIES = [
  "ICU / Critical Care",
  "Physiotherapy & Rehabilitation",
  "Respiratory Care",
  "Mobility & Patient Transport",
  "Patient Monitoring",
  "Infusion & Syringe Pumps",
  "Surgical",
  "Diagnostic",
  "Electrotherapy",
  "Oxygen Therapy",
  "Laboratory",
  "Other Biomedical Equipment",
];

// Restructure consolidation pass, Part D — composite "Add Device" payload/response.
// Composes DeviceModelIn/AssetIn server-side; no new Device/Asset table.
export interface DeviceCreateInput {
  device_model_id?: string | null;
  model_name?: string | null;
  manufacturer?: string | null;
  model_number?: string | null;
  category_id?: string | null;
  category?: string | null;
  criticality_default?: string | null;
  serial_number?: string | null;
  asset_number?: string | null;
  branch_id: string;
  department_id?: string | null;
  location_id?: string | null;
  status?: string;
  criticality?: string | null;
  purchase_date?: string | null;
  installation_date?: string | null;
  operational_since?: string | null;
}

export interface DeviceCreateResult {
  device_model: DeviceModel;
  asset: Asset;
}

// Module 18A — Asset Lifecycle, AMC/CMC & Maintenance Management
export const LIFECYCLE_STATUSES = [
  "PROCUREMENT", "RECEIVED", "INSTALLED", "OPERATIONAL", "UNDER_MAINTENANCE",
  "UNDER_REPAIR", "AMC", "CMC", "WARRANTY", "CONDEMNED", "RETIRED",
];

export interface ContractCoverage {
  contract_id: string | null;
  contract_number: string | null;
  vendor_id: string | null;
  start_date: string | null;
  end_date: string | null;
  status: string | null;
  expiry_status: string;
}

export interface AssetLifecycleSummary {
  asset_id: string;
  asset_number: string;
  serial_number: string | null;
  device_model_id: string;
  status: string;
  branch_id: string;
  department_id: string | null;
  location_id: string | null;
  criticality: string | null;
  purchase_date: string | null;
  installation_date: string | null;
  operational_since: string | null;
  warranty: ContractCoverage | null;
  amc: ContractCoverage | null;
  cmc: ContractCoverage | null;
  current_vendor_id: string | null;
  last_pm_date: string | null;
  next_pm_due_date: string | null;
  last_calibration_date: string | null;
  next_calibration_due_date: string | null;
  last_service_visit_date: string | null;
}

export interface MaintenanceHistoryEntry {
  id: string;
  source_type: string;
  asset_id: string;
  maintenance_type: string | null;
  service_date: string | null;
  engineer_id: string | null;
  vendor_id: string | null;
  performed_by_label: string | null;
  issue: string | null;
  action_taken: string | null;
  parts_used: string | null;
  downtime_hours: number | null;
  result: string | null;
  next_maintenance_date: string | null;
  remarks: string | null;
}

export interface AssetDashboardSummary {
  total_operational: number;
  under_maintenance: number;
  under_repair: number;
  amc_active: number;
  amc_expired: number;
  cmc_active: number;
  cmc_expired: number;
  warranty_active: number;
  warranty_expired: number;
  condemned: number;
  retired: number;
  upcoming_amc_expiry: number;
  upcoming_cmc_expiry: number;
  upcoming_maintenance: number;
  recent_maintenance: MaintenanceHistoryEntry[];
}

// Part B — central warehouse device stock. A physical-asset count grouped by
// DeviceModel for the is_central_warehouse Branch(es) — never a DeviceModel
// catalog count. See backend/app/services/asset_lifecycle_service.py.
export interface CentralDeviceStock {
  device_model_id: string;
  name: string;
  manufacturer: string | null;
  model_number: string | null;
  category: string | null;
  available_count: number;
}

export interface AssetProvenanceEntry {
  requirement_id: string;
  requirement_number: string;
  requirement_branch_id: string;
  requirement_status: string;
  fulfillment_id: string;
  fulfillment_number: string;
  fulfillment_status: string;
  expected_delivery_date: string | null;
  actual_delivery_date: string | null;
  delivered: boolean;
}

export interface CalendarEvent {
  event_type: string;
  date: string;
  asset_id: string | null;
  reference_id: string;
  label: string;
}

// Device QR Code Management System
export interface QrFieldDefinition {
  key: string;
  label: string;
  section: string;
  default_visible: boolean;
}

export interface QrFieldConfigItem {
  key: string;
  enabled: boolean;
  label: string;
  section: string;
  order: number;
}

export interface QrSettings {
  enabled: boolean;
  fields: QrFieldConfigItem[];
  updated_by_user_id: string | null;
}

export interface QrAssetStatus {
  asset_id: string;
  asset_number: string | null;
  serial_number: string | null;
  device_name: string;
  branch_name: string | null;
  status: string;
  has_qr: boolean;
  qr_active: boolean;
  qr_generated_at: string | null;
}

export interface QrBulkGenerateResult {
  generated: number;
  already_existing: number;
  failed: number;
  failed_asset_ids: string[];
}

export interface QrPublicField {
  key: string;
  label: string;
  section: string;
  value: string;
}

export interface QrPublicPage {
  enabled: boolean;
  fields: QrPublicField[];
}

export interface BranchDeviceAssignment {
  id: string;
  branch_id: string;
  device_model_id: string | null;
  source_name: string;
  known_quantity: number | null;
  assignment_status: string;
  inventory_mapping_status: string | null;
  asset_identification_status: string | null;
  data_quality_flag: string | null;
  notes: string | null;
  environment: string;
}

export interface Consumable {
  id: string;
  item_code: string;
  name: string;
  branch_id: string | null;
  category: string | null;
  manufacturer: string | null;
  compatible_model_number: string | null;
  unit: string | null;
  opening_quantity: number | null;
  min_level: number | null;
  reorder_level: number | null;
  expiry_date: string | null;
  supplier: string | null;
  vendor_id: string | null;
  status: string;
  environment: string;
  stock_status: string;
}

export interface ConsumableTransaction {
  id: string;
  consumable_id: string;
  transaction_type: string;
  quantity: number;
  source_branch_id: string | null;
  destination_branch_id: string | null;
  requirement_id: string | null;
  reference: string | null;
  performed_by: string | null;
  created_at: string;
}

// `branch_id === null` is the central/hub pool a consumable requirement is normally
// fulfilled from; a non-NULL `branch_id` is that branch's own on-hand quantity,
// created automatically the first time central stock is transferred to it.
export const CONSUMABLE_CATEGORIES = [
  "Physiotherapy Consumables",
  "ICU Consumables",
  "Respiratory Care Consumables",
  "Diagnostic Consumables",
  "Infection Control",
  "Infusion & IV Consumables",
  "General Biomedical Consumables",
];

export const CONSUMABLE_STOCK_STATUSES = ["OUT_OF_STOCK", "LOW_STOCK", "NORMAL"];

export interface AuditLog {
  id: string;
  user_id: string | null;
  action: string;
  entity: string;
  entity_id: string | null;
  old_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  import_job_id: string | null;
  created_at: string | null;
}

export interface ImportJob {
  id: string;
  file_name: string;
  data_type: string | null;
  status: string;
  sheet_names: string[] | null;
  column_mapping: Record<string, string | null> | null;
  summary: Record<string, unknown> | null;
  uploaded_by: string | null;
  created_at: string | null;
}

export interface ImportRow {
  id: string;
  sheet_name: string;
  row_number: number;
  mapped_data: Record<string, unknown> | null;
  business_key: string | null;
  outcome: string;
  entity_type: string | null;
  is_duplicate_candidate: boolean;
}

export interface ImportErrorEntry {
  id: string;
  row_number: number | null;
  sheet_name: string | null;
  field: string | null;
  severity: string;
  message: string;
}

export interface ImportPreview {
  job: ImportJob;
  rows: ImportRow[];
  errors: ImportErrorEntry[];
}

export interface ReviewItem {
  id: string;
  source_type: string;
  source_id: string;
  reason_code: string;
  detail: string | null;
  status: string;
  resolved_by: string | null;
  resolution_notes: string | null;
  import_job_id: string | null;
}

export interface AuditLogEntry {
  id: string;
  user_id: string | null;
  action: string;
  entity: string;
  entity_id: string | null;
  old_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  import_job_id: string | null;
  created_at: string | null;
}

export interface UserRecord {
  id: string;
  email: string;
  name: string;
  is_active: boolean;
  hospital_scope_id: string | null;
  phone: string | null;
  whatsapp_number: string | null;
  department_id: string | null;
  notification_preference: string;
  roles: string[];
}

export const NOTIFICATION_PREFERENCES = ["EMAIL", "WHATSAPP", "BOTH", "NONE"];

export interface RoleRecord {
  id: string;
  code: string;
  name: string;
  description: string | null;
  permissions: string[];
}

export interface PermissionRecord {
  id: string;
  code: string;
  description: string | null;
}

export interface IncidentCoverage {
  asset_id: string;
  coverage_status: string; // "AMC" | "CMC" | "WARRANTY" | "NOT_COVERED"
  vendor_id: string | null;
  amc: ContractCoverage | null;
  cmc: ContractCoverage | null;
  warranty: ContractCoverage | null;
}

export interface Incident {
  id: string;
  created_at: string;
  branch_id: string;
  department_id: string | null;
  location_id: string | null;
  asset_id: string | null;
  reported_by: string | null;
  problem_category: string | null;
  description: string;
  severity: string;
  clinical_impact: string | null;
  patient_care_impact: string | null;
  device_status_at_report: string | null;
  status: string;
  assigned_engineer_id: string | null;
  assigned_vendor_id: string | null;
  resolution: string | null;
  parts_used: string | null;
  downtime_hours: number | null;
  root_cause: string | null;
  corrective_action: string | null;
  preventive_action: string | null;
  closed_by: string | null;
  closed_at: string | null;
  closure_approved_by: string | null;
  // Lightweight procurement reference fields — plain display/spend-total data, not a
  // real Quotation/Indent/PurchaseOrder chain (see backend/app/models/incident.py).
  cer_number: string | null;
  indent_number: string | null;
  po_number: string | null;
  po_closure_date: string | null;
  po_cost_excl_gst: number | null;
  po_gst_percent: number | null;
  po_amount: number | null;
  vendor_estimate_cost_excl_gst: number | null;
  vendor_estimate_gst_percent: number | null;
  vendor_estimate_cost_incl_gst: number | null;
  // Only populated on the single-incident detail read (GET /incidents/{id}) when
  // asset_id is set — see routers/incidents.py.
  coverage?: IncidentCoverage | null;
  // Module 21 — Procurement status rollup, populated on the same detail read.
  procurement?: IncidentProcurementSummary | null;
}

export interface IncidentStatusHistoryEntry {
  id: string;
  incident_id: string;
  from_status: string | null;
  to_status: string;
  changed_by: string | null;
  notes: string | null;
}

export const INCIDENT_STATUSES = ["OPEN", "WORK_IN_PROGRESS", "TO_BE_CONDEMNED", "OPERATIONAL", "CANCELLED"];

export const INCIDENT_SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];

export interface PMSchedule {
  id: string;
  asset_id: string;
  frequency: string | null;
  custom_interval_days: number | null;
  next_due_date: string | null;
  is_active: boolean;
  due_status: string;
}

export interface PMRecord {
  id: string;
  schedule_id: string | null;
  asset_id: string;
  engineer_id: string | null;
  incident_id: string | null;
  performed_date: string | null;
  checklist: Record<string, unknown> | null;
  parts_replaced: string | null;
  electrical_safety_result: string | null;
  functional_check_result: string | null;
  calibration_status: string | null;
  next_due_date: string | null;
  supervisor_approved_by: string | null;
  supervisor_approved_at: string | null;
  notes: string | null;
}

export const PM_FREQUENCIES = ["MONTHLY", "QUARTERLY", "HALF_YEARLY", "YEARLY", "CUSTOM"];
export const PM_DUE_STATUSES = ["NOT_SCHEDULED", "OVERDUE", "DUE", "UPCOMING"];
export const PM_RESULT_OPTIONS = ["PASS", "FAIL", "NOT_APPLICABLE"];

export interface CalibrationSchedule {
  id: string;
  asset_id: string;
  frequency: string | null;
  custom_interval_days: number | null;
  next_due_date: string | null;
  is_active: boolean;
  due_status: string;
}

export interface CalibrationRecord {
  id: string;
  schedule_id: string | null;
  asset_id: string;
  incident_id: string | null;
  performed_date: string | null;
  certificate_number: string | null;
  calibration_agency: string | null;
  reference_standard: string | null;
  result: string | null;
  pass_fail: string | null;
  next_due_date: string | null;
  notes: string | null;
}

export const CALIBRATION_FREQUENCIES = ["MONTHLY", "QUARTERLY", "HALF_YEARLY", "YEARLY", "CUSTOM"];
export const CALIBRATION_DUE_STATUSES = ["NOT_SCHEDULED", "OVERDUE", "DUE", "UPCOMING"];
export const CALIBRATION_PASS_FAIL = ["PASS", "FAIL"];

export interface Vendor {
  id: string;
  name: string;
  vendor_type: string | null;
  is_oem: boolean;
  is_authorized_distributor: boolean;
  is_service_vendor: boolean;
  city: string | null;
  state: string | null;
  email: string | null;
  phone: string | null;
  website: string | null;
  support_coverage: string | null;
  response_sla_hours: number | null;
  rating: number | null;
  status: string;
  environment: string;
}

export interface VendorContact {
  id: string;
  vendor_id: string;
  name: string;
  role: string | null;
  email: string | null;
  phone: string | null;
  is_primary: boolean;
}

export interface DeviceModelVendor {
  id: string;
  device_model_id: string;
  vendor_id: string;
  rank: number;
  notes: string | null;
}

export interface VendorServiceVisit {
  id: string;
  incident_id: string | null;
  asset_id: string;
  engineer_id: string | null;
  visit_type: string | null;
  visit_date: string | null;
  summary: string | null;
  parts_used: string | null;
  cost: number | null;
  next_action: string | null;
}

export const VENDOR_TYPES = ["OEM", "DISTRIBUTOR", "SERVICE", "OTHER"];
export const VENDOR_STATUSES = ["ACTIVE", "INACTIVE"];

export interface Contract {
  id: string;
  contract_type: string;
  vendor_id: string | null;
  asset_ids: string[];
  contract_number: string | null;
  start_date: string | null;
  end_date: string | null;
  pm_frequency: string | null;
  calibration_frequency: string | null;
  response_time_hours: number | null;
  resolution_time_hours: number | null;
  contract_value: number | null;
  renewal_date: string | null;
  contact_person: string | null;
  contact_phone: string | null;
  contact_email: string | null;
  coverage_notes: string | null;
  status: string;
  expiry_status: string;
  environment: string;
}

export interface ContractServiceVisit {
  id: string;
  incident_id: string | null;
  asset_id: string;
  vendor_id: string | null;
  engineer_id: string | null;
  visit_type: string | null;
  visit_date: string | null;
  summary: string | null;
  parts_used: string | null;
  cost: number | null;
  next_action: string | null;
}

export const CONTRACT_TYPES = ["AMC", "CMC", "WARRANTY"];
export const CONTRACT_STATUSES = ["ACTIVE", "EXPIRED", "RENEWAL_PENDING", "CANCELLED", "UNKNOWN"];
export const CONTRACT_EXPIRY_STATUSES = ["NOT_SET", "EXPIRED", "EXPIRING_SOON", "UPCOMING", "ACTIVE"];

export interface InventoryItem {
  id: string;
  item_code: string;
  name: string;
  category: string | null;
  unit: string | null;
  min_level: number | null;
  max_level: number | null;
  reorder_level: number | null;
  unit_cost: number | null;
  supplier: string | null;
  is_consumable: boolean;
  current_stock: number;
  stock_status: string;
}

export interface InventoryItemStock {
  item_id: string;
  current_stock: number;
  stock_status: string;
  min_level: number | null;
  max_level: number | null;
  reorder_level: number | null;
}

export interface InventoryTransaction {
  id: string;
  item_id: string;
  location_id: string | null;
  transaction_type: string;
  quantity: number;
  batch_number: string | null;
  expiry_date: string | null;
  expiry_status: string;
  reference: string | null;
  performed_by: string | null;
  created_at: string;
}

export interface SparePartCompatibility {
  id: string;
  inventory_item_id: string;
  device_model_id: string;
  notes: string | null;
}

export const INVENTORY_TRANSACTION_TYPES = ["STOCK_IN", "STOCK_OUT", "ADJUSTMENT", "TRANSFER"];
export const INVENTORY_STOCK_STATUSES = ["OUT_OF_STOCK", "LOW_STOCK", "NORMAL", "OVERSTOCK"];
export const INVENTORY_EXPIRY_STATUSES = ["NOT_SET", "EXPIRED", "NEAR_EXPIRY", "OK"];

export interface Document {
  id: string;
  title: string;
  document_type: string;
  manufacturer: string | null;
  model_number: string | null;
  device_model_id: string | null;
  asset_id: string | null;
  vendor_id: string | null;
  active_version_id: string | null;
  effective_date: string | null;
  expiry_date: string | null;
  uploaded_by: string | null;
  access_level: string;
  approval_status: string;
  approved_by: string | null;
  is_active: boolean;
  version_count: number;
}

export interface DocumentVersion {
  id: string;
  document_id: string;
  version_label: string;
  file_name: string;
  effective_date: string | null;
  processing_status: string;
}

export interface DocumentLink {
  id: string;
  document_id: string;
  entity_type: string;
  entity_id: string;
}

export const DOCUMENT_TYPES = [
  "OEM_MANUAL", "SERVICE_MANUAL", "USER_MANUAL", "SOP", "PM_CHECKLIST",
  "CALIBRATION_CERTIFICATE", "AMC_CONTRACT", "CMC_CONTRACT", "WARRANTY_DOCUMENT",
  "SERVICE_REPORT", "PURCHASE_DOCUMENT", "INVOICE", "TRAINING_DOCUMENT",
  "DEVICE_PHOTOGRAPH", "INCIDENT_PHOTOGRAPH",
];
export const DOCUMENT_ACCESS_LEVELS = ["PUBLIC", "INTERNAL", "RESTRICTED"];
export const DOCUMENT_APPROVAL_STATUSES = ["PENDING", "APPROVED", "REJECTED"];
export const DOCUMENT_ENTITY_TYPES = [
  "incident", "asset", "pm_schedule", "pm_record", "calibration_schedule",
  "calibration_record", "vendor", "amc_contract", "cmc_contract", "warranty",
  "inventory_item",
  // Final hardening pass — extends linking to the Module 16A/17A/21 workflow entities.
  "requirement", "quotation", "indent", "purchase_order", "invoice", "service_visit",
];

export interface AssetHistory {
  id: string;
  asset_id: string;
  event_type: string;
  from_value: string | null;
  to_value: string | null;
  branch_id: string | null;
  department_id: string | null;
  location_id: string | null;
  changed_by: string | null;
  notes: string | null;
  created_at: string | null;
}

export const ASSET_HISTORY_EVENT_TYPES = [
  "STATUS_CHANGE", "LOCATION_CHANGE", "DEPARTMENT_CHANGE", "BRANCH_TRANSFER",
  "OWNERSHIP_CHANGE", "LIFECYCLE_TRANSITION",
];

export interface EscalationPolicy {
  id: string;
  name: string;
  trigger_type: string;
  severity_threshold: string | null;
  device_criticality_threshold: string | null;
  is_active: boolean;
}

export interface EscalationLevel {
  id: string;
  policy_id: string;
  level_number: number;
  role_code: string | null;
  wait_minutes: number | null;
}

export interface EscalationEvent {
  id: string;
  incident_id: string;
  policy_id: string;
  level_number: number;
  notified_user_id: string | null;
  notes: string | null;
}

export const ESCALATION_TRIGGER_TYPES = [
  "TIME_BASED", "SEVERITY_BASED", "CLINICAL_RISK", "REPEATED_FAILURE", "UNRESOLVED",
];

export interface NotificationTemplate {
  id: string;
  code: string;
  channel: string;
  event: string;
  subject_template: string | null;
  body_template: string;
  is_active: boolean;
  sender_name: string | null;
  sender_email: string | null;
  sender_whatsapp_number: string | null;
  receiver_name: string | null;
  receiver_email: string | null;
  receiver_whatsapp_number: string | null;
  /** Per-rule opt-in: deliver this rule's WhatsApp notifications immediately through
   * the existing WhatsApp Web automation instead of manual approve + attempt-send.
   * Defaults false, so existing rules keep the approval flow. */
  auto_send: boolean;
}

export interface NotificationTemplateDeleteResult {
  deleted: boolean;
  warning: string | null;
}

export interface NotificationRecord {
  id: string;
  template_id: string | null;
  recipient: string;
  channel: string;
  event: string;
  related_entity_type: string | null;
  related_entity_id: string | null;
  status: string;
  approval_required: boolean;
  approved_by: string | null;
  sent_at: string | null;
  failure_reason: string | null;
  related_requirement_id: string | null;
  related_fulfillment_id: string | null;
  rendered_subject: string | null;
  rendered_body: string | null;
  whatsapp_url: string | null;
  whatsapp_status: string | null;
  whatsapp_opened_at: string | null;
  whatsapp_confirmed_by: string | null;
  whatsapp_provider: string | null;
}

export interface CommunicationProviderConfig {
  channel: string;
  active_provider: string;
  is_configured: boolean;
}

// Module 19 Enhancement — WhatsApp Web browser-automation session.
export interface WhatsAppAutomationStatus {
  state: "DISCONNECTED" | "CONNECTING" | "AWAITING_QR_SCAN" | "CONNECTED" | "ERROR";
  qr_code_base64: string | null;
  connected_at: string | null;
  last_error: string | null;
  last_checked_at: string | null;
}

export interface WhatsAppTestMessageResult {
  state: string;
  detail: string;
  evidence: string | null;
}

export interface NotificationEvent {
  code: string;
  label: string;
}

export interface NotificationTemplatePreviewInput {
  channel: string;
  event: string;
  subject_template?: string | null;
  body_template: string;
  sender_name?: string | null;
  sender_email?: string | null;
  sender_whatsapp_number?: string | null;
  receiver_name?: string | null;
  receiver_email?: string | null;
  receiver_whatsapp_number?: string | null;
  requirement_id?: string | null;
  fulfillment_id?: string | null;
}

export interface NotificationTemplatePreview {
  rendered_subject: string | null;
  rendered_body: string;
  rendered_sender_name: string | null;
  rendered_sender_email: string | null;
  rendered_sender_whatsapp_number: string | null;
  rendered_receiver_name: string | null;
  rendered_receiver_email: string | null;
  rendered_receiver_whatsapp_number: string | null;
  used_sample_data: boolean;
}

export const NOTIFICATION_CHANNELS = ["IN_APP", "EMAIL", "WHATSAPP", "SMS"];
export const NOTIFICATION_STATUSES = ["DRAFT", "PENDING_APPROVAL", "APPROVED", "SENT", "FAILED"];
export const COMMUNICATION_CHANNELS = ["EMAIL", "WHATSAPP"];
// The supported event list/labels used to be hard-coded here (and were already stale —
// missing Incident/Procurement events). Superseded by GET /api/v1/notifications/events
// (see hooks/useNotificationEvents.ts), which reflects the backend's actual
// ALL_NOTIFICATION_EVENTS/EVENT_LABELS (schemas/notification.py) — a single source of
// truth instead of a second, driftable list.
export const WHATSAPP_STATUSES = ["QUEUED", "OPENED", "REQUIRES_USER_CONFIRMATION", "SENT", "FAILED"];
export const TEMPLATE_VARIABLES = [
  "requirement_number", "hospital", "branch", "department", "priority", "items",
  "status", "expected_delivery_date", "fulfillment_number", "receiver_name",
  "requester_name", "requester_email", "manager_name", "manager_email",
  "inventory_manager_name", "inventory_manager_email", "reason", "required_by_date",
];

/** Variables available to the contract (AMC/CMC/Warranty) events — mirrors
 * backend `schemas/notification.py` CONTRACT_TEMPLATE_VARIABLES. Kept as its own list
 * because a token only resolves for the event family that populates it: putting
 * `{{contract_end}}` in a requirement rule would render the literal text, not a date. */
export const CONTRACT_TEMPLATE_VARIABLES = [
  "contract_number", "contract_type", "contract_name", "status", "event",
  "device_name", "model_name", "asset_number", "asset_count",
  "branch_name", "department_name",
  "vendor_name", "vendor_email", "vendor_phone",
  "contract_start", "contract_end", "expiry_days", "updated_by",
];

/** Contract events, so the rule editor can offer the right variable list for the
 * selected event instead of one undifferentiated set. */
export const CONTRACT_EVENT_CODES = [
  "CONTRACT_CREATED", "AMC_RENEWED", "CMC_RENEWED", "WARRANTY_RENEWED",
  "CONTRACT_EXPIRING", "CONTRACT_EXPIRED", "CONTRACT_ASSET_ADDED",
  "CONTRACT_ASSET_REMOVED", "CONTRACT_STATUS_CHANGED",
];

/** Variables available to the physical asset / device lifecycle events — mirrors
 * backend `schemas/notification.py` ASSET_TEMPLATE_VARIABLES. Separate list for the
 * same reason as the contract one: a token only resolves for the event family whose
 * context builder populates it. */
export const ASSET_TEMPLATE_VARIABLES = [
  "event", "asset_id", "asset_number", "serial_number",
  "device_name", "model_name", "device_model", "manufacturer", "category",
  "status", "previous_status", "new_status", "criticality",
  "branch_name", "department_name", "location_name",
  "previous_branch", "new_branch", "previous_department", "new_department",
  "previous_location", "new_location",
  "vendor_name", "vendor_email", "vendor_phone",
  "updated_by", "updated_at", "reference", "notes",
  "branch_email", "branch_phone", "branch_whatsapp",
  "unit_head_name", "unit_head_email", "unit_head_phone",
  "bme_name", "bme_email", "bme_phone",
  "inventory_manager_name", "inventory_manager_email", "inventory_manager_phone",
  "previous_branch_email", "previous_branch_whatsapp",
  "previous_unit_head_email", "previous_unit_head_phone",
];

/** Physical asset / device lifecycle events, so the rule editor offers the asset
 * variable list for them instead of the requirement one. */
export const ASSET_EVENT_CODES = [
  "ASSET_CREATED", "ASSET_ALLOCATED", "ASSET_TRANSFERRED", "ASSET_STATUS_CHANGED",
  "ASSET_CONDEMNED", "ASSET_RETIRED", "ASSET_LOCATION_CHANGED",
];

/** The variable list the rule editor should show for one event code. */
export function templateVariablesForEvent(eventCode: string): string[] {
  if (CONTRACT_EVENT_CODES.includes(eventCode)) return CONTRACT_TEMPLATE_VARIABLES;
  if (ASSET_EVENT_CODES.includes(eventCode)) return ASSET_TEMPLATE_VARIABLES;
  return TEMPLATE_VARIABLES;
}

export interface BusinessImpactAssumption {
  id: string;
  name: string;
  branch_id: string | null;
  device_category: string | null;
  revenue_per_hour: number | null;
  currency: string;
  notes: string | null;
  is_active: boolean;
}

export interface IncidentImpactEstimate {
  incident_id: string;
  asset_id: string | null;
  downtime_hours: number | null;
  assumption_id: string | null;
  revenue_per_hour: number | null;
  currency: string | null;
  estimated_impact: number | null;
  basis: string;
}

export interface FailureSummary {
  asset_id: string;
  incident_count: number;
  total_downtime_hours: number;
  mttr_hours: number | null;
  mtbf_hours: number | null;
  repeat_failure: boolean;
  basis: string;
}

export interface InventoryLocation {
  id: string;
  branch_id: string;
  code: string | null;
  name: string;
  kind: string;
}

export const INVENTORY_LOCATION_KINDS = ["WAREHOUSE", "BIN", "STORE", "OTHER"];

export interface ServiceVisit {
  id: string;
  incident_id: string | null;
  asset_id: string;
  vendor_id: string | null;
  engineer_id: string | null;
  visit_type: string | null;
  visit_date: string | null;
  summary: string | null;
  parts_used: string | null;
  cost: number | null;
  next_action: string | null;
  issue: string | null;
  downtime_hours: number | null;
  // Module 21 — Procurement. Set when this visit is the vendor visit scheduled
  // against a Purchase Order.
  purchase_order_id: string | null;
}

// Batch E, Part 20 — historical/display fallback only, matching backend
// schemas/service_visit.py's VISIT_TYPES comment. The authoritative, admin-managed
// list is now fetched from GET /api/v1/service-visits/types (see ServiceVisitType
// below) — this constant is unused by the visit-type <select>s themselves anymore.
export const SERVICE_VISIT_TYPES = ["PM", "CALIBRATION", "BREAKDOWN", "INSTALLATION", "OTHER"];

export interface ServiceVisitType {
  id: string;
  name: string;
  is_active: boolean;
}

export interface RequirementItem {
  id: string;
  requirement_id: string;
  item_type: string;
  device_model_id: string | null;
  inventory_item_id: string | null;
  consumable_id: string | null;
  asset_id: string | null;
  description: string | null;
  requested_quantity: number;
  unit: string | null;
  remarks: string | null;
}

export interface RequirementItemInput {
  item_type: string;
  device_model_id?: string | null;
  inventory_item_id?: string | null;
  consumable_id?: string | null;
  asset_id?: string | null;
  description?: string | null;
  requested_quantity: number;
  unit?: string | null;
  remarks?: string | null;
}

export interface Requirement {
  id: string;
  request_number: string;
  branch_id: string;
  department_id: string | null;
  requester_id: string;
  requester_role: string | null;
  priority: string;
  required_by_date: string | null;
  reason: string;
  status: string;
  manager_id: string | null;
  manager_decision: string | null;
  manager_decision_at: string | null;
  manager_remarks: string | null;
  created_at: string;
  updated_at: string;
  items: RequirementItem[];
}

export interface RequirementCreateInput {
  branch_id: string;
  department_id?: string | null;
  priority: string;
  required_by_date?: string | null;
  reason: string;
  items: RequirementItemInput[];
}

export interface RequirementUpdateInput {
  branch_id?: string;
  department_id?: string | null;
  priority?: string;
  required_by_date?: string | null;
  reason?: string;
  items?: RequirementItemInput[];
}

export interface RequirementDecisionInput {
  remarks?: string | null;
}

export interface RequirementStatusHistoryEntry {
  id: string;
  requirement_id: string;
  from_status: string | null;
  to_status: string;
  changed_by: string | null;
  notes: string | null;
  created_at: string;
}

export const REQUIREMENT_STATUSES = [
  "DRAFT", "SUBMITTED", "PENDING_MANAGER_APPROVAL", "APPROVED", "REJECTED", "CANCELLED",
];
export const REQUIREMENT_STATUS_LABELS: Record<string, string> = {
  DRAFT: "Draft",
  SUBMITTED: "Submitted",
  PENDING_MANAGER_APPROVAL: "Pending Manager Approval",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  CANCELLED: "Cancelled",
};
export const REQUIREMENT_PRIORITIES = ["LOW", "MEDIUM", "HIGH", "URGENT"];
// Requirements > New Requirement > Requested Items fix — "DEVICE" and "DEVICE_MODEL"
// both referenced the same device_model_id catalog picker, showing the user two
// identical-looking options ("Devices" and "Device Models") for one workflow.
// DEVICE_MODEL is the canonical, tested value (used throughout the backend test
// suite); "DEVICE" is kept out of the selectable list but still accepted wherever an
// item is read back (see RequirementItemsTable.tsx's isDevice check) so any
// historical row using it still displays and edits correctly.
export const REQUIREMENT_ITEM_TYPES = ["DEVICE_MODEL", "CONSUMABLE", "SPARE_PART", "OTHER", "CONDEMNATION"];
export const REQUIREMENT_ITEM_TYPE_LABELS: Record<string, string> = {
  DEVICE_MODEL: "Device",
  CONSUMABLE: "Consumable",
  SPARE_PART: "Spare part",
  OTHER: "Other",
  CONDEMNATION: "Condemnation",
};
export const REQUIREMENT_ITEM_TYPE_REFERENCE_FIELD: Record<string, string | null> = {
  DEVICE: "device_model_id",
  DEVICE_MODEL: "device_model_id",
  CONSUMABLE: "consumable_id",
  SPARE_PART: "inventory_item_id",
  OTHER: null,
  CONDEMNATION: "asset_id",
};

export interface FulfillmentItemAsset {
  id: string;
  fulfillment_item_id: string;
  asset_id: string;
  delivered: boolean;
}

export interface FulfillmentItem {
  id: string;
  fulfillment_id: string;
  requirement_item_id: string;
  item_type: string;
  device_model_id: string | null;
  inventory_item_id: string | null;
  consumable_id: string | null;
  description: string | null;
  requested_quantity: number;
  allocated_quantity: number;
  delivered_quantity: number;
  pending_quantity: number;
  available_quantity: number | null;
  unit: string | null;
  assets: FulfillmentItemAsset[];
}

export interface Fulfillment {
  id: string;
  fulfillment_number: string;
  requirement_id: string;
  status: string;
  source_location_id: string | null;
  inventory_manager_id: string | null;
  expected_delivery_date: string | null;
  actual_delivery_date: string | null;
  delivered_by_id: string | null;
  delivery_remarks: string | null;
  cancel_remarks: string | null;
  created_at: string;
  updated_at: string;
  items: FulfillmentItem[];
}

export interface FulfillmentAllocationItemInput {
  requirement_item_id: string;
  quantity?: number | null;
  asset_ids?: string[] | null;
}

export interface FulfillmentAllocateInput {
  source_location_id?: string | null;
  expected_delivery_date?: string | null;
  items: FulfillmentAllocationItemInput[];
}

export interface FulfillmentStatusHistoryEntry {
  id: string;
  fulfillment_id: string;
  from_status: string | null;
  to_status: string;
  changed_by: string | null;
  notes: string | null;
  created_at: string;
}

export const FULFILLMENT_STATUSES = [
  "PENDING_ALLOCATION", "ALLOCATED", "UNDER_PROCESS", "PACKED", "OUT_FOR_DELIVERY",
  "DELIVERED", "COMPLETED", "CANCELLED",
];
export const FULFILLMENT_STATUS_LABELS: Record<string, string> = {
  PENDING_ALLOCATION: "Pending Allocation",
  ALLOCATED: "Allocated",
  UNDER_PROCESS: "Under Process",
  PACKED: "Packed",
  OUT_FOR_DELIVERY: "Out For Delivery",
  DELIVERED: "Delivered",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
};
export const FULFILLMENT_CANCELLABLE_STATUSES = ["PENDING_ALLOCATION", "ALLOCATED", "UNDER_PROCESS", "PACKED"];
export const FULFILLMENT_SERIALIZED_ITEM_TYPES = ["DEVICE", "DEVICE_MODEL"];

export interface SystemHealth {
  api: string;
  database: string;
  database_detail: string | null;
  migration_status: string;
  missing_tables: string[];
  ai: string;
  docker_required: boolean;
}

// ---- Module 21 — Procurement ----

export interface QuotationLine {
  id: string;
  quotation_id: string;
  description: string;
  quantity: number;
  unit_price: number;
  taxes: number;
  total: number;
  covered: boolean;
}

export interface Quotation {
  id: string;
  quotation_number: string;
  incident_id: string;
  asset_id: string;
  vendor_id: string;
  quote_date: string | null;
  valid_until: string | null;
  status: string;
  coverage_status: string;
  coverage_note: string | null;
  created_by: string | null;
  decided_by: string | null;
  decided_at: string | null;
  rejection_reason: string | null;
  total: number;
  created_at: string;
  lines: QuotationLine[];
}

export const QUOTATION_STATUSES = ["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CANCELLED"];
export const QUOTATION_COVERAGE_STATUSES = ["NOT_COVERED", "PARTIALLY_COVERED"];

export interface Indent {
  id: string;
  indent_number: string;
  quotation_id: string;
  incident_id: string;
  asset_id: string;
  vendor_id: string;
  requester_id: string | null;
  amount: number;
  status: string;
  notes: string | null;
  created_at: string;
}

export const INDENT_STATUSES = ["DRAFT", "SUBMITTED", "COMPLETED", "CANCELLED"];

export interface PurchaseOrderLine {
  id: string;
  purchase_order_id: string;
  quotation_line_id: string | null;
  description: string;
  quantity: number;
  unit_price: number;
  total: number;
}

export interface PurchaseOrder {
  id: string;
  po_number: string;
  indent_id: string;
  quotation_id: string;
  incident_id: string;
  asset_id: string;
  vendor_id: string;
  issue_date: string | null;
  expected_vendor_date: string | null;
  total: number;
  status: string;
  created_by: string | null;
  created_at: string;
  lines: PurchaseOrderLine[];
}

export const PO_STATUSES = ["DRAFT", "ISSUED", "ACKNOWLEDGED", "IN_PROGRESS", "DELIVERED", "COMPLETED", "CANCELLED"];
export const PO_NEXT_STATUS: Record<string, string | null> = {
  DRAFT: "ISSUED",
  ISSUED: "ACKNOWLEDGED",
  ACKNOWLEDGED: "IN_PROGRESS",
  IN_PROGRESS: "DELIVERED",
  DELIVERED: "COMPLETED",
  COMPLETED: null,
};

export interface Invoice {
  id: string;
  invoice_number: string;
  purchase_order_id: string;
  vendor_id: string;
  incident_id: string;
  asset_id: string;
  invoice_date: string | null;
  amount: number;
  recorded_by: string | null;
  recorded_at: string | null;
  verification_status: string;
  verified_by: string | null;
  verified_at: string | null;
  rejection_reason: string | null;
  created_at: string;
}

export const INVOICE_VERIFICATION_STATUSES = ["PENDING", "VERIFIED", "REJECTED"];

export interface IncidentProcurementSummary {
  quotations: Quotation[];
  indents: Indent[];
  purchase_orders: PurchaseOrder[];
  invoices: Invoice[];
  financially_closed: boolean;
}

export interface AssetProcurementProvenanceEntry {
  quotation_id: string;
  quotation_number: string;
  quotation_status: string;
  coverage_status: string;
  incident_id: string;
  indent_id: string | null;
  indent_number: string | null;
  purchase_order_id: string | null;
  po_number: string | null;
  po_status: string | null;
  invoice_id: string | null;
  invoice_number: string | null;
  invoice_verification_status: string | null;
}
