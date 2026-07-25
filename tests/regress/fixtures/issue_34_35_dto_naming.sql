-- ============================================================
-- Regression fixture for Issue #34 (DTO/Entity) and #35 (Mapper naming)
-- ============================================================
-- #34: Tests DTO generation - procedure with many IN/OUT params
--      and %TYPE references on DDL columns.
-- #35: Tests mapper method naming - multiple SELECT/INSERT statements
--      in one procedure should NOT use just numeric suffixes.
-- ============================================================

-- DDL for Entity generation and %TYPE reference
CREATE TABLE t_issue34_order (
    order_id        BIGINT PRIMARY KEY,
    customer_name   VARCHAR(200),
    product_code    VARCHAR(50),
    quantity        INT,
    unit_price      NUMERIC(18,4),
    total_amount    NUMERIC(18,4),
    order_date      DATE,
    status          VARCHAR(20),
    created_by      VARCHAR(100),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE t_issue34_order_log (
    log_id      BIGINT PRIMARY KEY,
    order_id    BIGINT,
    action      VARCHAR(50),
    old_status  VARCHAR(20),
    new_status  VARCHAR(20),
    log_time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE OR REPLACE PACKAGE pkg_issue34_order AS

    -- Procedure with many IN/OUT params (Issue #34: should generate DTO, not flat 20+ params)
    PROCEDURE proc_create_order(
        p_customer_name IN VARCHAR,
        p_product_code  IN VARCHAR,
        p_quantity      IN INT,
        p_unit_price    IN NUMERIC,
        p_created_by    IN VARCHAR,
        p_order_id      OUT BIGINT,
        p_total_amount  OUT NUMERIC,
        p_status_msg    OUT VARCHAR
    );

    -- Procedure with multiple SELECTs (Issue #35: method names should be semantic)
    PROCEDURE proc_query_order_stats(
        p_status        IN VARCHAR,
        p_min_amount    IN NUMERIC,
        p_order_count   OUT INT,
        p_total_revenue OUT NUMERIC,
        p_avg_amount    OUT NUMERIC
    );

    -- Procedure with multiple DML ops (Issue #35: different targets need distinct names)
    PROCEDURE proc_update_order(
        p_order_id      IN BIGINT,
        p_new_status    IN VARCHAR,
        p_updated_by    IN VARCHAR,
        p_old_status    OUT VARCHAR,
        p_rows_updated  OUT INT
    );

END pkg_issue34_order;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue34_order AS

    -- Issue #34: Many IN/OUT params - should generate DTO not flat params
    PROCEDURE proc_create_order(
        p_customer_name IN VARCHAR,
        p_product_code  IN VARCHAR,
        p_quantity      IN INT,
        p_unit_price    IN NUMERIC,
        p_created_by    IN VARCHAR,
        p_order_id      OUT BIGINT,
        p_total_amount  OUT NUMERIC,
        p_status_msg    OUT VARCHAR
    ) IS
        v_order_id  t_issue34_order.order_id%TYPE;   -- %TYPE reference (needs Entity)
        v_amount    t_issue34_order.total_amount%TYPE;
    BEGIN
        -- 1st SELECT: get next order ID
        SELECT COALESCE(MAX(order_id), 0) + 1
          INTO v_order_id
          FROM t_issue34_order;

        v_amount := p_quantity * p_unit_price;

        -- 1st INSERT
        INSERT INTO t_issue34_order (
            order_id, customer_name, product_code,
            quantity, unit_price, total_amount,
            order_date, status, created_by, created_at
        ) VALUES (
            v_order_id, p_customer_name, p_product_code,
            p_quantity, p_unit_price, v_amount,
            CURRENT_DATE, 'CREATED', p_created_by, CURRENT_TIMESTAMP
        );

        -- 2nd INSERT: log
        INSERT INTO t_issue34_order_log (log_id, order_id, action, log_time)
        VALUES (v_order_id, v_order_id, 'CREATE', CURRENT_TIMESTAMP);

        p_order_id := v_order_id;
        p_total_amount := v_amount;
        p_status_msg := 'Order created successfully';

    EXCEPTION
        WHEN OTHERS THEN
            p_status_msg := 'Error: ' || SQLERRM;
            ROLLBACK;
    END;

    -- Issue #35: Multiple DMLs (SELECTs, INSERTs) - method naming should be semantic
    PROCEDURE proc_query_order_stats(
        p_status        IN VARCHAR,
        p_min_amount    IN NUMERIC,
        p_order_count   OUT INT,
        p_total_revenue OUT NUMERIC,
        p_avg_amount    OUT NUMERIC
    ) IS
    BEGIN
        -- SELECT 1: count
        SELECT COUNT(*)
          INTO p_order_count
          FROM t_issue34_order
         WHERE status = p_status;

        -- SELECT 2: total revenue (different target, different semantics)
        SELECT COALESCE(SUM(total_amount), 0)
          INTO p_total_revenue
          FROM t_issue34_order
         WHERE status = p_status;

        -- SELECT 3: average amount with filter
        SELECT COALESCE(AVG(total_amount), 0)
          INTO p_avg_amount
          FROM t_issue34_order
         WHERE status = p_status
           AND total_amount >= p_min_amount;
    END;

    -- Issue #35: Multiple DML targets - update table + insert log
    PROCEDURE proc_update_order(
        p_order_id      IN BIGINT,
        p_new_status    IN VARCHAR,
        p_updated_by    IN VARCHAR,
        p_old_status    OUT VARCHAR,
        p_rows_updated  OUT INT
    ) IS
    BEGIN
        -- SELECT for update check
        SELECT status
          INTO p_old_status
          FROM t_issue34_order
         WHERE order_id = p_order_id;

        -- UPDATE
        UPDATE t_issue34_order
           SET status = p_new_status
         WHERE order_id = p_order_id;

        -- UPDATE count: SQL%ROWCOUNT simulation
        SELECT COUNT(*)
          INTO p_rows_updated
          FROM t_issue34_order
         WHERE order_id = p_order_id
           AND status = p_new_status;

        -- INSERT log
        INSERT INTO t_issue34_order_log (log_id, order_id, action, old_status, new_status, log_time)
        VALUES (p_order_id, p_order_id, 'STATUS_CHANGE', p_old_status, p_new_status, CURRENT_TIMESTAMP);
    END;

END pkg_issue34_order;
/
