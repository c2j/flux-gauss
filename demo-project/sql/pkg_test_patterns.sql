CREATE OR REPLACE PROCEDURE pkg_test.for_integer_loop(p_limit INTEGER)
AS $$
DECLARE
    i INTEGER;
    v_total INTEGER := 0;
BEGIN
    FOR i IN 1..p_limit LOOP
        v_total := v_total + i * 10;
        INSERT INTO t_summary(id, amount, batch_no) VALUES(i, v_total, 1);
    END LOOP;
    UPDATE t_config SET value = v_total WHERE key = 'total';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE PROCEDURE pkg_test.for_query_loop(p_status VARCHAR)
AS $$
DECLARE
    v_rec RECORD;
    v_count INTEGER := 0;
BEGIN
    FOR v_rec IN SELECT id, name, amount FROM t_orders WHERE status = p_status ORDER BY id LOOP
        v_count := v_count + 1;
        UPDATE t_orders SET processed = true WHERE id = v_rec.id;
        INSERT INTO t_audit(order_id, action, operator) VALUES(v_rec.id, 'PROCESSED', 'system');
    END LOOP;
    INSERT INTO t_log(id, msg) VALUES(1, 'processed ' || v_count || ' orders');
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE PROCEDURE pkg_test.cursor_fetch_loop(p_category VARCHAR)
AS $$
DECLARE
    v_cursor CURSOR(p_cat VARCHAR) FOR SELECT id, name, price FROM t_products WHERE category = p_cat;
    v_id BIGINT;
    v_name VARCHAR;
    v_price NUMERIC;
    v_discount NUMERIC := 0;
BEGIN
    OPEN v_cursor(p_category);
    LOOP
        FETCH v_cursor INTO v_id, v_name, v_price;
        EXIT WHEN NOT FOUND;
        IF v_price > 1000 THEN
            v_discount := v_price * 0.1;
            UPDATE t_products SET price = price - v_discount WHERE id = v_id;
        ELSIF v_price > 500 THEN
            v_discount := v_price * 0.05;
            UPDATE t_products SET price = price - v_discount WHERE id = v_id;
        ELSE
            v_discount := 0;
        END IF;
        INSERT INTO t_price_log(product_id, old_price, discount) VALUES(v_id, v_price, v_discount);
    END LOOP;
    CLOSE v_cursor;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE PROCEDURE pkg_test.nested_if_with_calls(p_order_id BIGINT, p_action VARCHAR)
AS $$
DECLARE
    v_status VARCHAR;
    v_amount NUMERIC;
    v_formatted VARCHAR;
BEGIN
    SELECT status, total_amount INTO v_status, v_amount FROM t_orders WHERE id = p_order_id;
    IF p_action = 'APPROVE' THEN
        IF v_status = 'PENDING' THEN
            UPDATE t_orders SET status = 'APPROVED' WHERE id = p_order_id;
            PERFORM pkg_common.log_operation('ORDER', 'APPROVE', p_order_id);
            PERFORM pkg_common.send_notification('EMAIL', 'Order approved');
        ELSE
            RAISE EXCEPTION 'Cannot approve order in status: %', v_status;
        END IF;
    ELSIF p_action = 'REJECT' THEN
        IF v_status = 'PENDING' THEN
            UPDATE t_orders SET status = 'REJECTED' WHERE id = p_order_id;
            PERFORM pkg_common.log_operation('ORDER', 'REJECT', p_order_id);
        END IF;
    ELSIF p_action = 'CANCEL' THEN
        IF v_status IN ('PENDING', 'APPROVED') THEN
            v_formatted := pkg_common.format_amount(v_amount);
            UPDATE t_orders SET status = 'CANCELLED', remark = v_formatted WHERE id = p_order_id;
            PERFORM pkg_common.send_notification('SMS', 'Order cancelled');
        ELSE
            RAISE EXCEPTION 'Cannot cancel order in status: %', v_status;
        END IF;
    ELSE
        RAISE NOTICE 'Unknown action: %', p_action;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE PROCEDURE pkg_test.while_loop_with_dynamic(p_threshold INTEGER)
AS $$
DECLARE
    v_count INTEGER;
    v_batch INTEGER := 0;
BEGIN
    SELECT COUNT(*) INTO v_count FROM t_tasks WHERE status = 'PENDING';
    WHILE v_count > 0 LOOP
        v_batch := v_batch + 1;
        UPDATE t_tasks SET status = 'PROCESSING', batch_no = v_batch
        WHERE status = 'PENDING' LIMIT p_threshold;
        PERFORM pkg_common.log_operation('TASK', 'BATCH_' || v_batch, v_count);
        SELECT COUNT(*) INTO v_count FROM t_tasks WHERE status = 'PENDING';
    END LOOP;
END;
$$ LANGUAGE plpgsql;
