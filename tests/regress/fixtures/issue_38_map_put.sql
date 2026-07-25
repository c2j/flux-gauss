-- ============================================================
-- Regression fixture for Issue #38 (__MAP_PUT__ residual code)
-- ============================================================
-- Tests that cross-package variable assignments do NOT produce
-- illegal __MAP_PUT__ placeholder code in generated Java.
--
-- The issue: converting `pkg_other.out_var := value` generates
-- `__MAP_PUT__pkgOther__out_var = value` which is not valid Java.
-- ============================================================

CREATE OR REPLACE PACKAGE pkg_issue38_shared AS
    -- Package-level variables that get assigned across packages
    out_status     VARCHAR(200);
    out_error_code VARCHAR(50);
    out_error_msg  VARCHAR(1000);
    global_counter INT := 0;
END pkg_issue38_shared;
/

CREATE OR REPLACE PACKAGE pkg_issue38_main AS

    PROCEDURE proc_do_work(
        p_input      IN VARCHAR,
        p_status     OUT VARCHAR
    );

    PROCEDURE proc_cross_set_error(
        p_code IN VARCHAR,
        p_msg  IN VARCHAR
    );

    PROCEDURE proc_increment_counter;

END pkg_issue38_main;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue38_main AS

    -- Cross-package variable assignment (issue #38 trigger)
    PROCEDURE proc_do_work(
        p_input      IN VARCHAR,
        p_status     OUT VARCHAR
    ) IS
    BEGIN
        -- This should NOT generate __MAP_PUT__
        pkg_issue38_shared.out_status := 'PROCESSING';
        pkg_issue38_shared.out_error_code := '';
        pkg_issue38_shared.out_error_msg := '';

        IF p_input IS NULL THEN
            -- This should NOT generate __MAP_PUT__
            pkg_issue38_shared.out_status := 'FAILED';
            pkg_issue38_shared.out_error_code := 'E001';
            pkg_issue38_shared.out_error_msg := 'Input is null';
            p_status := 'FAILED';
        ELSE
            pkg_issue38_shared.out_status := 'SUCCESS';
            pkg_issue38_shared.global_counter := pkg_issue38_shared.global_counter + 1;
            p_status := 'SUCCESS';
        END IF;
    END;

    -- Direct cross-package variable write
    PROCEDURE proc_cross_set_error(
        p_code IN VARCHAR,
        p_msg  IN VARCHAR
    ) IS
    BEGIN
        -- These should NOT generate __MAP_PUT__
        pkg_issue38_shared.out_error_code := p_code;
        pkg_issue38_shared.out_error_msg := p_msg;
    END;

    -- Package variable with arithmetic
    PROCEDURE proc_increment_counter IS
    BEGIN
        pkg_issue38_shared.global_counter := pkg_issue38_shared.global_counter + 1;

        IF pkg_issue38_shared.global_counter > 1000 THEN
            pkg_issue38_shared.out_status := 'OVERFLOW';
        END IF;
    END;

END pkg_issue38_main;
/
