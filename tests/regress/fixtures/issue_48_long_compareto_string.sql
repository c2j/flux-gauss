-- ============================================================
-- Regression fixture for Issue #48
-- Long.compareTo(String) — type mismatch not caught at compile time
-- ============================================================
-- Root cause: BinaryOp handler at line 9248-9260 early-returns
-- for Long-typed variables, preventing String-vs-Numeric coercion
-- from running. When the right operand is a quoted string literal
-- ('9999'), it stays as String and Long.compareTo(String)
-- will not compile.
-- ============================================================

CREATE OR REPLACE PACKAGE pkg_issue48_compare AS

    -- Long 变量与字符串字面量比较（= / <>）
    PROCEDURE proc_check_pro_id(
        p_pro_id    IN BIGINT,     -- 明确 LONG 类型
        p_result    OUT VARCHAR
    );

    -- Long 变量与数字字面量比较（= / <>）— 应正确
    PROCEDURE proc_check_count(
        p_count     IN BIGINT,
        p_label     OUT VARCHAR
    );

    -- VARCHAR2 变量与数值字面量比较 — 应使用 equals
    PROCEDURE proc_check_status(
        p_status    IN VARCHAR,
        p_code      OUT INT
    );

    -- NUMBER 变量与字符串字面量比较（relational: > < >= <=）
    PROCEDURE proc_compare_level(
        p_level     IN NUMERIC,
        p_threshold IN VARCHAR,    -- 阈值作为字符串传入
        p_result    OUT VARCHAR
    );

END pkg_issue48_compare;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue48_compare AS

    -- ============================================================
    -- Bug trigger: BIGINT 变量 vs 字符串字面量 '9999'
    -- PL/SQL: IF v_pro_id = 9999  (9999 是 NUMBER 字面量，自动类型)
    -- 但如果 SQL 写成 IF v_pro_id = '9999'
    -- 错误 Java: vProId.compareTo("9999") == 0  ← 编译失败
    -- 正确 Java: vProId == 9999L 或 vProId.compareTo(9999L) == 0
    -- ============================================================
    PROCEDURE proc_check_pro_id(
        p_pro_id    IN BIGINT,
        p_result    OUT VARCHAR
    ) IS
        v_pro_id BIGINT;   -- 明确数字类型
        v_count  INT;
    BEGIN
        v_pro_id := p_pro_id;

        -- Bug: 与字符串字面量比较（PL/SQL 允许 NUMBER='字符串'）
        IF v_pro_id = '9999' THEN
            p_result := 'MATCH_9999';
        ELSIF v_pro_id = '350' THEN
            p_result := 'MATCH_350';
        ELSE
            p_result := 'NO_MATCH';
        END IF;

        -- 数字字面量比较（应正确）
        SELECT COUNT(*) INTO v_count
          FROM DUAL;
        IF v_count = 0 THEN
            p_result := p_result || '_ZERO';
        END IF;
    END;

    -- ============================================================
    -- 对比组: 纯数字比较（应生成正确的 Long == 或 compareTo(Long)）
    -- ============================================================
    PROCEDURE proc_check_count(
        p_count     IN BIGINT,
        p_label     OUT VARCHAR
    ) IS
    BEGIN
        -- 这些是纯数字比较，应正确转换
        IF p_count = 0 THEN
            p_label := 'ZERO';
        ELSIF p_count = 100 THEN
            p_label := 'HUNDRED';
        ELSIF p_count > 1000 THEN
            p_label := 'LARGE';
        ELSE
            p_label := 'OTHER';
        END IF;
    END;

    -- ============================================================
    -- 对比组: VARCHAR2 变量 vs 数值字面量（应使用 equals）
    -- ============================================================
    PROCEDURE proc_check_status(
        p_status    IN VARCHAR,
        p_code      OUT INT
    ) IS
    BEGIN
        -- String 变量与数值字面量比较
        -- 应在 String 侧使用 equals，数值侧不应被强制转 Long
        IF p_status = '0' THEN
            p_code := 0;
        ELSIF p_status = '1' THEN
            p_code := 1;
        ELSIF p_status = '2' THEN
            p_code := 2;
        ELSE
            p_code := -1;
        END IF;
    END;

    -- ============================================================
    -- Bug trigger: NUMERIC 变量 vs 字符串字面量（relational 比较）
    -- PL/SQL: IF p_level >= '100'  → 隐式转换
    -- ============================================================
    PROCEDURE proc_compare_level(
        p_level     IN NUMERIC,
        p_threshold IN VARCHAR,
        p_result    OUT VARCHAR
    ) IS
    BEGIN
        IF p_level >= p_threshold THEN
            p_result := 'ABOVE';
        ELSIF p_level > 0 THEN
            p_result := 'POSITIVE';
        ELSE
            p_result := 'ZERO_OR_NEGATIVE';
        END IF;
    END;

END pkg_issue48_compare;
/
