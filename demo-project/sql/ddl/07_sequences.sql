-- ============================================================
-- All sequences (deduplicated)
-- Auto-consolidated from demo-project/sql/*.sql
-- Each table includes ALL column variants from ALL source files
-- ============================================================

-- SEQ_ARCHIVE (from: gauss_delete_all_styles.sql, gauss_insert_all_styles.sql)
CREATE SEQUENCE seq_archive START WITH 1 INCREMENT BY 1;

-- SEQ_AUDIT (from: gauss_delete_all_styles.sql)
CREATE SEQUENCE seq_audit START WITH 1 INCREMENT BY 1;

-- SEQ_AUDIT_LOG (from: gauss_complete_examples.sql)
CREATE SEQUENCE seq_audit_log START WITH 1 INCREMENT BY 1;

-- SEQ_BATCH_LOG (from: gauss_complete_examples.sql)
CREATE SEQUENCE seq_batch_log START WITH 1 INCREMENT BY 1;

-- SEQ_BONUS (from: gauss_function_calls.sql)
CREATE SEQUENCE seq_bonus START WITH 1 INCREMENT BY 1;

-- SEQ_BONUS_LIMIT_LOG (from: gauss_complete_examples.sql)
CREATE SEQUENCE seq_bonus_limit_log START WITH 1 INCREMENT BY 1;

-- SEQ_CDC (from: pkg_merge_fix1.sql)
CREATE SEQUENCE seq_cdc START WITH 1 INCREMENT BY 1;

-- SEQ_CONFLICT_LOG (from: pkg_merge_fix1.sql)
CREATE SEQUENCE seq_conflict_log START WITH 1 INCREMENT BY 1;

-- SEQ_DW_SALES (from: pkg_merge_fix1.sql)
CREATE SEQUENCE seq_dw_sales START WITH 1 INCREMENT BY 1;

-- SEQ_EMP (from: gauss_insert_all_styles.sql)
CREATE SEQUENCE seq_emp START WITH 1001 INCREMENT BY 1;

-- SEQ_EMPLOYEE_BONUS (from: gauss_complete_examples.sql)
CREATE SEQUENCE seq_employee_bonus START WITH 1 INCREMENT BY 1;

-- SEQ_ERROR_LOG (from: gauss_complete_examples.sql)
CREATE SEQUENCE seq_error_log START WITH 1 INCREMENT BY 1;

-- SEQ_EXCEPTION_LOG (from: gauss_complete_examples.sql)
CREATE SEQUENCE seq_exception_log START WITH 1 INCREMENT BY 1;

-- SEQ_HISTORY (from: gauss_insert_all_styles.sql, gauss_update_all_styles.sql)
CREATE SEQUENCE seq_history START WITH 1 INCREMENT BY 1;

-- SEQ_LOG (from: gauss_delete_all_styles.sql, gauss_function_calls.sql, gauss_insert_all_styles.sql)
CREATE SEQUENCE seq_log START WITH 1 INCREMENT BY 1;

-- SEQ_MERGE_AUDIT (from: pkg_merge_fix1.sql)
CREATE SEQUENCE seq_merge_audit START WITH 1 INCREMENT BY 1;

-- SEQ_PERF (from: gauss_delete_all_styles.sql)
CREATE SEQUENCE seq_perf START WITH 1 INCREMENT BY 1;

-- SEQ_PERFORMANCE_LOG (from: gauss_complete_examples.sql)
CREATE SEQUENCE seq_performance_log START WITH 1 INCREMENT BY 1;

-- SEQ_REJECT (from: pkg_merge_fix1.sql)
CREATE SEQUENCE seq_reject START WITH 1 INCREMENT BY 1;

-- SEQ_RESULT (from: gauss_select_all_styles.sql)
CREATE SEQUENCE seq_result START WITH 1 INCREMENT BY 1;

-- SEQ_SAL_LOG (from: gauss_update_select.sql)
CREATE SEQUENCE seq_sal_log START WITH 1 INCREMENT BY 1;

-- SEQ_SCAN_LOG (from: gauss_complete_examples.sql)
CREATE SEQUENCE seq_scan_log START WITH 1 INCREMENT BY 1;

-- SEQ_SUMMARY (from: gauss_insert_all_styles.sql)
CREATE SEQUENCE seq_summary START WITH 1 INCREMENT BY 1;

