CREATE OR REPLACE TABLE dat_invest_in_out_144 (
    operation_no   NUMBER,
    batch_cnt      INTEGER,
    remark         VARCHAR2(64)
);

CREATE OR REPLACE PACKAGE pkg_issue144cmp IS
    PROCEDURE prc_cmp_literal;
END pkg_issue144cmp;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue144cmp IS
    -- Issue #144 batch 4a: PL/SQL implicit conversion lets a numeric column
    -- compare against a string literal (operation_no = '4801040001'). The
    -- Python engine emitted ((Number) ...).longValue() == "4801040001" —
    -- long == String does not compile. String literals on either side of a
    -- .longValue()/.intValue()/.doubleValue() comparison must be re-rendered
    -- as numeric literals, and String-typed variables must be wrapped in
    -- Xxx.parseXxx(...).
    PROCEDURE prc_cmp_literal IS
        CURSOR c_info IS
            SELECT t.operation_no, t.batch_cnt
              FROM dat_invest_in_out_144 t;
        v_succ_result VARCHAR2(8);
        v_cnt         NUMBER := 0;
    BEGIN
        FOR r_info IN c_info LOOP
            IF r_info.operation_no = '4801040001' THEN
                v_cnt := v_cnt + 1;
            END IF;
            IF r_info.operation_no <> '888' THEN
                v_cnt := v_cnt + 2;
            END IF;
            IF '777' = r_info.operation_no THEN
                v_cnt := v_cnt + 4;
            END IF;
            IF r_info.batch_cnt = '42' THEN
                v_cnt := v_cnt + 8;
            END IF;
            IF v_succ_result = r_info.operation_no THEN
                v_cnt := v_cnt + 16;
            END IF;
            IF v_succ_result <> r_info.batch_cnt THEN
                v_cnt := v_cnt + 32;
            END IF;
        END LOOP;
    END;
END pkg_issue144cmp;
/
