-- ============================================================
-- Regression fixture for Issue #41 (Type system defects)
-- ============================================================
-- Tests that:
-- 1. NUMBER params → Long/BigDecimal not String
-- 2. DATE params → LocalDate/Date not String
-- 3. BOOLEAN params → Boolean not String
-- 4. EXCEPTION type → BusinessException not String
-- 5. VARCHAR2 still maps to String (correct)
-- ============================================================

CREATE TABLE t_issue41_account (
    account_id      BIGINT PRIMARY KEY,
    account_name    VARCHAR(200),
    balance         NUMERIC(18,4),
    open_date       DATE,
    is_active       BOOLEAN,
    credit_limit    NUMERIC(18,4)
);

CREATE OR REPLACE PACKAGE pkg_issue41_types AS

    -- User-defined EXCEPTION (should NOT be String)
    account_not_found EXCEPTION;
    insufficient_funds EXCEPTION;
    invalid_amount EXCEPTION;

    -- Constants with explicit types
    MIN_BALANCE  CONSTANT NUMERIC(18,4) := 0.00;
    MAX_CREDIT   CONSTANT NUMERIC(18,4) := 100000.00;

    -- Procedure with typed parameters (NUMBER, DATE, BOOLEAN)
    PROCEDURE proc_transfer(
        p_from_account   IN BIGINT,
        p_to_account     IN BIGINT,
        p_amount         IN NUMERIC,     -- should be BigDecimal, not String
        p_transfer_date  IN DATE,        -- should be LocalDate, not String
        p_is_urgent      IN BOOLEAN,     -- should be Boolean, not String
        p_new_balance    OUT NUMERIC,    -- OUT param should be correct type
        p_status         OUT VARCHAR
    );

    -- Procedure that uses EXCEPTION type
    PROCEDURE proc_withdraw(
        p_account_id IN BIGINT,
        p_amount     IN NUMERIC,
        p_new_balance OUT NUMERIC,
        p_status     OUT VARCHAR
    );

    -- Procedure with local EXCEPTION declarations
    PROCEDURE proc_validate_account(
        p_account_id IN BIGINT,
        p_is_valid   OUT BOOLEAN,
        p_balance    OUT NUMERIC
    );

END pkg_issue41_types;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue41_types AS

    -- Typed parameters test
    PROCEDURE proc_transfer(
        p_from_account   IN BIGINT,
        p_to_account     IN BIGINT,
        p_amount         IN NUMERIC,
        p_transfer_date  IN DATE,
        p_is_urgent      IN BOOLEAN,
        p_new_balance    OUT NUMERIC,
        p_status         OUT VARCHAR
    ) IS
        v_from_balance t_issue41_account.balance%TYPE;  -- %TYPE should resolve from DDL
        v_to_balance   t_issue41_account.balance%TYPE;
    BEGIN
        -- SELECT with %TYPE variable
        SELECT balance
          INTO v_from_balance
          FROM t_issue41_account
         WHERE account_id = p_from_account;

        -- Numeric comparison - should use native operators (>), not compareTo
        IF v_from_balance < p_amount THEN
            RAISE insufficient_funds;
        END IF;

        -- BOOLEAN comparison - should be native, not String compare
        IF p_is_urgent THEN
            p_status := 'URGENT TRANSFER';
        END IF;

        -- Numeric arithmetic
        v_from_balance := v_from_balance - p_amount;

        UPDATE t_issue41_account
           SET balance = v_from_balance
         WHERE account_id = p_from_account;

        SELECT balance
          INTO v_to_balance
          FROM t_issue41_account
         WHERE account_id = p_to_account;

        v_to_balance := v_to_balance + p_amount;

        UPDATE t_issue41_account
           SET balance = v_to_balance
         WHERE account_id = p_to_account;

        p_new_balance := v_from_balance;
        p_status := 'TRANSFER COMPLETE';
    END;

    -- EXCEPTION type usage
    PROCEDURE proc_withdraw(
        p_account_id IN BIGINT,
        p_amount     IN NUMERIC,
        p_new_balance OUT NUMERIC,
        p_status     OUT VARCHAR
    ) IS
        v_balance t_issue41_account.balance%TYPE;
    BEGIN
        SELECT balance, is_active
          INTO v_balance
          FROM t_issue41_account
         WHERE account_id = p_account_id;

        IF p_amount <= 0 THEN
            RAISE invalid_amount;
        END IF;

        IF v_balance < p_amount THEN
            RAISE insufficient_funds;
        END IF;

        v_balance := v_balance - p_amount;

        UPDATE t_issue41_account
           SET balance = v_balance
         WHERE account_id = p_account_id;

        p_new_balance := v_balance;
        p_status := 'WITHDRAWAL SUCCESS';

    EXCEPTION
        WHEN insufficient_funds THEN
            p_status := 'Insufficient funds';
            p_new_balance := v_balance;
        WHEN invalid_amount THEN
            p_status := 'Invalid amount';
            p_new_balance := v_balance;
        WHEN OTHERS THEN
            p_status := 'Error: ' || SQLERRM;
            p_new_balance := v_balance;
    END;

    -- Local EXCEPTION declaration (Issue #41: should NOT be String)
    PROCEDURE proc_validate_account(
        p_account_id IN BIGINT,
        p_is_valid   OUT BOOLEAN,
        p_balance    OUT NUMERIC
    ) IS
        -- Local EXCEPTION declaration - should map to BusinessException, not String
        e_account_closed EXCEPTION;
        v_balance NUMERIC;
        v_active  BOOLEAN;
    BEGIN
        SELECT balance, is_active
          INTO v_balance, v_active
          FROM t_issue41_account
         WHERE account_id = p_account_id;

        IF NOT v_active THEN
            RAISE e_account_closed;
        END IF;

        p_is_valid := TRUE;
        p_balance := v_balance;

    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            RAISE account_not_found;
        WHEN e_account_closed THEN
            p_is_valid := FALSE;
            p_balance := 0;
    END;

END pkg_issue41_types;
/
