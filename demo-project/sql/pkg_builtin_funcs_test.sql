-- NOTE: DDL moved to ddl/*.sql

CREATE OR REPLACE PROCEDURE pkg_builtin_funcs.test_all_funcs(p_input VARCHAR)
AS $$
DECLARE
    v_result  VARCHAR;
    v_amount  NUMERIC;
    v_idx     INTEGER;
BEGIN
    -- ================================================================
    -- 1. SUBSTR / SUBSTRING  →  SpecialFunction (ogsql 特殊处理)
    --    SQL: SUBSTR(str, start, len)  start 从 1 开始
    --    Java 等价: str.substring(start-1, start-1+len)
    -- ================================================================

    -- 3-arg: substr('abcdef', 2, 3) → 'bcd'
    v_result := substr('abcdef', 2, 3);

    -- 2-arg: substr('abcdef', 3) → 'cdef'
    v_result := substr('abcdef', 3);

    -- substring FROM/FOR syntax
    v_result := substring('abcdef' FROM 2 FOR 3);

    -- substr in IF condition
    IF substr('abc', 1, 2) = 'ab' THEN
        v_result := 'matched';
    END IF;

    -- ================================================================
    -- 2. 字符串函数  →  FunctionCall
    -- ================================================================
    v_result := upper('hello');                        -- → toUpperCase()
    v_result := lower('HELLO');                        -- → toLowerCase()
    v_result := trim('  hello  ');                     -- → trim()
    v_result := length('hello');                       -- → length()
    v_result := replace('hello', 'l', 'L');            -- → replace()
    v_result := instr('hello', 'll');                  -- → indexOf() + 1
    v_result := concat('a', 'b');                      -- → concat / format
    v_result := lpad('abc', 5, '0');                   -- → String.format 左填充
    v_result := rpad('abc', 5, '0');                   -- → String.format 右填充
    v_result := ltrim('  hello');                      -- → replaceAll 去左空格
    v_result := rtrim('hello  ');                      -- → replaceAll 去右空格
    v_result := chr(65);                               -- → (char) 强转
    v_result := ascii('A');                            -- → (int) charAt(0)

    -- ================================================================
    -- 3. 数学函数  →  FunctionCall
    -- ================================================================
    v_amount := abs(-10);                              -- → Math.abs()
    v_amount := ceil(3.14);                            -- → Math.ceil()
    v_amount := floor(3.14);                           -- → Math.floor()
    v_amount := round(3.1415);                         -- → Math.round()
    v_amount := trunc(3.14);                           -- → Math.floor()
    v_amount := mod(10, 3);                            -- → % 运算
    v_amount := power(2, 3);                           -- → Math.pow()
    v_amount := sign(-5);                              -- → Integer.signum()

    -- ================================================================
    -- 4. 空值处理  →  FunctionCall
    -- ================================================================
    v_result := nvl(v_result, 'default');              -- → 三元: != null ? x : y
    v_result := coalesce(v_result, 'fallback');        -- → Objects.requireNonNullElse

    -- ================================================================
    -- 5. 类型转换  →  FunctionCall
    -- ================================================================
    v_result := to_char(123);                          -- → String.valueOf()
    v_amount := to_number('123');                      -- → Long.valueOf()

    -- ================================================================
    -- 6. 嵌套函数调用
    -- ================================================================
    v_result := upper(substr('abcdef', 1, 3));         -- 内层 SpecialFunction + 外层 FunctionCall
    v_result := nvl(trim(v_result), 'empty');          -- 两层 FunctionCall

    -- ================================================================
    -- 7. 函数在条件判断中的使用
    -- ================================================================
    IF upper(v_result) = 'ABC' THEN
        v_amount := 1;
    ELSIF length(v_result) > 5 THEN
        v_amount := 2;
    ELSIF instr(v_result, 'x') > 0 THEN
        v_amount := 3;
    END IF;

    -- ================================================================
    -- 8. 函数与表列/变量组合
    -- ================================================================
    IF substr(p_input, 1, 1) = 'A' THEN
        v_result := upper(p_input);
    END IF;
END;
$$ LANGUAGE plpgsql;
