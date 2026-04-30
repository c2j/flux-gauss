-- =====================================================================
-- 自定义函数调用测试用例
-- 验证用户自定义函数调用与内置函数调用在 AST 中的区别
-- ogsql 解析结果：
--   自定义函数 → FunctionCall  (与大多数内置函数相同)
--   substr/substring → SpecialFunction (唯一被特殊对待的内置函数)
-- =====================================================================

-- 被调用的自定义函数（需单独文件或位于同文件前部）
CREATE OR REPLACE FUNCTION pkg_custom_funcs.format_amount(p_amount NUMERIC) RETURNS VARCHAR
AS $$
BEGIN
    RETURN to_char(p_amount);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION pkg_custom_funcs.get_status_label(p_status VARCHAR) RETURNS VARCHAR
AS $$
BEGIN
    RETURN upper(p_status);
END;
$$ LANGUAGE plpgsql;

-- 测试存储过程：混合使用自定义函数和内置函数
CREATE OR REPLACE PROCEDURE pkg_custom_funcs.test_mixed_calls(p_order_id BIGINT)
AS $$
DECLARE
    v_status  VARCHAR;
    v_amount  NUMERIC;
    v_label   VARCHAR;
BEGIN
    -- 自定义函数调用（FunctionCall, 2-part name: pkg.func）
    v_label := pkg_custom_funcs.format_amount(100.50);
    v_label := pkg_custom_funcs.get_status_label('pending');

    -- 自定义函数调用（FunctionCall, 1-part name: func）
    v_label := format_amount(200.00);

    -- 内置函数调用（FunctionCall）
    v_label := upper(v_label);
    v_label := nvl(v_label, 'UNKNOWN');

    -- 内置函数调用（SpecialFunction）
    v_label := substr(v_label, 1, 10);

    -- 混合嵌套：自定义函数 + 内置函数
    v_label := upper(pkg_custom_funcs.get_status_label('active'));
    v_label := substr(pkg_custom_funcs.format_amount(v_amount), 1, 5);

    -- 在 IF 中混合使用
    IF pkg_custom_funcs.get_status_label(v_status) = 'ACTIVE' THEN
        v_amount := 1;
    ELSIF substr(v_label, 1, 1) = 'P' THEN
        v_amount := 2;
    END IF;
END;
$$ LANGUAGE plpgsql;
