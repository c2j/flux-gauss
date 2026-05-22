-- =====================================================================
-- 缺失表 DDL（从 DML 引用推断的列定义）
-- 这些表在存储过程中被引用但没有 CREATE TABLE 定义
-- =====================================================================

-- 订单表 (referenced by: pkg_order, pkg_test_patterns, pkg_package_vars_test)
CREATE TABLE IF NOT EXISTS t_orders (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT,
    product_id    BIGINT,
    qty           INT,
    status        VARCHAR(20) DEFAULT 'CREATED',
    total_amount  NUMERIC(18,2),
    amount        NUMERIC(18,2),
    remark        VARCHAR(500),
    processed     BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMP,
    batch_no      INT,
    name          VARCHAR(200)
);

-- 产品表 (referenced by: pkg_product, pkg_inventory, pkg_order, pkg_test_patterns)
CREATE TABLE IF NOT EXISTS t_products (
    id            BIGSERIAL PRIMARY KEY,
    name          VARCHAR(200),
    price         NUMERIC(18,2),
    stock_qty     INT DEFAULT 1000,
    category      VARCHAR(100),
    supplier_id   BIGINT,
    active        BOOLEAN DEFAULT TRUE
);

-- 支付表 (referenced by: pkg_payment)
CREATE TABLE IF NOT EXISTS t_payments (
    id            BIGSERIAL PRIMARY KEY,
    order_id      BIGINT,
    amount        NUMERIC(18,2),
    method        VARCHAR(50),
    status        VARCHAR(20),
    paid_at       TIMESTAMP
);

-- 对账表 (referenced by: pkg_payment.reconcile_payments)
CREATE TABLE IF NOT EXISTS t_reconciliation (
    id            BIGSERIAL PRIMARY KEY,
    date          VARCHAR(20),
    total_amount  NUMERIC(18,2),
    total_count   INT
);

-- 报表表 (referenced by: pkg_report)
CREATE TABLE IF NOT EXISTS t_reports (
    id            BIGSERIAL PRIMARY KEY,
    type          VARCHAR(50),
    content       TEXT,
    generated_at  TIMESTAMP
);

-- 操作日志表 (referenced by: pkg_common.log_operation)
CREATE TABLE IF NOT EXISTS t_operation_log (
    id            BIGSERIAL PRIMARY KEY,
    module        VARCHAR(50),
    action        VARCHAR(50),
    target_id     BIGINT,
    created_at    TIMESTAMP
);

-- 通知表 (referenced by: pkg_common.send_notification)
CREATE TABLE IF NOT EXISTS t_notifications (
    id            BIGSERIAL PRIMARY KEY,
    channel       VARCHAR(50),
    message       TEXT,
    sent_at       TIMESTAMP
);

-- 用户表 (referenced by: pkg_cursor_patterns)
CREATE TABLE IF NOT EXISTS t_users (
    id            BIGSERIAL PRIMARY KEY,
    name          VARCHAR(100),
    status        VARCHAR(20),
    processed     INT DEFAULT 0
);

-- 账户表 (referenced by: pkg_cursor_patterns.prc_cursor_conditional)
CREATE TABLE IF NOT EXISTS t_accounts (
    id            BIGSERIAL PRIMARY KEY,
    name          VARCHAR(100),
    balance       NUMERIC(18,2),
    status        VARCHAR(20)
);

-- 审计表 (referenced by: pkg_cursor_patterns, pkg_test_patterns, pkg_type_test)
CREATE TABLE IF NOT EXISTS t_audit (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT,
    order_id      BIGINT,
    action        VARCHAR(100),
    operator      VARCHAR(50)
);

-- 库存日志表 (referenced by: pkg_inventory)
CREATE TABLE IF NOT EXISTS t_inventory_log (
    id            BIGSERIAL PRIMARY KEY,
    product_id    BIGINT,
    delta         INT,
    reason        VARCHAR(100)
);

-- 通用日志表 (referenced by: pkg_package_vars_test, pkg_test_patterns, pkg_type_test)
CREATE TABLE IF NOT EXISTS t_log (
    id            BIGINT,
    msg           TEXT
);

-- 任务表 (referenced by: pkg_test_patterns.while_loop_with_dynamic)
CREATE TABLE IF NOT EXISTS t_tasks (
    id            BIGSERIAL PRIMARY KEY,
    status        VARCHAR(20) DEFAULT 'PENDING',
    batch_no      INT
);

-- 统计表 (referenced by: pkg_cursor_patterns.prc_for_select)
CREATE TABLE IF NOT EXISTS t_stats (
    id            BIGSERIAL PRIMARY KEY,
    stat_key      VARCHAR(100),
    cnt           INT DEFAULT 0
);

-- 告警表 (referenced by: pkg_cursor_patterns.prc_cursor_conditional, pkg_package_vars_test)
CREATE TABLE IF NOT EXISTS t_alerts (
    id            BIGSERIAL PRIMARY KEY,
    acnt_id       BIGINT,
    order_id      BIGINT,
    alert_type    VARCHAR(50),
    message       VARCHAR(500)
);

-- Mapper参数测试订单表 (referenced by: pkg_mapper_param_test)
CREATE TABLE IF NOT EXISTS t_mapper_order (
    order_id      BIGSERIAL PRIMARY KEY,
    customer_id   BIGINT,
    product_id    BIGINT,
    quantity      INT,
    unit_price    NUMERIC(18,2),
    discount      NUMERIC(18,2) DEFAULT 0,
    total_amount  NUMERIC(18,2),
    order_status  VARCHAR(50),
    remark        VARCHAR(500),
    created_by    VARCHAR(100),
    updated_at    TIMESTAMP
);

-- Mapper参数测试订单明细表 (referenced by: pkg_mapper_param_test)
CREATE TABLE IF NOT EXISTS t_mapper_order_item (
    id            BIGSERIAL PRIMARY KEY,
    item_id       BIGINT,
    order_id      BIGINT,
    line_no       INT,
    product_name  VARCHAR(200),
    qty           INT,
    price         NUMERIC(18,2),
    unit_price    NUMERIC(18,2),
    line_amount   NUMERIC(18,2)
);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='t_mapper_order_item' AND column_name='item_id') THEN
    ALTER TABLE t_mapper_order_item ADD COLUMN item_id BIGINT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='t_mapper_order_item' AND column_name='unit_price') THEN
    ALTER TABLE t_mapper_order_item ADD COLUMN unit_price NUMERIC(18,2);
  END IF;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- RptBatchDownload stub tables (referenced by: PKG_RPT_BATCH_DOWNLOAD)
CREATE TABLE IF NOT EXISTS dic_all_kind (
    operation_kind VARCHAR(100),
    kind_id        VARCHAR(100),
    kind_name      VARCHAR(200)
);
CREATE TABLE IF NOT EXISTS swh_all_kind (
    operation_kind VARCHAR(100),
    kind_id        VARCHAR(100),
    kind_name      VARCHAR(200),
    remark2        VARCHAR(200)
);
CREATE TABLE IF NOT EXISTS dat_rpt_batch_info (
    seq_id        BIGINT,
    report_id     VARCHAR(100),
    report_name   VARCHAR(200),
    user_id       VARCHAR(100),
    status        VARCHAR(20),
    report_date   VARCHAR(20),
    "TIMESTAMP"   TIMESTAMP,
    content       BYTEA,
    proc_name     VARCHAR(200),
    sql_script    TEXT,
    col_name      VARCHAR(500),
    batch_type    VARCHAR(20),
    begin_date    VARCHAR(20),
    report_format VARCHAR(20)
);
CREATE SEQUENCE IF NOT EXISTS seq_rpt_batch START WITH 1 INCREMENT BY 1;
CREATE TABLE IF NOT EXISTS test_sys_dummy (dummy INT);
INSERT INTO test_sys_dummy SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM test_sys_dummy);

-- DepositAcntInfoInquiry stub views (referenced by: PKG_DEPOSIT_ACNT_INFO_INQUIRY)
-- Columns inferred from SELECT/WHERE clauses in PKG_DEPOSIT_ACNT_INFO_INQUIRY.sql
CREATE TABLE IF NOT EXISTS v_par_client_acnt_info_noflag (
    client_acnt_id    VARCHAR(200),
    sys_acnt_id       VARCHAR(200),
    fund_code         VARCHAR(200),
    accno             VARCHAR(200),
    accname           VARCHAR(200),
    accnamefund       VARCHAR(200),
    belong_bank_code  VARCHAR(200),
    coin_code         VARCHAR(200),
    zone_code         VARCHAR(200),
    brno              VARCHAR(200),
    acnt_type         VARCHAR(50),
    bank_name         VARCHAR(200),
    bank_code         VARCHAR(200),
    bank_cexc         VARCHAR(200),
    bank_bic          VARCHAR(200),
    sys_flag          VARCHAR(50),
    cnt_flag          VARCHAR(50),
    dept_code         VARCHAR(200),
    dept_type         VARCHAR(50),
    auth_area         VARCHAR(200),
    asset_type        VARCHAR(50),
    accname_eng       VARCHAR(200),
    vald_flag         VARCHAR(50),
    inure_begin_date  VARCHAR(50),
    inure_end_date    VARCHAR(50),
    parent_acnt_id    VARCHAR(200),
    sysupdatetm       VARCHAR(50),
    if_inter_bank     VARCHAR(50),
    operator          VARCHAR(200),
    client_no         VARCHAR(200),
    acnt_name         VARCHAR(200),
    acnt_status       VARCHAR(50),
    prod_type         VARCHAR(50),
    currency          VARCHAR(50),
    balance           NUMERIC(18,2)
);
CREATE TABLE IF NOT EXISTS v_acnt_check_base_rule (
    client_acnt_id    VARCHAR(200),
    asset_acnt_id     VARCHAR(200),
    rule_id           BIGINT,
    rule_name         VARCHAR(200),
    check_result      VARCHAR(50)
);
CREATE TABLE IF NOT EXISTS MV_ACCOUNT_PRIV (
    account_id        BIGINT,
    account_code      VARCHAR(50),
    privilege         VARCHAR(50),
    user_id           VARCHAR(50),
    role              VARCHAR(50)
);
CREATE TABLE IF NOT EXISTS par_fund_info (
    fund_code     VARCHAR(50),
    fund_name     VARCHAR(200)
);
CREATE TABLE IF NOT EXISTS par_sys_area (
    area_code     VARCHAR(50),
    area_name     VARCHAR(200)
);
CREATE TABLE IF NOT EXISTS par_sys_securities (
    sec_code      VARCHAR(50),
    sec_name      VARCHAR(200)
);
CREATE TABLE IF NOT EXISTS par_sys_market (
    market_code   VARCHAR(50),
    market_name   VARCHAR(200)
);
CREATE TABLE IF NOT EXISTS par_sys_acnt_info (
    acnt_no       VARCHAR(50),
    acnt_name     VARCHAR(200)
);
CREATE TABLE IF NOT EXISTS par_sys_coin (
    coin_code     VARCHAR(50),
    coin_name     VARCHAR(200)
);
CREATE TABLE IF NOT EXISTS usermessage (
    id            BIGSERIAL PRIMARY KEY,
    user_id       VARCHAR(50),
    content       TEXT,
    created_at    TIMESTAMP
);

-- Columns inferred from SELECT/INSERT/UPDATE in PKG_2008802001_MGT.sql
CREATE TABLE IF NOT EXISTS dat_clr_cash_dtl (
    account_id        VARCHAR(200),
    account_date      VARCHAR(20),
    account_seqno     VARCHAR(200),
    accnt_seqno       VARCHAR(200),
    in_amount         NUMERIC(18,4),
    out_amount        NUMERIC(18,4) DEFAULT 0,
    describe          VARCHAR(500),
    trade_code        VARCHAR(50),
    match_status      VARCHAR(50),
    respond_date      VARCHAR(50),
    interface_seq     VARCHAR(200),
    operation_status  VARCHAR(50)
);
CREATE TABLE IF NOT EXISTS dat_trustee_acnt_detail (
    interface_seq     VARCHAR(200),
    recipacc          VARCHAR(200),
    recipnam          VARCHAR(200),
    serialno          VARCHAR(200),
    busidate          VARCHAR(50),
    timestmp          VARCHAR(50),
    updtranf          VARCHAR(200),
    revtranf          VARCHAR(200),
    trxcode           VARCHAR(50),
    drcrf             VARCHAR(50),
    amount            NUMERIC(18,4),
    detailf           VARCHAR(500),
    currtype          VARCHAR(50),
    subcode           VARCHAR(50),
    euoflag           VARCHAR(50),
    accno             VARCHAR(200)
);
CREATE TABLE IF NOT EXISTS v_par_asset_acnt_info (
    asset_acnt_id     VARCHAR(50),
    accname           VARCHAR(200),
    accno             VARCHAR(50)
);

-- Oracle system view stubs (referenced by: PKG_AAS_DATACLEAR)
CREATE TABLE IF NOT EXISTS MY_TAB_PARTITIONS (
    TABLE_NAME        VARCHAR(200),
    PARTITION_NAME    VARCHAR(200),
    num_rows          BIGINT DEFAULT 0
);
CREATE TABLE IF NOT EXISTS MY_TAB_COLUMNS (
    TABLE_NAME        VARCHAR(200),
    COLUMN_NAME       VARCHAR(200),
    column_id         INT
);

CREATE TABLE IF NOT EXISTS t_summary (
    id          BIGINT,
    amount      NUMERIC(18,4),
    batch_no    INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS par_sys_plan (
    plan_id     VARCHAR(50),
    acnt_id     VARCHAR(50)
);
CREATE TABLE IF NOT EXISTS dat_zl_batchpayment (
    planid          VARCHAR(50),
    beneaccount     VARCHAR(100),
    apaysum         NUMERIC(18,4),
    apaydate        VARCHAR(20),
    send_tm         VARCHAR(30)
);
CREATE TABLE IF NOT EXISTS tmp_batchpay_submit (
    planid          VARCHAR(50),
    rece_account    VARCHAR(100),
    status          VARCHAR(20),
    inst_date       VARCHAR(20),
    pay_tm          TIMESTAMP,
    pay_amount      NUMERIC(18,4)
);
CREATE TABLE IF NOT EXISTS trade_backup (
    trade_id        BIGSERIAL PRIMARY KEY,
    account_id      BIGINT,
    amount          NUMERIC(18,4),
    trade_date      DATE,
    status          VARCHAR(20),
    fee             NUMERIC(18,4),
    batch_seq       INT,
    parent_trade_id BIGINT,
    processed_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS t_config (
    key             VARCHAR(200),
    value           NUMERIC(18,4)
);

-- =====================================================================
-- 以下表结构来源于 gauss_*_all_styles.sql / gauss_update_select.sql
-- 各 SQL 文件中有独立的 CREATE TABLE 定义，但远程数据库可能缺失
-- =====================================================================

-- 薪资表 (referenced by: gauss_update_select)
CREATE TABLE IF NOT EXISTS emp_salary (
    emp_id          INTEGER PRIMARY KEY,
    emp_name        VARCHAR2(100),
    dept_id         INTEGER,
    base_salary     NUMERIC(18,2),
    bonus_pct       NUMERIC(5,2),
    allowance       NUMERIC(18,2),
    total_salary    NUMERIC(18,2),
    last_update     TIMESTAMP,
    update_reason   VARCHAR2(200)
);

-- 部门调薪标准表 (referenced by: gauss_update_select)
CREATE TABLE IF NOT EXISTS dept_raise_standard (
    dept_id         INTEGER PRIMARY KEY,
    dept_name       VARCHAR2(100),
    base_raise_pct  NUMERIC(5,2),
    bonus_raise_pct NUMERIC(5,2),
    allowance_add   NUMERIC(18,2),
    effective_date  DATE,
    is_active       INTEGER DEFAULT 1
);

-- 薪资更新日志表 (referenced by: gauss_update_select)
CREATE TABLE IF NOT EXISTS salary_update_log (
    log_id          INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    old_base        NUMERIC(18,2),
    new_base        NUMERIC(18,2),
    old_bonus_pct   NUMERIC(5,2),
    new_bonus_pct   NUMERIC(5,2),
    old_allowance   NUMERIC(18,2),
    new_allowance   NUMERIC(18,2),
    old_total       NUMERIC(18,2),
    new_total       NUMERIC(18,2),
    update_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_by       VARCHAR2(50) DEFAULT CURRENT_USER
);

-- 员工归档表 (referenced by: gauss_delete_all_styles, gauss_insert_all_styles)
CREATE TABLE IF NOT EXISTS emp_archive (
    archive_id      INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    emp_name        VARCHAR2(100),
    dept_id         INTEGER,
    final_salary    NUMERIC(18,2),
    archive_date    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    archive_reason  VARCHAR2(200)
);

-- 绩效表 (referenced by: gauss_delete_all_styles, gauss_select_all_styles, gauss_update_all_styles, gauss_update_select)
CREATE TABLE IF NOT EXISTS emp_performance (
    perf_id         INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    perf_year       INTEGER,
    perf_quarter    INTEGER,
    perf_score      NUMERIC(5,2),
    perf_grade      VARCHAR2(10),
    eval_date       DATE,
    perf_rating     VARCHAR2(10)
);

-- 员工操作日志表 (referenced by: gauss_insert_all_styles)
CREATE TABLE IF NOT EXISTS emp_log (
    log_id          INTEGER PRIMARY KEY,
    operation       VARCHAR2(50),
    emp_id          INTEGER,
    old_data        VARCHAR2(4000),
    new_data        VARCHAR2(4000),
    op_time         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    op_user         VARCHAR2(50) DEFAULT CURRENT_USER
);

-- 员工临时暂存表 (referenced by: gauss_insert_all_styles)
CREATE TABLE IF NOT EXISTS emp_temp_staging (
    seq_no          INTEGER,
    raw_name        VARCHAR2(100),
    raw_dept        VARCHAR2(50),
    raw_salary      VARCHAR2(50),
    is_valid        INTEGER DEFAULT 1,
    parse_error     VARCHAR2(200)
);

-- 员工联系方式表 (referenced by: gauss_delete_all_styles)
CREATE TABLE IF NOT EXISTS emp_contacts (
    contact_id      INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    contact_type    VARCHAR2(20),
    contact_value   VARCHAR2(100)
);

-- 员工项目表 (referenced by: gauss_delete_all_styles, gauss_select_all_styles)
CREATE TABLE IF NOT EXISTS emp_projects (
    project_id      INTEGER,
    emp_id          INTEGER,
    role            VARCHAR2(50),
    hours_per_week  INTEGER,
    start_date      DATE,
    end_date        DATE,
    PRIMARY KEY (project_id, emp_id)
);

-- 部门汇总表 (referenced by: gauss_insert_all_styles, gauss_function_calls, pkg_type_test)
CREATE TABLE IF NOT EXISTS dept_summary (
    summary_id      INTEGER PRIMARY KEY,
    dept_id         INTEGER,
    dept_name       VARCHAR2(100),
    emp_count       INTEGER,
    total_payroll   NUMERIC(18,2),
    avg_salary      NUMERIC(18,2),
    max_salary      NUMERIC(18,2),
    min_salary      NUMERIC(18,2),
    summary_date    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 结果日志表 (referenced by: gauss_select_all_styles)
CREATE TABLE IF NOT EXISTS result_log (
    log_id          INTEGER PRIMARY KEY,
    demo_name       VARCHAR2(100),
    result_desc     VARCHAR2(500),
    row_count       INTEGER,
    log_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 销售数据表 (referenced by: gauss_select_all_styles)
CREATE TABLE IF NOT EXISTS sales_data (
    sale_id         INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    sale_date       DATE,
    region          VARCHAR2(20),
    product_a_qty   INTEGER,
    product_b_qty   INTEGER,
    product_c_qty   INTEGER,
    product_a_amt   NUMERIC(18,2),
    product_b_amt   NUMERIC(18,2),
    product_c_amt   NUMERIC(18,2)
);

-- 临时统计表 (referenced by: gauss_delete_all_styles)
CREATE TABLE IF NOT EXISTS tmp_stats (
    stat_id         INTEGER PRIMARY KEY,
    stat_name       VARCHAR2(100),
    stat_value      INTEGER,
    stat_time       TIMESTAMP
);

-- 薪资历史表 (referenced by: gauss_insert_all_styles, gauss_update_all_styles)
CREATE TABLE IF NOT EXISTS salary_history (
    history_id      INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    old_salary      NUMERIC(18,2),
    new_salary      NUMERIC(18,2),
    change_date     TIMESTAMP,
    change_reason   VARCHAR2(200)
);

-- 操作日志表 (referenced by: gauss_delete_all_styles)
CREATE TABLE IF NOT EXISTS operation_log (
    log_id          INTEGER PRIMARY KEY,
    operation       VARCHAR2(50),
    table_name      VARCHAR2(50),
    record_id       VARCHAR2(50),
    old_data        VARCHAR2(4000),
    new_data        VARCHAR2(4000),
    op_time         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    op_user         VARCHAR2(50) DEFAULT CURRENT_USER
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id          BIGSERIAL PRIMARY KEY,
    log_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    severity        VARCHAR(20),
    message         VARCHAR(4000),
    session_id      VARCHAR(100),
    operation       VARCHAR(100),
    sql_text        VARCHAR(4000),
    params          VARCHAR(1000),
    bind_params     VARCHAR(1000),
    user_name       VARCHAR(100) DEFAULT CURRENT_USER
 );

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'audit_log' AND column_name = 'severity' AND table_schema = current_schema()) THEN
    ALTER TABLE audit_log ADD COLUMN severity VARCHAR(20);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'audit_log' AND column_name = 'message' AND table_schema = current_schema()) THEN
    ALTER TABLE audit_log ADD COLUMN message VARCHAR(4000);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'audit_log' AND column_name = 'session_id' AND table_schema = current_schema()) THEN
    ALTER TABLE audit_log ADD COLUMN session_id VARCHAR(100);
  END IF;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS delete_audit (
    audit_id        INTEGER PRIMARY KEY,
    batch_id        VARCHAR2(50),
    delete_type     VARCHAR2(50),
    target_table    VARCHAR2(50),
    rows_deleted    INTEGER,
    rows_archived   INTEGER,
    criteria        VARCHAR2(500),
    start_time      TIMESTAMP,
    end_time        TIMESTAMP,
    status          VARCHAR2(20)
);

-- =====================================================================
-- employees 表补充列（各 SQL 文件定义的 employees 列不完全一致）
-- 以下 ALTER 语句按需添加缺失列
-- =====================================================================

ALTER TABLE employees ADD COLUMN allowance       NUMERIC(18,2) DEFAULT 0;
ALTER TABLE employees ADD COLUMN total_salary    NUMERIC(18,2);
ALTER TABLE employees ADD COLUMN last_update     TIMESTAMP;
ALTER TABLE employees ADD COLUMN update_reason   VARCHAR2(200);
ALTER TABLE employees ADD COLUMN create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE employees ADD COLUMN update_time     TIMESTAMP;
ALTER TABLE employees ADD COLUMN email           VARCHAR2(100);
ALTER TABLE employees ADD COLUMN phone           VARCHAR2(50);
ALTER TABLE employees ADD COLUMN address         VARCHAR2(200);
ALTER TABLE employees ADD COLUMN is_deleted      INTEGER DEFAULT 0;
ALTER TABLE employees ADD COLUMN delete_time     TIMESTAMP;
ALTER TABLE employees ADD COLUMN delete_reason   VARCHAR2(200);
ALTER TABLE employees ADD COLUMN employee_id     INTEGER;
ALTER TABLE employees ADD COLUMN employee_name   VARCHAR2(100);
ALTER TABLE employees ADD COLUMN department_id   INTEGER;
ALTER TABLE employees ADD COLUMN salary          NUMERIC(18,2) DEFAULT 0;
ALTER TABLE employees ADD COLUMN current_project_id INTEGER;
ALTER TABLE employees ADD COLUMN bonus_pct       NUMERIC(5,2);

ALTER TABLE emp_performance ADD COLUMN eval_year INTEGER;

ALTER TABLE departments ADD COLUMN department_name VARCHAR2(100);
ALTER TABLE departments ADD COLUMN create_time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE departments ADD COLUMN update_time    TIMESTAMP;
ALTER TABLE departments ADD COLUMN budget         NUMERIC(18,2);
ALTER TABLE departments ADD COLUMN is_active      INTEGER DEFAULT 1;
ALTER TABLE departments ADD COLUMN manager_id     INTEGER;
ALTER TABLE departments ADD COLUMN dept_name      VARCHAR2(100);

-- db_log table (referenced by AAS dataclear procedures)
DROP TABLE IF EXISTS db_log CASCADE;
CREATE TABLE IF NOT EXISTS db_log (
    ID          VARCHAR(30) PRIMARY KEY,
    PROC_NAME   VARCHAR(200),
    INFO        TEXT,
    LOG_LEVEL   VARCHAR(20),
    TIME_STAMP  VARCHAR(20),
    CALL_STACK  TEXT,
    ERR_STACK   TEXT,
    STEP_NO     INTEGER,
    SQL_TXT     TEXT,
    SQL_PARAM   TEXT,
    LOG_DATE    VARCHAR(20)
);

-- Column aliases for cross-file compatibility (different source files use different column names)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='employees' AND column_name='employee_id') THEN
    ALTER TABLE employees ADD COLUMN employee_id INTEGER;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='employees' AND column_name='department_id') THEN
    ALTER TABLE employees ADD COLUMN department_id INTEGER;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='employees' AND column_name='salary') THEN
    ALTER TABLE employees ADD COLUMN salary NUMERIC(18,2);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='employees' AND column_name='current_project_id') THEN
    ALTER TABLE employees ADD COLUMN current_project_id INTEGER;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='departments' AND column_name='department_id') THEN
    ALTER TABLE departments ADD COLUMN department_id INTEGER;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='orders' AND column_name='status') THEN
    ALTER TABLE orders ADD COLUMN status VARCHAR(20);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='transactions' AND column_name='dept_id') THEN
    ALTER TABLE transactions ADD COLUMN dept_id INTEGER;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='transactions' AND column_name='tx_date') THEN
    ALTER TABLE transactions ADD COLUMN tx_date DATE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='transactions' AND column_name='amount') THEN
    ALTER TABLE transactions ADD COLUMN amount NUMERIC(18,2);
  END IF;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
CREATE TABLE IF NOT EXISTS t_performance_reviews (
    id            BIGSERIAL PRIMARY KEY,
    emp_id        INT,
    review_year   INT,
    "performance" NUMERIC(18,2),
    bonus         NUMERIC(18,2),
    new_salary    NUMERIC(18,2)
);

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='transactions' AND column_name='tx_date') THEN
    ALTER TABLE transactions ADD COLUMN tx_date DATE;
  END IF;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='employees' AND column_name='perf_score') THEN
    ALTER TABLE employees ADD COLUMN perf_score NUMERIC(5,2);
  END IF;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
UPDATE employees SET perf_score = 85 WHERE perf_score IS NULL;

UPDATE employees SET employee_id = emp_id WHERE employee_id IS NULL;
UPDATE employees SET department_id = dept_id WHERE department_id IS NULL;
DO $$ BEGIN UPDATE employees SET salary = base_salary WHERE salary IS NULL; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN UPDATE departments SET department_id = dept_id WHERE department_id IS NULL; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN UPDATE orders SET status = order_status WHERE status IS NULL; EXCEPTION WHEN undefined_table THEN NULL; END $$;

DO $$ BEGIN
INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, allowance, hire_date, status)
SELECT i, 'Demo_Employee_' || i, 10, 8000 + i * 100, 0.10, 500, '2024-01-01', 'ACTIVE'
FROM generate_series(1001, 1020) AS i
WHERE NOT EXISTS (SELECT 1 FROM employees WHERE emp_id = i);
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- =====================================================================
-- proc_Five_Gotos resource tables
-- =====================================================================
CREATE TABLE IF NOT EXISTS resource_locks (
    lock_id     BIGINT PRIMARY KEY,
    task_id     BIGINT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_log (
    task_id     BIGINT,
    action      VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS quota (
    task_type       VARCHAR(50) PRIMARY KEY,
    remaining_quota INTEGER DEFAULT 10
);

DO $$ BEGIN
INSERT INTO quota (task_type, remaining_quota)
SELECT 'A', 10 WHERE NOT EXISTS (SELECT 1 FROM quota WHERE task_type = 'A');
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
