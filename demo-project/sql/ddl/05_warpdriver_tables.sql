-- ============================================================
-- WARPDRIVER stress test tables (from PKG_WARPDRIVER_STRESS_TEST-DDL.sql)
-- Auto-consolidated from demo-project/sql/*.sql
-- Each table includes ALL column variants from ALL source files
-- ============================================================

CREATE TABLE orders (
  order_id BIGINT PRIMARY KEY,
  customer_id BIGINT,
  account_id BIGINT,
  total_amount NUMERIC(18,4),
  status VARCHAR2(20),
  biz_date DATE,
  priority INT,
  version INT DEFAULT 0,
  process_flag VARCHAR2(20),
  process_time TIMESTAMP,
  submit_time TIMESTAMP,
  pay_time TIMESTAMP,
  ship_time TIMESTAMP,
  complete_time TIMESTAMP,
  cancel_time TIMESTAMP,
  refund_apply_time TIMESTAMP,
  refund_time TIMESTAMP,
  refund_reject_time TIMESTAMP,
  partial_refund_amt NUMERIC(18,4),
  retry_count INT DEFAULT 0,
  last_retry TIMESTAMP,
  create_time TIMESTAMP DEFAULT current_timestamp,
  update_time TIMESTAMP
);


CREATE TABLE order_items (
  item_id BIGINT PRIMARY KEY,
  order_id BIGINT,
  product_id BIGINT,
  required_qty INT
);


CREATE TABLE inventory (
  product_id BIGINT PRIMARY KEY,
  available_qty INT
);


CREATE TABLE accounts (
  account_id BIGINT PRIMARY KEY,
  balance NUMERIC(18,4),
  pre_amount NUMERIC(18,4),
  frozen_flag VARCHAR2(1)
);


CREATE TABLE settlement (
  settle_id BIGINT PRIMARY KEY,
  settle_date DATE,
  region_code VARCHAR2(20),
  settle_amount NUMERIC(18,4),
  fee_rate NUMERIC(5,4)
);


CREATE TABLE operation_logs (
  log_id BIGINT PRIMARY KEY,
  create_time TIMESTAMP
);


CREATE TABLE audit_trail (
  trail_id BIGSERIAL PRIMARY KEY,
  action_code VARCHAR2(50),
  detail_info TEXT,
  created_at TIMESTAMP,
  session_id BIGINT
);


CREATE TABLE distributed_locks (
  lock_key VARCHAR2(100) PRIMARY KEY,
  holder_id BIGINT,
  acquired_at TIMESTAMP
);


CREATE TABLE transaction_log (
  tx_id BIGSERIAL PRIMARY KEY,
  order_id BIGINT,
  amount NUMERIC(18,4),
  tx_type VARCHAR2(20),
  tx_time TIMESTAMP
);


CREATE TABLE task_log_master (
  log_id BIGSERIAL PRIMARY KEY
);


CREATE TABLE order_item_snapshot (
  snap_id BIGSERIAL PRIMARY KEY,
  log_id BIGINT,
  item_data JSONB,
  created_at TIMESTAMP
);


CREATE TABLE error_log (
  err_id BIGSERIAL PRIMARY KEY,
  log_id BIGINT,
  order_id BIGINT,
  err_msg VARCHAR2(200),
  created_at TIMESTAMP
);


CREATE TABLE black_list (
  customer_id BIGINT PRIMARY KEY,
  active VARCHAR2(1)
);


CREATE TABLE risk_rules (
  rule_id BIGINT PRIMARY KEY,
  rule_type VARCHAR2(20),
  threshold INT
);


CREATE TABLE customer_risk (
  customer_id BIGINT,
  rule_id BIGINT,
  risk_score INT,
  PRIMARY KEY(customer_id, rule_id)
);


CREATE TABLE bulk_orders (
  order_id BIGINT PRIMARY KEY,
  batch_id BIGINT,
  customer_id BIGINT,
  order_type VARCHAR2(20),
  process_flag VARCHAR2(20),
  process_time TIMESTAMP,
  reject_reason VARCHAR2(100)
);


CREATE TABLE state_transitions (
  trans_id BIGSERIAL PRIMARY KEY,
  order_id BIGINT,
  event VARCHAR2(50),
  from_state VARCHAR2(20),
  to_state VARCHAR2(20),
  trans_time TIMESTAMP
);


CREATE TABLE job_dispatch_log (
  dispatch_id BIGSERIAL PRIMARY KEY,
  job_name VARCHAR2(100),
  task_id BIGINT,
  dispatch_time TIMESTAMP,
  status VARCHAR2(20)
);


CREATE TABLE tree_nodes (
  node_id BIGINT PRIMARY KEY,
  parent_id BIGINT
);


CREATE TABLE tree_attributes (
  attr_id BIGSERIAL PRIMARY KEY,
  node_id BIGINT
);


CREATE TABLE account_journal (
  journal_id BIGSERIAL PRIMARY KEY,
  account_id BIGINT,
  dr_amount NUMERIC(18,4),
  cr_amount NUMERIC(18,4),
  remark VARCHAR2(200)
);


CREATE TABLE risk_events (
  event_id BIGSERIAL PRIMARY KEY,
  account_id BIGINT,
  event_type VARCHAR2(50),
  event_time TIMESTAMP
);


CREATE TABLE notifications (
  notify_id BIGSERIAL PRIMARY KEY,
  account_id BIGINT,
  notify_type VARCHAR2(20),
  content TEXT
);


CREATE TABLE payments (
  payment_id BIGINT PRIMARY KEY,
  customer_id BIGINT,
  pay_amount NUMERIC(18,4),
  pay_channel VARCHAR2(20),
  pay_time TIMESTAMP,
  pay_status VARCHAR2(20)
);


CREATE TABLE customer_credits (
  customer_id BIGINT PRIMARY KEY,
  credit_level INT
);


CREATE SEQUENCE seq_log_master START 1;

CREATE SEQUENCE lock_seq START 1;

