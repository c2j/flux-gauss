CREATE OR REPLACE PACKAGE BODY pkg_cursor_patterns AS

-- Case 1: FOR IN SELECT loop
PROCEDURE prc_for_select(p_status VARCHAR) IS
    v_total INTEGER := 0;
BEGIN
    FOR v_rec IN (SELECT id, name, status FROM t_users WHERE status = p_status ORDER BY id) LOOP
        v_total := v_total + 1;
        INSERT INTO t_audit(user_id, action) VALUES(v_rec.id, 'processed');
    END LOOP;
    UPDATE t_stats SET cnt = v_total WHERE stat_key = 'users_processed';
END;

-- Case 2: Explicit cursor OPEN/FETCH/CLOSE
PROCEDURE prc_cursor_walk(p_min_id INTEGER) IS
    v_cur SYS_REFCURSOR;
    v_id INTEGER;
    v_name VARCHAR;
    v_cnt INTEGER := 0;
BEGIN
    OPEN v_cur FOR SELECT id, name FROM t_users WHERE id > p_min_id ORDER BY id;
    LOOP
        FETCH v_cur INTO v_id, v_name;
        EXIT WHEN NOT FOUND;
        v_cnt := v_cnt + 1;
        UPDATE t_users SET processed = 1 WHERE id = v_id;
    END LOOP;
    CLOSE v_cur;
    INSERT INTO t_audit(user_id, action) VALUES(v_cnt, 'cursor processed');
END;

-- Case 3: Cursor with IF inside loop
PROCEDURE prc_cursor_conditional(p_status VARCHAR) IS
    v_cur SYS_REFCURSOR;
    v_id INTEGER;
    v_name VARCHAR;
    v_balance NUMERIC;
BEGIN
    OPEN v_cur FOR SELECT id, name, balance FROM t_accounts WHERE status = p_status;
    LOOP
        FETCH v_cur INTO v_id, v_name, v_balance;
        EXIT WHEN NOT FOUND;
        IF v_balance > 10000 THEN
            INSERT INTO t_alerts(acnt_id, alert_type, message) VALUES(v_id, 'HIGH_BALANCE', v_name);
        ELSIF v_balance < 0 THEN
            UPDATE t_accounts SET status = 'FROZEN' WHERE id = v_id;
        ELSE
            NULL;
        END IF;
    END LOOP;
    CLOSE v_cur;
END;

END pkg_cursor_patterns;
