CREATE OR REPLACE PACKAGE issue_144_while_date_arith IS
    -- Issue #144 batch 4e: WHILE 循环条件里的日期/数值运算族。
    -- PL/SQL 中 DATE ± VARCHAR2 依赖隐式转换合法；Java 侧三处残余：
    --  a) to_date(...) - varchar2_param → ((Number)(String)).longValue() 非法转型
    --  b) date_var + varchar2_param → 落到 String 拼接，Date 变量接收 String 报错
    --  c) record.NUMBER字段 + nvl(record.NUMBER字段,0) → 字段访问统一
    --     .doubleValue() 解箱后 is_bd 分支仍按 BigDecimal 发 .add( → double.add
    TYPE deposit_receipt IS RECORD (
        amount      NUMBER,
        draw_amount NUMBER
    );
    FUNCTION get_section_begin_date(
        p_i_start_date        IN VARCHAR2,
        p_i_end_date          IN VARCHAR2,
        p_i_settle_frequency  IN VARCHAR2,
        p_i_settle_freq_count IN VARCHAR2 DEFAULT '1'
    ) RETURN VARCHAR2;
    PROCEDURE prc_interest_by_amount(
        p_i_deposit_receipt IN deposit_receipt,
        p_i_amount          IN VARCHAR2,
        p_o_accrual_value   OUT NUMBER
    );
END issue_144_while_date_arith;
/

CREATE OR REPLACE PACKAGE BODY issue_144_while_date_arith IS
    FUNCTION get_section_begin_date(
        p_i_start_date        IN VARCHAR2,
        p_i_end_date          IN VARCHAR2,
        p_i_settle_frequency  IN VARCHAR2,
        p_i_settle_freq_count IN VARCHAR2 DEFAULT '1'
    ) RETURN VARCHAR2 IS
        v_section_start_day DATE := '';
        v_start_date        VARCHAR2(8) := p_i_start_date;
    BEGIN
        IF p_i_start_date > p_i_end_date THEN
            RETURN '0';
        ELSE
            CASE p_i_settle_frequency
                WHEN '5' THEN
                    -- 数字字面量日期加减（对照组，现有路径已编译通过）
                    v_section_start_day := to_date(p_i_start_date, 'yyyymmdd');
                    WHILE v_section_start_day <=
                          to_date(p_i_end_date, 'yyyymmdd') - 7
                    LOOP
                        v_section_start_day := v_section_start_day + 7;
                    END LOOP;
                WHEN '6' THEN
                    -- VARCHAR2 参数作为天数：DATE - 'n' / DATE + 'n'
                    v_section_start_day := to_date(p_i_start_date, 'yyyymmdd');
                    WHILE v_section_start_day <=
                          to_date(p_i_end_date, 'yyyymmdd') - p_i_settle_freq_count
                    LOOP
                        v_section_start_day := v_section_start_day + p_i_settle_freq_count;
                    END LOOP;
                ELSE
                    RETURN p_i_start_date;
            END CASE;
            RETURN to_char(v_section_start_day, 'yyyymmdd');
        END IF;
    END;

    PROCEDURE prc_interest_by_amount(
        p_i_deposit_receipt IN deposit_receipt,
        p_i_amount          IN VARCHAR2,
        p_o_accrual_value   OUT NUMBER
    ) IS
        v_tmp NUMBER;
    BEGIN
        -- record.NUMBER 字段 + nvl(record.NUMBER 字段, 0)：
        -- 字段访问统一 .doubleValue() 解箱，但 is_bd 仍按 BigDecimal 发 .add(
        v_tmp := round(p_i_amount / (p_i_deposit_receipt.amount +
                                     nvl(p_i_deposit_receipt.draw_amount, 0)) * 2, 2);
        p_o_accrual_value := v_tmp;
    END;
END issue_144_while_date_arith;
/
