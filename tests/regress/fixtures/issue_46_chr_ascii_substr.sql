-- ============================================================
-- Regression fixture for Issue #46
-- CHR(ASCII(SUBSTR(...))) converted to malformed Java:
--   int String.valueOf(...).charAt(0)
-- Two type keywords back-to-back is invalid Java syntax.
-- ============================================================
-- Root causes:
-- 1. ascii template: `(int) String.valueOf({args0}).charAt(0)`
--    → when args0 has type cast prefix, produces "int String.valueOf..."
-- 2. chr template: `String.valueOf((char)({args0}))`
--    → fails when args0 is String type: (char)("A") invalid
-- 3. _sf_substr: _might_be_long ignores String-typed args
-- ============================================================

CREATE OR REPLACE PACKAGE pkg_issue46_functions AS

    -- CHR(ASCII(SUBSTR(...))) 复合表达式 — 原始 Bug 触发点
    PROCEDURE proc_convert_trade_code(
        p_zqdm    IN  VARCHAR,
        p_result  OUT VARCHAR
    );

    -- ASCII 单独使用
    PROCEDURE proc_get_ascii(
        p_input  IN  VARCHAR,
        p_code   OUT INT
    );

    -- CHR 单独使用
    PROCEDURE proc_get_char(
        p_ascii_code IN INT,
        p_char       OUT VARCHAR
    );

    -- SUBSTR 带字符串偏移（类型混用）
    PROCEDURE proc_substr_mixed(
        p_text    IN  VARCHAR,
        p_start   IN  VARCHAR,  -- 字符串类型的 offset
        p_len     IN  VARCHAR,  -- 字符串类型的 length
        p_result  OUT VARCHAR
    );

    -- 多级 range 分支（模拟原始 bug 中的 7 段 range 模式）
    PROCEDURE proc_range_mapping(
        p_input  IN  VARCHAR,
        p_result OUT VARCHAR
    );

END pkg_issue46_functions;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue46_functions AS

    -- ============================================================
    -- Bug trigger: CHR(ASCII(SUBSTR(...))) 复合链式调用
    -- 原始 PL/SQL 模式:
    --   CHR(ASCII(SUBSTR(i.zqdm, 5, 1)) + 48 + SUBSTR(i.zqdm, 6, 1))
    -- 错误 Java:
    --   (int String.valueOf(...).charAt(0) ...
    -- ============================================================
    PROCEDURE proc_convert_trade_code(
        p_zqdm    IN  VARCHAR,
        p_result  OUT VARCHAR
    ) IS
        v_char1 INT;
        v_char2 INT;
        v_chr   VARCHAR(10);
    BEGIN
        -- CHR + ASCII + SUBSTR 链式调用
        -- 原 bug: int String.valueOf(...).charAt(0) —— 两个类型关键字
        v_char1 := ASCII(SUBSTR(p_zqdm, 5, 1));
        v_char2 := ASCII(SUBSTR(p_zqdm, 6, 1));

        v_chr := CHR(v_char1 + 48) || SUBSTR(p_zqdm, 7, 1);
        p_result := v_chr;
    END;

    -- ============================================================
    -- ASCII 单独调用
    -- 原模板: (int) String.valueOf({args0}).charAt(0)
    -- ============================================================
    PROCEDURE proc_get_ascii(
        p_input  IN  VARCHAR,
        p_code   OUT INT
    ) IS
    BEGIN
        -- 直接调用 ASCII
        p_code := ASCII(SUBSTR(p_input, 1, 1));
    END;

    -- ============================================================
    -- CHR 单独调用 — 测试 String arg 场景
    -- 原模板: String.valueOf((char)({args0}))
    -- 当 args0 是 String 类型时失败
    -- ============================================================
    PROCEDURE proc_get_char(
        p_ascii_code IN INT,
        p_char       OUT VARCHAR
    ) IS
    BEGIN
        -- CHR 接受 INT 参数，正常情况应正确
        p_char := CHR(p_ascii_code);
    END;

    -- ============================================================
    -- SUBSTR 带字符串类型的 offset/length 参数
    -- _sf_substr 中的 _might_be_long() 只处理 Long/BigDecimal，
    -- 不处理 String 类型参数
    -- ============================================================
    PROCEDURE proc_substr_mixed(
        p_text    IN  VARCHAR,
        p_start   IN  VARCHAR,  -- 字符串类型 offset
        p_len     IN  VARCHAR,  -- 字符串类型 length
        p_result  OUT VARCHAR
    ) IS
    BEGIN
        -- SUBSTR 接受 VARCHAR 类型的 start/len（PL/SQL 允许隐式转换）
        -- Java: substring(string - 1) — 编译错误
        p_result := SUBSTR(p_text, p_start, p_len);
    END;

    -- ============================================================
    -- 多 range 分支模式（模拟原始 bug 的 7 段）
    -- 原始 bug 中每一段都重复 CHR(ASCII(SUBSTR(...))) 模式
    -- ============================================================
    PROCEDURE proc_range_mapping(
        p_input  IN  VARCHAR,
        p_result OUT VARCHAR
    ) IS
        v_num INT;
    BEGIN
        v_num := ASCII(SUBSTR(p_input, 1, 1));

        IF v_num >= 49 AND v_num <= 57 THEN
            p_result := CHR(v_num + 16);
        ELSIF v_num >= 65 AND v_num <= 74 THEN
            p_result := CHR(v_num - 16);
        ELSIF v_num >= 75 AND v_num <= 84 THEN
            p_result := CHR(v_num - 30);
        ELSE
            p_result := CHR(v_num);
        END IF;
    END;

END pkg_issue46_functions;
/
