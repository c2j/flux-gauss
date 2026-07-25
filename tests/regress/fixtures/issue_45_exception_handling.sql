-- ============================================================
-- Regression fixture for Issue #45
-- EXCEPTION WHEN no_data_found THEN ... WHEN OTHERS THEN
-- converted to two peer-level catch blocks — Java disallows this
-- ============================================================
-- Root cause: Converter splits a single EXCEPTION block's
-- multiple WHEN clauses into separate catch blocks.
-- PL/SQL: EXCEPTION WHEN A THEN ... WHEN B THEN ...
-- Wrong Java: catch(A){} catch(B){}   ← valid Java but wrong semantics
-- OR: two catch at same level inside inner try → illegal
-- ============================================================

CREATE TABLE t_issue45_security (
    security_id   VARCHAR(20) PRIMARY KEY,
    security_name VARCHAR(200),
    market        VARCHAR(10)
);

CREATE TABLE t_issue45_security_bak (
    security_id   VARCHAR(20) PRIMARY KEY,
    security_name VARCHAR(200),
    market        VARCHAR(10)
);

CREATE OR REPLACE PACKAGE pkg_issue45_exception AS

    -- 主流程: 嵌套 EXCEPTION WHEN no_data_found + WHEN OTHERS
    PROCEDURE proc_link_etf_repay(
        p_security_code  IN  VARCHAR,
        p_o_succeed      OUT VARCHAR,
        p_o_security_id  OUT VARCHAR
    );

    -- 对比组: 单层 EXCEPTION WHEN OTHERS（应正确生成 try-catch）
    PROCEDURE proc_simple_exception(
        p_security_code IN VARCHAR,
        p_result        OUT VARCHAR
    );

    -- 多层嵌套 EXCEPTION（内层 BEGIN-EXCEPTION-END 内含两个 WHEN）
    PROCEDURE proc_nested_exception(
        p_code    IN VARCHAR,
        p_msg     OUT VARCHAR
    );

END pkg_issue45_exception;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue45_exception AS

    -- ============================================================
    -- Bug trigger: 同一个 EXCEPTION 块的两个 WHEN 分支
    -- PL/SQL 原始模式:
    --   BEGIN
    --     SELECT ... INTO v_id FROM t1 WHERE ...;
    --   EXCEPTION
    --     WHEN no_data_found THEN
    --       BEGIN SELECT ... INTO v_id FROM t2 WHERE ...;
    --       EXCEPTION WHEN OTHERS THEN ... END;
    --     WHEN OTHERS THEN
    --       ...;
    --   END;
    -- 错误Java: 两个平级 catch 块
    -- 正确Java: 应转换为 if(result==null) + 单层 try-catch
    -- ============================================================
    PROCEDURE proc_link_etf_repay(
        p_security_code  IN  VARCHAR,
        p_o_succeed      OUT VARCHAR,
        p_o_security_id  OUT VARCHAR
    ) IS
        v_security_id VARCHAR(20);
    BEGIN
        -- 主表查询
        SELECT t.security_id INTO v_security_id
          FROM t_issue45_security t
         WHERE t.security_name = p_security_code;

        p_o_succeed := 'FOUND_MAIN';
        p_o_security_id := v_security_id;

    EXCEPTION
        WHEN no_data_found THEN
            -- 主表没有，查备份表
            BEGIN
                SELECT t.security_id INTO v_security_id
                  FROM t_issue45_security_bak t
                 WHERE t.security_name = p_security_code;

                p_o_succeed := 'FOUND_BAK';
                p_o_security_id := v_security_id;
            EXCEPTION
                WHEN OTHERS THEN
                    p_o_succeed := 'ERROR: 没有在证券代码维护中维护！';
                    p_o_security_id := '';
            END;
        WHEN OTHERS THEN
            p_o_succeed := 'ERROR: 证券代码查询异常';
            p_o_security_id := '';
    END;

    -- ============================================================
    -- 对比组: 简单单层 EXCEPTION（应生成正确的 try-catch）
    -- ============================================================
    PROCEDURE proc_simple_exception(
        p_security_code IN VARCHAR,
        p_result        OUT VARCHAR
    ) IS
        v_id VARCHAR(20);
    BEGIN
        SELECT t.security_id INTO v_id
          FROM t_issue45_security t
         WHERE t.security_name = p_security_code;

        p_result := 'OK: ' || v_id;
    EXCEPTION
        WHEN OTHERS THEN
            p_result := 'ERROR: ' || SQLERRM;
    END;

    -- ============================================================
    -- Bug trigger: 内层 BEGIN-EXCEPTION-END 内含两个 WHEN
    -- ============================================================
    PROCEDURE proc_nested_exception(
        p_code    IN VARCHAR,
        p_msg     OUT VARCHAR
    ) IS
        v_value VARCHAR(100);
    BEGIN
        BEGIN
            SELECT t.security_name INTO v_value
              FROM t_issue45_security t
             WHERE t.security_id = p_code;

            p_msg := 'VALUE: ' || v_value;
        EXCEPTION
            WHEN no_data_found THEN
                p_msg := 'NOT_FOUND: ' || p_code;
            WHEN OTHERS THEN
                p_msg := 'SYSTEM_ERROR: ' || SQLERRM;
        END;
    EXCEPTION
        WHEN OTHERS THEN
            p_msg := 'OUTER_ERROR';
    END;

END pkg_issue45_exception;
/
