CREATE OR REPLACE PACKAGE issue_144_compareto_num IS
    -- Issue #144 batch 4d goal_b: compareTo 分支的数值适配缺口（004 报告
    -- 的相邻项）。BigDecimal 与数值字段对【字符串字面量】比较会走
    -- is_bd compareTo 分支但不做 String→BigDecimal 适配；String 变量
    -- 对数值表达式走 is_str compareTo 分支产生 String.compareTo(Long)
    -- 等不成立形态。
    PROCEDURE prc_compareto_num;
END issue_144_compareto_num;
/

CREATE OR REPLACE PACKAGE BODY issue_144_compareto_num IS
    PROCEDURE prc_compareto_num IS
        v_bd    NUMERIC;
        v_num   NUMBER;
        v_txt   VARCHAR2(100);
        v_flag  BOOLEAN;
    BEGIN
        v_bd  := 1.5;
        v_num := 2;
        v_txt := '10';
        -- BigDecimal 字段 vs 数字字符串字面量（is_bd compareTo 分支）
        IF v_bd > '2.5' THEN
            v_flag := TRUE;
        END IF;
        -- 反向：字符串字面量 vs BigDecimal 字段
        IF '0.5' < v_bd THEN
            v_flag := TRUE;
        END IF;
        -- BigDecimal 字段 vs String 变量
        IF v_bd >= v_txt THEN
            v_flag := TRUE;
        END IF;
        -- String 变量 vs 数值变量（is_str compareTo 分支，非字面量）
        IF v_txt > v_num THEN
            v_flag := TRUE;
        END IF;
        -- 数值变量 vs String 变量
        IF v_num < v_txt THEN
            v_flag := TRUE;
        END IF;
        -- = / <> 形态：String 变量 vs 数值变量（Long 分支 parseLong()
        -- primitive 解引用缺口）
        IF v_txt = v_num THEN
            v_flag := TRUE;
        END IF;
        IF v_num <> v_txt THEN
            v_flag := TRUE;
        END IF;
        -- String 变量 vs 数字字面量（对照：应走数值适配）
        IF v_txt >= '3' THEN
            v_flag := TRUE;
        END IF;
        -- String 字面量 vs 数值变量（对照：既有路径不受影响）
        IF '7' < v_num THEN
            v_flag := TRUE;
        END IF;
        NULL;
    END;
END issue_144_compareto_num;
/
