CREATE OR REPLACE PACKAGE pkg_issue143sub IS
    FUNCTION f_any(p_value IN NUMBER) RETURN NUMBER;
END pkg_issue143sub;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue143sub IS
    -- PR #143 review: ScalarSublink (= ANY (SELECT ...)) must fail loudly —
    -- generate _unsupportedScalarSublink(...) which throws
    -- UnsupportedOperationException, never a legal-but-constant-false literal.
    FUNCTION f_any(p_value IN NUMBER) RETURN NUMBER IS
    BEGIN
        IF p_value = ANY (SELECT 1) THEN
            RETURN 1;
        END IF;
        RETURN 0;
    END;
END pkg_issue143sub;
/
