-- ============================================================
-- Regression fixture for Issue #39 (Thread safety of package variables)
-- ============================================================
-- Package-level variables mapped as Service instance fields in
-- Spring singleton scope → thread safety issue.
--
-- Tests that:
-- 1. Read-only package vars → should be static final
-- 2. Mutable package vars → should be ThreadLocal or have warning comment
-- ============================================================

CREATE OR REPLACE PACKAGE pkg_issue39_config AS
    -- Read-only constants (should generate: static final)
    MAX_RETRY_COUNT    CONSTANT INT := 3;
    DEFAULT_TIMEOUT    CONSTANT INT := 30;
    APP_NAME           CONSTANT VARCHAR(100) := 'FluxGaussTest';

    -- Mutable package variables (thread safety concern)
    g_current_user     VARCHAR(100);
    g_session_id       VARCHAR(50);
    g_debug_mode       BOOLEAN := FALSE;

    -- Package variable that gets mutated in procedures
    g_batch_status     VARCHAR(20);

    PROCEDURE proc_set_session(
        p_user     IN VARCHAR,
        p_session  IN VARCHAR
    );

    PROCEDURE proc_enable_debug;

    PROCEDURE proc_run_batch(
        p_batch_name IN VARCHAR,
        p_status     OUT VARCHAR
    );

END pkg_issue39_config;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue39_config AS

    -- Sets mutable package variables (thread safety: concurrent calls will clash)
    PROCEDURE proc_set_session(
        p_user     IN VARCHAR,
        p_session  IN VARCHAR
    ) IS
    BEGIN
        g_current_user := p_user;
        g_session_id   := p_session;

        IF g_current_user = 'ADMIN' THEN
            g_debug_mode := TRUE;
        END IF;

        g_batch_status := 'IDLE';
    END;

    PROCEDURE proc_enable_debug IS
    BEGIN
        g_debug_mode := TRUE;
    END;

    PROCEDURE proc_run_batch(
        p_batch_name IN VARCHAR,
        p_status     OUT VARCHAR
    ) IS
        v_count INT;
    BEGIN
        g_batch_status := 'RUNNING';

        -- Simulate batch work
        IF g_debug_mode THEN
            p_status := 'DEBUG: batch ' || p_batch_name || ' running for user ' || g_current_user;
        ELSE
            p_status := 'Batch ' || p_batch_name || ' completed';
        END IF;

        g_batch_status := 'COMPLETED';
    END;

END pkg_issue39_config;
/
