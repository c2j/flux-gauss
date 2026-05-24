-- ============================================================
-- Custom composite types
-- Auto-consolidated from demo-project/sql/*.sql
-- Each table includes ALL column variants from ALL source files
-- ============================================================

-- Source: pkg_type_test.sql
CREATE TYPE emp_info AS (
    emp_id      BIGINT,
    emp_name    VARCHAR(100),
    emp_salary  NUMERIC(18, 2)
);


-- Source: pkg_type_test.sql
CREATE TYPE dept_summary AS (
    dept_id     BIGINT,
    dept_name   VARCHAR(100),
    head_count  INTEGER,
    total_salary NUMERIC(18, 2)
);


-- Source: pkg_mapper_param_test.sql
CREATE OR REPLACE TYPE order_detail AS (
    customer_id  BIGINT,
    product_id   BIGINT,
    item_count   NUMERIC,
    unit_price   NUMERIC
);


