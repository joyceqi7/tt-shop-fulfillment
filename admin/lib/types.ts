/**
 * 与后端 Pydantic schemas 1:1 镜像。
 * 后端定义在 app/api/routes/admin_*.py 各文件中。
 *
 * 字段命名严格按后端 snake_case；不在前端做转换，避免到处不一致。
 */

// ============== auth ==============

export interface MeResponse {
  username: string;
  role: AdminUserRole;
  display_name: string | null;
}

// ============== users ==============

export type AdminUserRole = "admin" | "ops" | "purchase" | "finance" | "support";

export interface AdminUserRow {
  id: number;
  username: string;
  role: AdminUserRole;
  display_name: string | null;
  note: string | null;
  enabled: boolean;
  last_login_at: string | null;
  last_login_ip: string | null;
  created_at: string;
  updated_at: string;
}

export interface AdminUserListResponse {
  items: AdminUserRow[];
}

// ============== orders ==============

export interface OrderListItem {
  order_id: string;
  shop_id: string;
  shop_name: string | null;
  buyer_email: string | null;
  buyer_tt_id: string | null;
  product_name: string | null;
  tt_settlement_status: string | null;
  status: string;
  line_count: number;
  total_amount: number | null;
  currency: string | null;
  create_time_tt: number | null;
  update_time_tt: number | null;
  pending_lines: number;
  shipped_lines: number;
  failed_lines: number;
}

export interface OrderListResponse {
  items: OrderListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface OrderLineDetail {
  order_line_id: string;
  seller_sku: string | null;
  tts_product_id: string | null;
  tts_sku_id: string | null;
  quantity: number;
  unit_price: number | null;
  currency: string | null;
  status: string;
  redeemed_code_id: number | null;
  redeemed_at: string | null;
  failed_reason: string | null;
}

export interface FulfillmentLogItem {
  id: number;
  api_endpoint: string;
  api_method: string;
  http_status: number | null;
  api_code: number | null;
  api_message: string | null;
  request_id: string | null;
  latency_ms: number | null;
  error: string | null;
  created_at: string;
  order_line_id: string | null;
}

export interface OrderDetailResponse {
  order_id: string;
  shop_id: string;
  shop_name: string | null;
  status: string;
  total_amount: number | null;
  currency: string | null;
  create_time_tt: number | null;
  update_time_tt: number | null;
  sla_deadline: string | null;
  lines: OrderLineDetail[];
  fulfillment_logs: FulfillmentLogItem[];
}

export interface ReshipResponse {
  job_id: string;
  event_id: number;
  order_id: string;
  status: "queued";
}

export interface PullOrderResponse {
  order_id: string;
  shop_id: string;
  order_status: string;
  line_count: number;
  event_id: number | null;
  job_id: string | null;
  action: "upserted";
}

export interface SyncOrdersResponse {
  status: "queued";
  job_id: string;
}

// ============== inventory ==============

export interface InventorySkuRow {
  sku_id: string;
  product_name: string | null;
  face_value: number;
  threshold: number;
  inventory_tag: "normal" | "low" | "zero" | "stagnant";
  total: number;
  free: number;
  locked: number;
  used: number;
  void: number;
  expired: number;
}

export interface InventorySummaryResponse {
  rows: InventorySkuRow[];
}

export interface BatchRow {
  batch_id: string;
  sku_id: string;
  face_value: number;
  total: number;
  free: number;
  used: number;
  locked: number;
  purchase_order_id: number | null;
  po_number: string | null;
  earliest_imported: string | null;
}

export interface BatchListResponse {
  items: BatchRow[];
}

export interface CodeRow {
  id: number;
  sku_id: string;
  face_value: number;
  status: string;
  batch_id: string;
  purchase_order_id: number | null;
  order_line_id: string | null;
  locked_at: string | null;
  used_at: string | null;
  expired_at: string | null;
  created_at: string;
  operator: string | null;
}

export interface CodeListResponse {
  items: CodeRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface ImportResponse {
  batch_id: string;
  total: number;
  inserted: number;
  skipped_duplicate: number;
  purchase_order_id: number;
}

export interface InventoryLogRow {
  id: number;
  operator: string;
  change_type: "新增" | "扣减" | "回补" | "调整";
  quantity: number;
  target_type: string | null;
  target_id: string | null;
  order_id: string | null;
  note: string | null;
  created_at: string;
}

export interface InventoryLogListResponse {
  items: InventoryLogRow[];
  total: number;
  limit: number;
  offset: number;
}

// ============== skus ==============

export interface SkuRow {
  id: number;
  internal_sku_id: string;
  product_name: string | null;
  seller_sku: string | null;
  tts_product_id: string | null;
  tts_sku_id: string | null;
  face_value: number;
  safety_stock_threshold: number | null;
  list_price_cents: number | null;
  inventory_locked: boolean;
  inventory_lock_reason: string | null;
  inventory_locked_at: string | null;
  api_link_count: number;
  has_auto_fulfill: boolean;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface SkuListResponse {
  items: SkuRow[];
  total: number;
}

export interface SupplierSkuLinkRow {
  id: number;
  sku_mapping_id: number;
  internal_sku_id: string;
  face_value: number;
  supplier_id: number;
  supplier_name: string | null;
  supplier_type: "manual" | "api";
  external_sku_id: string;
  external_face_value: number | null;
  auto_fulfill: boolean;
  supplier_stock: number | null;
  supplier_stock_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface SupplierSkuLinkListResponse {
  items: SupplierSkuLinkRow[];
}

// ============== TT products ==============

export interface TTSProductSkuRow {
  id: number;
  shop_id: string;
  shop_name: string | null;
  tts_product_id: string;
  tts_sku_id: string;
  seller_sku: string | null;
  product_name: string | null;
  sku_name: string | null;
  status: string | null;
  product_status: string | null;
  currency: string | null;
  price_cents: number | null;
  inventory_quantity: number | null;
  match_status: "matched" | "unmatched" | "conflict" | null;
  match_note: string | null;
  synced_at: string;
  updated_at: string;
}

export interface TTSProductListResponse {
  items: TTSProductSkuRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface TTSProductStatsResponse {
  total_skus: number;
  matched_skus: number;
  unmatched_skus: number;
  conflict_skus: number;
  last_synced_at: string | null;
}

export interface TTSProductSyncResponse {
  shops_total: number;
  shops_synced: number;
  products_seen: number;
  skus_seen: number;
  shop_results: {
    shop_id: string;
    shop_name: string | null;
    products_seen: number;
    skus_seen: number;
    pages_seen: number;
    error: string | null;
  }[];
}

// ============== suppliers ==============

export interface SupplierRow {
  id: number;
  name: string;
  contact_person: string | null;
  contact_phone: string | null;
  default_payment_terms_days: number;
  note: string | null;
  enabled: boolean;
  supplier_type?: "manual" | "api";
  api_host: string | null;
  api_app_id: string | null;
  api_account_id: string | null;
  api_currency: string | null;
  /** @deprecated 用 supplier_type === "api" */
  is_wgcards: boolean;
  po_count: number;
  unpaid_count: number;
  created_at: string;
}

export interface SupplierListResponse {
  items: SupplierRow[];
}

// ============== supplier orders ==============

export interface SupplierOrderRow {
  id: number;
  supplier_id: number;
  supplier_name: string | null;
  procurement_mode: "realtime" | "bulk";
  status: string;
  tt_order_id: string | null;
  tt_order_line_id: string | null;
  purchase_order_id: number | null;
  po_number: string | null;
  supplier_order_id: string | null;
  service_order: string | null;
  sku_id: string;
  buy_num: number;
  face_value: number | null;
  currency: string;
  total_amount: number | null;
  card_count: number;
  error_message: string | null;
  elapsed_seconds: number | null;
  placed_at: string | null;
  card_ready_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface SupplierOrderListResponse {
  items: SupplierOrderRow[];
  total: number;
  limit: number;
  offset: number;
}

// ============== purchases ==============

export interface PurchaseRow {
  id: number;
  po_number: string;
  supplier_id: number;
  supplier_name: string | null;
  sku_id: string;
  face_value: number;
  quantity: number;
  unit_cost: number;
  total_cost: number;
  purchase_date: string;
  payment_due_date: string | null;
  amount_paid: number;
  payment_status: string;
  paid_at: string | null;
  batch_id: string | null;
  note: string | null;
  created_at: string;
}

export interface PurchaseListResponse {
  items: PurchaseRow[];
  total: number;
  limit: number;
  offset: number;
}

// ============== finance ==============

export interface FinanceOverview {
  range_days: number;
  gmv: number;
  gmv_currency: string;
  platform_commission_estimated: number;
  commission_rate: number;
  sold_code_cost: number;
  inventory_cost: number;
  gross_profit: number;
  payable: number;
  payable_overdue: number;
  legacy_codes_no_cost: number;
}

export interface MonthlyPoint {
  month: string;
  gmv: number;
  cost: number;
}

export interface FinanceMonthlyResponse {
  months: MonthlyPoint[];
}

// ============== dashboard ==============

export interface KPIs {
  gmv_today: number;
  orders_today: number;
  pending_lines: number;
  free_codes_total: number;
  used_today: number;
  failed_today: number;
  pending_user_payments: number;
}

export interface TrendPoint {
  date: string;
  gmv: number;
  orders: number;
}

export interface LowStockRow {
  sku_id: string;
  face_value: number;
  free: number;
  threshold: number;
}

export interface RecentOrder {
  order_id: string;
  shop_id: string;
  status: string;
  total_amount: number | null;
  currency: string | null;
  update_time_tt: number | null;
  failed_lines: number;
}

export interface DashboardResponse {
  kpis: KPIs;
  trend_14d: TrendPoint[];
  low_stock: LowStockRow[];
  recent_failed_orders: RecentOrder[];
  supplier_sales_30d: SupplierSalesRow[];
}

export interface SupplierSalesRow {
  supplier_id: number;
  supplier_name: string;
  sold_codes: number;
  gmv_estimated: number;
  cost: number;
  gross_profit_estimated: number;
}

// ============== audit ==============

export interface AuditRow {
  id: number;
  operator: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditListResponse {
  items: AuditRow[];
  total: number;
  limit: number;
  offset: number;
}
