-- ============================================================
-- Core demo tables — ALL column variants merged (employees, departments, etc.)
-- Auto-consolidated from demo-project/sql/*.sql
-- Each table includes ALL column variants from ALL source files
-- ============================================================

-- Sources: gauss_delete_all_styles.sql, gauss_insert_all_styles.sql
DROP TABLE IF EXISTS emp_archive CASCADE;
CREATE TABLE emp_archive (
    archive_id      INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    emp_name        VARCHAR2(100),
    dept_id         INTEGER,
    final_salary    NUMERIC(18,2),
    archive_date    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    archive_reason  VARCHAR2(200)
);
-- Sources: gauss_complete_examples.sql, gauss_delete_all_styles.sql, gauss_function_calls.sql, gauss_insert_all_styles.sql, gauss_select_all_styles.sql, gauss_update_all_styles.sql
DROP TABLE IF EXISTS departments CASCADE;
CREATE TABLE departments (
    department_id   INTEGER,
    department_name VARCHAR2(100) NOT NULL,
    location        VARCHAR2(100),
    manager_id      INTEGER,
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time     TIMESTAMP,
    dept_id         INTEGER,
    dept_name       VARCHAR2(100),
    budget          NUMERIC(18,2),
    is_active       INTEGER DEFAULT 1,
    PRIMARY KEY (DEPARTMENT_ID, DEPT_ID)
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS performance_log CASCADE;
CREATE TABLE performance_log (
    log_id          INTEGER PRIMARY KEY,
    log_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    operation       VARCHAR2(200),
    rows_processed  INTEGER,
    elapsed_ms      NUMERIC(18,2)
);
-- Sources: gauss_insert_all_styles.sql, gauss_update_all_styles.sql
DROP TABLE IF EXISTS salary_history CASCADE;
CREATE TABLE salary_history (
    history_id      INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    old_salary      NUMERIC(18,2),
    new_salary      NUMERIC(18,2),
    change_date     TIMESTAMP,
    change_reason   VARCHAR2(200)
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS products CASCADE;
CREATE TABLE products (
    product_id      INTEGER PRIMARY KEY,
    product_name    VARCHAR2(200),
    category_id     INTEGER,
    price           NUMERIC(18,2),
    stock           INTEGER DEFAULT 0,
    status          VARCHAR2(20) DEFAULT 'ACTIVE'
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS archive_table CASCADE;
CREATE TABLE archive_table (
    id              INTEGER,
    name            VARCHAR2(200),
    amount          NUMERIC(18,2),
    status          VARCHAR2(20),
    archived_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Sources: gauss_update_select.sql
DROP TABLE IF EXISTS salary_update_log CASCADE;
CREATE TABLE salary_update_log (
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
-- Sources: gauss_select_all_styles.sql
DROP TABLE IF EXISTS result_log CASCADE;
CREATE TABLE result_log (
    log_id          INTEGER PRIMARY KEY,
    demo_name       VARCHAR2(100),
    result_desc     VARCHAR2(500),
    row_count       INTEGER,
    log_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Sources: gauss_update_select.sql
DROP TABLE IF EXISTS perf_coefficient CASCADE;
CREATE TABLE perf_coefficient (
    perf_rating     VARCHAR2(10) PRIMARY KEY,
    salary_coeff    NUMERIC(5,2),
    -- 薪资系数
    bonus_coeff     NUMERIC(5,2)          -- 奖金系数
);
-- Sources: gauss_insert_all_styles.sql
DROP TABLE IF EXISTS emp_temp_staging CASCADE;
CREATE TABLE emp_temp_staging (
    seq_no          INTEGER,
    raw_name        VARCHAR2(100),
    raw_dept        VARCHAR2(50),
    raw_salary      VARCHAR2(50),
    is_valid        INTEGER DEFAULT 1,
    parse_error     VARCHAR2(200)
);
-- Sources: gauss_update_select.sql
DROP TABLE IF EXISTS emp_salary CASCADE;
CREATE TABLE emp_salary (
    emp_id          INTEGER PRIMARY KEY,
    emp_name        VARCHAR2(100),
    dept_id         INTEGER,
    base_salary     NUMERIC(18,2),
    update_reason   VARCHAR2(200)
);
-- Sources: gauss_select_all_styles.sql
DROP TABLE IF EXISTS sales_data CASCADE;
CREATE TABLE sales_data (
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
-- Sources: gauss_delete_all_styles.sql
DROP TABLE IF EXISTS tmp_stats CASCADE;
CREATE TABLE tmp_stats (
    stat_id         INTEGER PRIMARY KEY,
    stat_name       VARCHAR2(100),
    stat_value      INTEGER,
    stat_time       TIMESTAMP
);
-- Sources: gauss_select_all_styles.sql
DROP TABLE IF EXISTS time_dim CASCADE;
CREATE TABLE time_dim (
    date_id         INTEGER PRIMARY KEY,
    full_date       DATE,
    year_num        INTEGER,
    quarter_num     INTEGER,
    month_num       INTEGER,
    day_num         INTEGER,
    weekday_name    VARCHAR2(10),
    is_holiday      INTEGER DEFAULT 0
);
-- Sources: gauss_update_select.sql
DROP TABLE IF EXISTS dept_raise_standard CASCADE;
CREATE TABLE dept_raise_standard (
    dept_id         INTEGER PRIMARY KEY,
    dept_name       VARCHAR2(100),
    base_raise_pct  NUMERIC(5,2),
    is_active       INTEGER DEFAULT 1
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS projects CASCADE;
CREATE TABLE projects (
    project_id      INTEGER PRIMARY KEY,
    project_name    VARCHAR2(200) NOT NULL,
    start_date      DATE,
    end_date        DATE,
    budget          NUMERIC(18,2),
    status          VARCHAR2(20) DEFAULT 'ACTIVE'
);
-- Sources: gauss_insert_all_styles.sql
DROP TABLE IF EXISTS dept_summary CASCADE;
CREATE TABLE dept_summary (
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
-- Sources: gauss_delete_all_styles.sql
DROP TABLE IF EXISTS emp_contacts CASCADE;
CREATE TABLE emp_contacts (
    contact_id      INTEGER PRIMARY KEY,
    emp_id          INTEGER REFERENCES employees(emp_id) ON DELETE CASCADE,
    contact_type    VARCHAR2(20),
    contact_value   VARCHAR2(100)
);
-- Sources: gauss_function_calls.sql
DROP TABLE IF EXISTS salary_log CASCADE;
CREATE TABLE salary_log (
    log_id          INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    old_val         VARCHAR2(100),
    new_val         VARCHAR2(100),
    calc_detail     VARCHAR2(500),
    log_time        TIMESTAMP
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS query_results CASCADE;
CREATE TABLE query_results (
    result_id       INTEGER PRIMARY KEY,
    query_id        INTEGER,
    priority        INTEGER DEFAULT 5,
    status          VARCHAR2(20),
    amount          NUMERIC(18,2),
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    query_params    VARCHAR2(1000)
);
-- Sources: gauss_delete_all_styles.sql, gauss_select_all_styles.sql
DROP TABLE IF EXISTS emp_projects CASCADE;
CREATE TABLE emp_projects (
    project_id      INTEGER,
    emp_id          INTEGER,
    role            VARCHAR2(50),
    hours_per_week  INTEGER,
    start_date      DATE,
    end_date        DATE
);
-- Sources: gauss_function_calls.sql
DROP TABLE IF EXISTS emp_bonus CASCADE;
CREATE TABLE emp_bonus (
    bonus_id        INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    bonus_amount    NUMERIC(18,2),
    calc_date       TIMESTAMP,
    calc_method     VARCHAR2(50)
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS order_items CASCADE;
CREATE TABLE order_items (
    item_id         INTEGER PRIMARY KEY,
    order_id        INTEGER REFERENCES orders(order_id),
    product_name    VARCHAR2(200),
    quantity        INTEGER DEFAULT 1,
    unit_price      NUMERIC(18,2) DEFAULT 0
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS employee_bonus CASCADE;
CREATE TABLE employee_bonus (
    bonus_id        INTEGER PRIMARY KEY,
    emp_id          INTEGER REFERENCES employees(employee_id),
    bonus_amount    NUMERIC(18,2) NOT NULL,
    bonus_month     INTEGER,
    bonus_year      INTEGER,
    calc_reason     VARCHAR2(500),
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time     TIMESTAMP
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS scan_log CASCADE;
CREATE TABLE scan_log (
    scan_id         INTEGER PRIMARY KEY,
    scan_time       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    table_name      VARCHAR2(100),
    record_id       INTEGER,
    record_status   VARCHAR2(50)
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS customers CASCADE;
CREATE TABLE customers (
    customer_id     INTEGER PRIMARY KEY,
    customer_name   VARCHAR2(100) NOT NULL,
    email           VARCHAR2(100),
    phone           VARCHAR2(50),
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS orders CASCADE;
CREATE TABLE orders (
    order_id        INTEGER PRIMARY KEY,
    customer_id     INTEGER REFERENCES customers(customer_id),
    order_date      DATE,
    order_status    VARCHAR2(20) DEFAULT 'PENDING',
    total_amount    NUMERIC(18,2) DEFAULT 0,
    priority        INTEGER DEFAULT 5,
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Sources: gauss_delete_all_styles.sql
DROP TABLE IF EXISTS operation_log CASCADE;
CREATE TABLE operation_log (
    log_id          INTEGER PRIMARY KEY,
    operation       VARCHAR2(50),
    table_name      VARCHAR2(50),
    record_id       VARCHAR2(50),
    old_data        VARCHAR2(4000),
    new_data        VARCHAR2(4000),
    op_time         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    op_user         VARCHAR2(50) DEFAULT CURRENT_USER
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS exception_log CASCADE;
CREATE TABLE exception_log (
    exception_id    INTEGER PRIMARY KEY,
    exception_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    record_id       INTEGER,
    exception_type  VARCHAR2(100),
    detail          VARCHAR2(500)
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS batch_log CASCADE;
CREATE TABLE batch_log (
    batch_id        INTEGER PRIMARY KEY,
    batch_type      VARCHAR2(50),
    start_time      TIMESTAMP,
    end_time        TIMESTAMP,
    status          VARCHAR2(20),
    record_count    INTEGER DEFAULT 0,
    total_amount    NUMERIC(18,2),
    message         VARCHAR2(1000)
);
-- Sources: gauss_delete_all_styles.sql, gauss_select_all_styles.sql, gauss_update_all_styles.sql, gauss_update_select.sql
DROP TABLE IF EXISTS emp_performance CASCADE;
CREATE TABLE emp_performance (
    perf_id         INTEGER,
    emp_id          INTEGER,
    perf_year       INTEGER,
    perf_quarter    INTEGER,
    perf_score      NUMERIC(5,2),
    perf_grade      VARCHAR2(10),
    eval_date       DATE,
    eval_year       INTEGER,
    perf_rating     VARCHAR2(10),
    PRIMARY KEY (PERF_ID, EMP_ID)
);
-- Sources: gauss_insert_all_styles.sql
DROP TABLE IF EXISTS emp_log CASCADE;
CREATE TABLE emp_log (
    log_id          INTEGER PRIMARY KEY,
    operation       VARCHAR2(50),
    emp_id          INTEGER,
    old_data        VARCHAR2(4000),
    new_data        VARCHAR2(4000),
    op_time         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    op_user         VARCHAR2(50) DEFAULT CURRENT_USER
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS audit_log CASCADE;
CREATE TABLE audit_log (
    log_id          INTEGER PRIMARY KEY,
    log_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    operation       VARCHAR2(100),
    sql_text        VARCHAR2(4000),
    params          VARCHAR2(1000),
    bind_params     VARCHAR2(1000),
    user_name       VARCHAR2(100) DEFAULT CURRENT_USER
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS error_log CASCADE;
CREATE TABLE error_log (
    error_id        INTEGER PRIMARY KEY,
    error_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    procedure_name  VARCHAR2(200),
    error_code      INTEGER,
    error_message   VARCHAR2(1000),
    context         VARCHAR2(500),
    sql_text        VARCHAR2(4000)
);
-- Sources: gauss_complete_examples.sql
DROP TABLE IF EXISTS bonus_limit_log CASCADE;
CREATE TABLE bonus_limit_log (
    log_id          INTEGER PRIMARY KEY,
    log_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    emp_id          INTEGER,
    limit_reason    VARCHAR2(500)
);
-- Sources: gauss_complete_examples.sql, gauss_delete_all_styles.sql, gauss_function_calls.sql, gauss_insert_all_styles.sql, gauss_package_constants.sql, gauss_select_all_styles.sql, gauss_update_all_styles.sql
DROP TABLE IF EXISTS employees CASCADE;
CREATE TABLE employees (
    employee_id     INTEGER,
    employee_name   VARCHAR2(100) NOT NULL,
    department_id   INTEGER REFERENCES departments(department_id),
    salary          NUMERIC(18,2) DEFAULT 0,
    email           VARCHAR2(100),
    hire_date       DATE DEFAULT CURRENT_DATE,
    status          VARCHAR2(20) DEFAULT 'ACTIVE',
    current_project_id INTEGER,
    update_time     TIMESTAMP,
    is_deleted      INTEGER DEFAULT 0,
    delete_time     TIMESTAMP,
    emp_id          INTEGER,
    emp_name        VARCHAR2(100) NOT NULL,
    dept_id         INTEGER,
    base_salary     NUMERIC(18,2) DEFAULT 0,
    bonus_pct       NUMERIC(5,2) DEFAULT 0.05,
    allowance       NUMERIC(18,2) DEFAULT 0,
    total_salary    NUMERIC(18,2),
    manager_id      INTEGER,
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_reason   VARCHAR2(200),
    phone           VARCHAR2(50),
    address         VARCHAR2(200),
    last_update     TIMESTAMP,
    delete_reason   VARCHAR2(200),
    PRIMARY KEY (EMP_ID, EMPLOYEE_ID)
);
-- Sources: gauss_delete_all_styles.sql
DROP TABLE IF EXISTS delete_audit CASCADE;
CREATE TABLE delete_audit (
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
