CREATE OR REPLACE TABLE dat_inst_base_info_144 (
    fund_code           VARCHAR2(20),
    cap_date            VARCHAR2(8),
    maturity_date       VARCHAR2(8),
    withdrawal_date     VARCHAR2(8),
    last_withdrawal_date VARCHAR2(8),
    submit_date         VARCHAR2(8)
);

CREATE OR REPLACE PACKAGE pkg_issue144cf IS
    PROCEDURE prc_cursor_fields(p_in_fund_code IN VARCHAR2);
END pkg_issue144cf;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue144cf IS
    -- Issue #144 batch 3: named-cursor FOR-loop records. The loop variable
    -- was registered as Integer (the generic _process_for prologue), so
    -- r_date.withdrawal_date missed the multi-part ColumnRef branch and
    -- degraded to "/* TODO: withdrawalDate() */ null" — dereferencing null
    -- later ("cannot dereference <nulltype>"). The loop var must be a
    -- Map<String, Object> during body processing.
    PROCEDURE prc_cursor_fields(p_in_fund_code IN VARCHAR2) IS
        CURSOR c_date IS
            SELECT t.cap_date, t.maturity_date, t.withdrawal_date,
                   t.last_withdrawal_date, t.submit_date
              FROM dat_inst_base_info_144 t
             WHERE t.fund_code = p_in_fund_code;
        v_cap_date VARCHAR2(8);
        v_dq_date  VARCHAR2(8);
        v_cnt      NUMBER := 0;
    BEGIN
        FOR r_date IN c_date LOOP
            IF r_date.withdrawal_date IS NULL THEN
                v_cap_date := r_date.cap_date;
                v_dq_date  := r_date.maturity_date;
            ELSE
                v_cap_date := r_date.cap_date;
                v_dq_date  := r_date.withdrawal_date;
            END IF;
            v_cnt := v_cnt + 1;
        END LOOP;
    END;
END pkg_issue144cf;
/
