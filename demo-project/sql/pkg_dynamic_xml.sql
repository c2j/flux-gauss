-- ============================================================
-- pkg_dynamic_xml — Dynamic SQL → MyBatis Dynamic XML Test Cases
--
-- 本包包含三种动态 SQL 模式的测试用例：
--   模式 A: 条件 WHERE / ORDER BY 拼接 → 期望生成 <if>/<where> 标签
--   模式 A+: UPDATE 条件 SET 子句 → 期望生成 <set>/<if> 标签
--   负例: 混合逻辑 IF → 期望保持 Java if/else，不生成动态 XML
-- ============================================================

CREATE OR REPLACE PACKAGE pkg_dynamic_xml IS

    -- 模式 A: 经典条件 WHERE + ORDER BY
    -- 期望: Mapper XML 包含 <where><if test="whereClause != null">...</if></where>
    --       和 <if test="orderBy != null">ORDER BY ${orderBy}</if>
    PROCEDURE proc_conditional_query(
        p_table_name    VARCHAR2,
        p_where_clause  VARCHAR2 DEFAULT NULL,
        p_order_by      VARCHAR2 DEFAULT NULL,
        p_limit         INTEGER  DEFAULT 100
    );

    -- 模式 A+: 条件 SET 子句
    -- 期望: Mapper XML 包含 <set><if test="status != null">status = #{status}</if>...</set>
    PROCEDURE proc_conditional_update(
        p_table_name VARCHAR2,
        p_id         INTEGER,
        p_status     VARCHAR2 DEFAULT NULL,
        p_amount     NUMBER   DEFAULT NULL,
        p_remark     VARCHAR2 DEFAULT NULL
    );

    -- 负例: 混合逻辑 IF — 不同分支构造完全不同的 SQL
    -- 期望: 保持 Java if/else + 多个 mapper 调用，不合并为单一动态 XML
    PROCEDURE proc_mixed_logic(
        p_table_name VARCHAR2,
        p_mode       VARCHAR2
    );

END pkg_dynamic_xml;
/

CREATE OR REPLACE PACKAGE BODY pkg_dynamic_xml IS

    -- ==========================================================
    -- 模式 A: 经典条件 WHERE + ORDER BY
    -- ==========================================================
    PROCEDURE proc_conditional_query(
        p_table_name    VARCHAR2,
        p_where_clause  VARCHAR2 DEFAULT NULL,
        p_order_by      VARCHAR2 DEFAULT NULL,
        p_limit         INTEGER  DEFAULT 100
    ) IS
        v_sql VARCHAR2(4000);
        v_count INTEGER;
    BEGIN
        -- 基础 SELECT
        v_sql := 'SELECT id, name, amount, status, create_time FROM ' || p_table_name;

        -- 条件 WHERE 子句
        IF p_where_clause IS NOT NULL THEN
            v_sql := v_sql || ' WHERE ' || p_where_clause;
        END IF;

        -- 条件 ORDER BY 子句
        IF p_order_by IS NOT NULL THEN
            v_sql := v_sql || ' ORDER BY ' || p_order_by;
        END IF;

        -- 固定 LIMIT（非条件）
        v_sql := v_sql || ' LIMIT ' || p_limit;

        DBE_OUTPUT.PRINT_LINE('Generated SQL: ' || v_sql);

        EXECUTE IMMEDIATE v_sql;

        -- 审计日志（非动态 SQL）
        SELECT COUNT(*) INTO v_count FROM audit_log WHERE operation = 'QUERY';
    END proc_conditional_query;

    -- ==========================================================
    -- 模式 A+: 条件 SET 子句
    -- ==========================================================
    PROCEDURE proc_conditional_update(
        p_table_name VARCHAR2,
        p_id         INTEGER,
        p_status     VARCHAR2 DEFAULT NULL,
        p_amount     NUMBER   DEFAULT NULL,
        p_remark     VARCHAR2 DEFAULT NULL
    ) IS
        v_sql VARCHAR2(4000);
        v_set_clause VARCHAR2(2000) := '';
    BEGIN
        v_sql := 'UPDATE ' || p_table_name;

        IF p_status IS NOT NULL THEN
            IF v_set_clause IS NOT NULL AND v_set_clause != '' THEN
                v_set_clause := v_set_clause || ', ';
            END IF;
            v_set_clause := v_set_clause || 'status = ''' || p_status || '''';
        END IF;

        IF p_amount IS NOT NULL THEN
            IF v_set_clause IS NOT NULL AND v_set_clause != '' THEN
                v_set_clause := v_set_clause || ', ';
            END IF;
            v_set_clause := v_set_clause || 'amount = ' || p_amount;
        END IF;

        IF p_remark IS NOT NULL THEN
            IF v_set_clause IS NOT NULL AND v_set_clause != '' THEN
                v_set_clause := v_set_clause || ', ';
            END IF;
            v_set_clause := v_set_clause || 'remark = ''' || p_remark || '''';
        END IF;

        IF v_set_clause IS NOT NULL AND v_set_clause != '' THEN
            v_sql := v_sql || ' SET ' || v_set_clause || ' WHERE id = ' || p_id;
            EXECUTE IMMEDIATE v_sql;
        END IF;
    END proc_conditional_update;

    -- ==========================================================
    -- 负例: 混合逻辑 IF — 不同分支构造完全不同的 SQL
    -- 期望: 保持 Java if/else，不合并为单一动态 XML
    -- ==========================================================
    PROCEDURE proc_mixed_logic(
        p_table_name VARCHAR2,
        p_mode       VARCHAR2
    ) IS
        v_sql VARCHAR2(4000);
    BEGIN
        IF p_mode = 'ARCHIVE' THEN
            -- 分支1: INSERT INTO ... _hist
            v_sql := 'INSERT INTO ' || p_table_name || '_hist SELECT * FROM ' || p_table_name;
            EXECUTE IMMEDIATE v_sql;
            -- 混合逻辑: 审计日志
            INSERT INTO audit_log(log_time, operation, sql_text)
            VALUES (SYSTIMESTAMP, 'ARCHIVE', v_sql);
        ELSIF p_mode = 'CLEAN' THEN
            -- 分支2: DELETE 旧数据
            v_sql := 'DELETE FROM ' || p_table_name || ' WHERE create_time < SYSDATE - 30';
            EXECUTE IMMEDIATE v_sql;
            -- 混合逻辑: 审计日志
            INSERT INTO audit_log(log_time, operation, sql_text)
            VALUES (SYSTIMESTAMP, 'CLEAN', v_sql);
        ELSE
            -- 分支3: 仅打印
            DBE_OUTPUT.PRINT_LINE('Unknown mode: ' || p_mode);
        END IF;
    END proc_mixed_logic;

END pkg_dynamic_xml;
/
