-- ============================================================
-- Regression fixture for Issue #40 (String comparison inconsistency)
-- ============================================================
-- Tests that:
-- 1. String equality comparison uses equals(), not compareTo()
-- 2. Numeric comparison on String values converts to numeric first
-- 3. Lexicographic compareTo() is NOT used for numeric comparison semantics
-- ============================================================

CREATE OR REPLACE PACKAGE pkg_issue40_compare AS

    -- String equality comparisons (=)
    PROCEDURE proc_check_status(
        p_status     IN VARCHAR,
        p_result     OUT VARCHAR
    );

    -- String with relational comparisons (>=, <=)
    PROCEDURE proc_validate_level(
        p_level      IN VARCHAR,
        p_min_level  IN VARCHAR,
        p_is_valid   OUT BOOLEAN
    );

    -- Numeric-like strings in comparisons
    PROCEDURE proc_compare_versions(
        p_version    IN VARCHAR,
        p_min_ver    IN VARCHAR,
        p_ok         OUT BOOLEAN
    );

END pkg_issue40_compare;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue40_compare AS

    -- String equality: should use .equals() not .compareTo()
    PROCEDURE proc_check_status(
        p_status     IN VARCHAR,
        p_result     OUT VARCHAR
    ) IS
    BEGIN
        -- EQUALITY comparison (=) - should be .equals()
        IF p_status = 'ACTIVE' THEN
            p_result := 'OK';
        ELSIF p_status = 'INACTIVE' THEN
            p_result := 'DISABLED';
        ELSIF p_status = 'PENDING' THEN
            p_result := 'WAIT';
        ELSE
            p_result := 'UNKNOWN';
        END IF;
    END;

    -- String relational comparison (>=) - should convert to numeric or
    -- at least not use compareTo for pure numeric semantics
    PROCEDURE proc_validate_level(
        p_level      IN VARCHAR,
        p_min_level  IN VARCHAR,
        p_is_valid   OUT BOOLEAN
    ) IS
    BEGIN
        -- >= comparison: if these are numbers-as-strings,
        -- compareTo("3") >= 0 gives wrong result for "10" (lexicographic)
        IF p_level >= p_min_level THEN
            p_is_valid := TRUE;
        ELSE
            p_is_valid := FALSE;
        END IF;
    END;

    -- Numeric-like strings: compareTo("10") vs compareTo("9")
    -- Lexicographic: "10" < "9" (wrong for numeric semantics)
    PROCEDURE proc_compare_versions(
        p_version    IN VARCHAR,
        p_min_ver    IN VARCHAR,
        p_ok         OUT BOOLEAN
    ) IS
    BEGIN
        -- == comparison on version strings
        IF p_version = '0' THEN
            p_ok := FALSE;
            RETURN;
        END IF;

        -- >= comparison on version strings
        IF p_version >= p_min_ver THEN
            p_ok := TRUE;
        ELSE
            p_ok := FALSE;
        END IF;
    END;

END pkg_issue40_compare;
/
