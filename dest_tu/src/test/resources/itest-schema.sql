DROP TABLE IF EXISTS "dat_clr_cash_dtl" CASCADE;
DROP TABLE IF EXISTS "dat_zl_batchpayment" CASCADE;
DROP TABLE IF EXISTS "db_log" CASCADE;
DROP TABLE IF EXISTS "delete_audit" CASCADE;
DROP TABLE IF EXISTS "emp_projects" CASCADE;
DROP TABLE IF EXISTS "prm_sth_payback_accnt_date" CASCADE;
DROP TABLE IF EXISTS "tmp_batchpay_submit" CASCADE;
DROP TABLE IF EXISTS "tmp_emp_report" CASCADE;
DROP TABLE IF EXISTS "v_par_asset_acnt_info" CASCADE;

CREATE TABLE "dat_clr_cash_dtl" (
    "account_date" TIMESTAMP,
    "account_id" BIGINT,
    "account_seqno" TEXT,
    "interface_seq" TEXT,
    "match_status" TEXT,
    "operation_status" TEXT,
    "t" TEXT
);

CREATE TABLE "dat_zl_batchpayment" (
    "account_date" TIMESTAMP,
    "account_id" BIGINT,
    "account_seqno" TEXT,
    "acnt_id" BIGINT,
    "apaysum" TEXT,
    "beneaccount" TEXT,
    "interface_seq" TEXT,
    "planid" TEXT
);

CREATE TABLE "db_log" (
    "call_stack" TEXT,
    "cost_value" numeric(20,6),
    "err_stack" TEXT,
    "id" varchar(20),
    "info" TEXT,
    "log_date" varchar(20),
    "log_level" varchar(20),
    "proc_name" varchar(200),
    "sql_param" TEXT,
    "sql_txt" TEXT,
    "step_no" varchar(20),
    "time_stamp" varchar(20)
);

CREATE TABLE "delete_audit" (
    "audit_id" INTEGER,
    "batch_id" INTEGER
);

CREATE TABLE "emp_projects" (
    "emp_id" INTEGER,
    "end_date" DATE,
    "hours_per_week" NUMERIC(5,1),
    "project_id" INTEGER,
    "role" varchar(50)
);

CREATE TABLE "prm_sth_payback_accnt_date" (
    "accnt_id" BIGINT,
    "in_accnt_date" TIMESTAMP,
    "in_respond_date" TIMESTAMP,
    "t" TEXT
);

CREATE TABLE "tmp_batchpay_submit" (
    "account_date" TIMESTAMP,
    "account_id" BIGINT,
    "account_seqno" TEXT,
    "interface_seq" TEXT,
    "out_amount" NUMERIC(18,2),
    "plan_id" BIGINT,
    "rece_account" TEXT,
    "status" TEXT,
    "yyyymmdd" TEXT
);

CREATE TABLE "tmp_emp_report" (
    "base_salary" NUMERIC(18,2),
    "dept_id" INTEGER,
    "emp_id" INTEGER,
    "emp_name" varchar(100)
);

CREATE TABLE "v_par_asset_acnt_info" (
    "account_id" BIGINT,
    "asset_acnt_id" BIGINT,
    "interface_seq" TEXT,
    "match_status" TEXT,
    "operation_status" TEXT,
    "rownum" TEXT,
    "s" TEXT,
    "use_cplan" TEXT
);
