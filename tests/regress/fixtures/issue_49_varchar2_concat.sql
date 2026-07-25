-- ============================================================
-- Regression fixture for Issue #49
-- vProId declared VARCHAR2 but converter maps it to Long
-- due to _infer_type_from_column_name() heuristic (*id* → Long)
-- Then used as String concatenation → semantic error
-- ============================================================
-- Root cause: Column name contains "pro_id" → "_id" → Long heuristic.
-- Actually declared as VARCHAR2 in PL/SQL, used for string
-- concatenation of comma-separated ID lists.
-- ============================================================

CREATE OR REPLACE PACKAGE pkg_issue49_concat AS

    -- VARCHAR2 用 "pro_id" 命名 → 被误映射为 Long
    PROCEDURE proc_build_id_list(
        p_base_ids   IN  VARCHAR,
        p_extra_ids  IN  VARCHAR,
        p_result     OUT VARCHAR
    );

    -- VARCHAR2 用 "trade_ids" 命名 → 也会被误映射为 Long
    PROCEDURE proc_append_trade_ids(
        p_new_id    IN  VARCHAR,
        p_result    OUT VARCHAR
    );

    -- 对比组: 不含 id/no/seq 的 VARCHAR2（应正确映射）
    PROCEDURE proc_concat_labels(
        p_prefix    IN  VARCHAR,
        p_suffix    IN  VARCHAR,
        p_result    OUT VARCHAR
    );

END pkg_issue49_concat;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue49_concat AS

    -- ============================================================
    -- Bug trigger: v_pro_id 是 VARCHAR2(4000)，用于存储逗号分隔ID列表
    -- 但变量名含 "pro_id" → "_id" → 启发式 → Long
    -- 错误: Long vProId = 0L; 然后 vProId = vProId + "8208,..."
    --       Long + String → Java 自动转为 String 拼接（编译通过但语义错）
    -- 后续用 vProId 做数字比较时类型已变成 String → 运行时错误
    -- ============================================================
    PROCEDURE proc_build_id_list(
        p_base_ids   IN  VARCHAR,
        p_extra_ids  IN  VARCHAR,
        p_result     OUT VARCHAR
    ) IS
        v_pro_id VARCHAR(4000);  -- ← 含 "pro_id"，启发式映射为 Long
    BEGIN
        -- 初始化（如果 v_pro_id 被映射为 Long，parseLong 可能失败）
        IF p_base_ids IS NOT NULL THEN
            v_pro_id := p_base_ids;  -- 如果映射为 Long: parseLong(pBaseIds)
        ELSE
            v_pro_id := '6034';      -- 如果映射为 Long: Long.parseLong("6034")
        END IF;

        -- 字符串拼接追加（关键错误点）
        -- PL/SQL: v_pro_id := v_pro_id || '8208, 9, 354, 360, 370, 5821'
        -- 错误 Java: vProId = vProId + "8208, 9, ..."
        --   Long + String → String (Java 自动类型提升)
        --   此时 vProId 从 Long 变成 String！后续使用出错
        v_pro_id := v_pro_id || '8208, 9, 354, 360, 370, 5821';

        -- 再次拼接
        IF p_extra_ids IS NOT NULL THEN
            v_pro_id := v_pro_id || ', ' || p_extra_ids;
        END IF;

        p_result := v_pro_id;
    END;

    -- ============================================================
    -- Bug trigger: v_trade_ids 也是 VARCHAR2，含 "ids" → Long
    -- ============================================================
    PROCEDURE proc_append_trade_ids(
        p_new_id    IN  VARCHAR,
        p_result    OUT VARCHAR
    ) IS
        v_trade_ids VARCHAR(2000);  -- ← 含 "ids"，启发式映射为 Long
    BEGIN
        -- 赋值
        v_trade_ids := '1001, 1002, 1003';

        -- 拼接新 ID
        IF p_new_id IS NOT NULL THEN
            v_trade_ids := v_trade_ids || ',' || p_new_id;
        END IF;

        p_result := v_trade_ids;
    END;

    -- ============================================================
    -- 对比组: 不含 "id/no/seq" 命名 — 应正确映射为 String
    -- ============================================================
    PROCEDURE proc_concat_labels(
        p_prefix    IN  VARCHAR,
        p_suffix    IN  VARCHAR,
        p_result    OUT VARCHAR
    ) IS
        v_labels VARCHAR(1000);  -- 不含触发词，应正确为 String
    BEGIN
        v_labels := p_prefix;
        IF p_suffix IS NOT NULL THEN
            v_labels := v_labels || '_' || p_suffix;
        END IF;
        p_result := v_labels;
    END;

END pkg_issue49_concat;
/
