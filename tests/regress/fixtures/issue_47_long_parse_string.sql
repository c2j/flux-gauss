-- ============================================================
-- Regression fixture for Issue #47
-- Long.parseLong("2.5.1") — NumberFormatException at runtime
-- ============================================================
-- Root cause: _infer_type_from_column_name() heuristically
-- maps "*no*", "*id*", "*num*", "*seq*" column names to Long.
-- VARCHAR2 variables with these name patterns get mis-typed.
-- String assignments like '2.5.1' become Long.parseLong("2.5.1").
-- ============================================================

CREATE TABLE t_issue47_config (
    config_id    BIGINT PRIMARY KEY,
    step_no      VARCHAR(50),    -- 步骤编号，含点号 "2.5.1"
    process_id   VARCHAR(50),    -- 流程ID，非纯数字
    batch_no     VARCHAR(50),    -- 批号
    seq_num      INT,            -- 序号（真正数字）
    config_name  VARCHAR(200),
    config_value VARCHAR(1000)
);

CREATE OR REPLACE PACKAGE pkg_issue47_types AS

    -- 含 "step_no" 的变量（含 "no" → 被启发式映射为 Long）
    PROCEDURE proc_set_step_no(
        p_seq       IN INT,
        p_sub_seq   IN INT,
        p_step_no   OUT VARCHAR
    );

    -- 含 "id" 的 VARCHAR2 变量（→ 被启发式映射为 Long）
    PROCEDURE proc_process_id(
        p_type      IN VARCHAR,
        p_pro_id    OUT VARCHAR     -- 流程ID，非数字
    );

    -- 对比组: 明确声明为 VARCHAR 的变量（不应被错误映射）
    PROCEDURE proc_correct_varchar(
        p_label     IN VARCHAR,
        p_value     OUT VARCHAR
    );

END pkg_issue47_types;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue47_types AS

    -- ============================================================
    -- Bug trigger: v_step_no 声明为 VARCHAR2(50)
    -- 但包含 "no" → 启发式 → Long
    -- 赋值 '2.5.1' → Long.parseLong("2.5.1") → NumberFormatException
    -- ============================================================
    PROCEDURE proc_set_step_no(
        p_seq       IN INT,
        p_sub_seq   IN INT,
        p_step_no   OUT VARCHAR
    ) IS
        v_step_no VARCHAR(50);  -- ← 含 "no"，会被映射为 Long
    BEGIN
        IF p_seq = 1 THEN
            IF p_sub_seq = 1 THEN
                v_step_no := '1.1';
            ELSE
                v_step_no := '1.' || CAST(p_sub_seq AS VARCHAR);
            END IF;
        ELSIF p_seq = 2 THEN
            IF p_sub_seq = 5 THEN
                v_step_no := '2.5.1';  -- ← 非数字字符串！
            ELSIF p_sub_seq = 7 THEN
                v_step_no := '2.7';    -- ← 非数字字符串！
            ELSIF p_sub_seq = 8 THEN
                v_step_no := '2.8';    -- ← 非数字字符串！
            ELSE
                v_step_no := '2.' || CAST(p_sub_seq AS VARCHAR);
            END IF;
        ELSE
            v_step_no := CAST(p_seq AS VARCHAR) || '.0';
        END IF;

        p_step_no := v_step_no;
    END;

    -- ============================================================
    -- Bug trigger: v_pro_id 声明为 VARCHAR2
    -- 但包含 "pro_id" → "_id" → Long
    -- 赋值 '6034' → Long.parseLong("6034") (侥幸成功)
    -- 但后续拼接 → 类型语义错误
    -- ============================================================
    PROCEDURE proc_process_id(
        p_type      IN VARCHAR,
        p_pro_id    OUT VARCHAR
    ) IS
        v_pro_id VARCHAR(200);  -- ← 含 "pro_id"，会被映射为 Long
    BEGIN
        IF p_type = 'NEW' THEN
            v_pro_id := '6034';
        ELSIF p_type = 'OLD' THEN
            v_pro_id := '5821';
        ELSE
            v_pro_id := '9999';
        END IF;

        -- 字符串拼接（如果 v_pro_id 被映射为 Long，此处 Long+String 语义错误）
        v_pro_id := v_pro_id || ',8208,9,354,360,370,6034,5821';

        p_pro_id := v_pro_id;
    END;

    -- ============================================================
    -- 对比组: 明确声明为 VARCHAR
    -- 不应受命名启发式影响
    -- ============================================================
    PROCEDURE proc_correct_varchar(
        p_label     IN VARCHAR,
        p_value     OUT VARCHAR
    ) IS
        v_label VARCHAR(100);  -- 不含 "id/no/num/seq"，应正确映射为 String
    BEGIN
        v_label := p_label;
        IF LENGTH(v_label) = 0 THEN
            v_label := 'DEFAULT';
        END IF;
        p_value := v_label;
    END;

END pkg_issue47_types;
/
