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
    stock_qty     INT DEFAULT 0,
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
    order_id      BIGINT,
    line_no       INT,
    product_name  VARCHAR(200),
    qty           INT,
    price         NUMERIC(18,2),
    line_amount   NUMERIC(18,2)
);

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
