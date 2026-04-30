CREATE OR REPLACE PROCEDURE search_target(
    OUT result INT
) AS $$
DECLARE
    i INT; j INT;
BEGIN
    result := -1;

    FOR i IN 1..100 LOOP
        FOR j IN 1..100 LOOP
            IF some_condition(i, j) THEN
                result := i * 1000 + j;
                GOTO found_it;  -- 跳出两层
            END IF;
        END LOOP;
    END LOOP;

    RAISE NOTICE 'Not found';
    RETURN;

<<found_it>>
    RAISE NOTICE 'Found at %', result;
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE PROCEDURE process_data() AS $$
DECLARE
    cur CURSOR FOR SELECT * FROM src_table;
    rec RECORD;
    batch_count INT := 0;
BEGIN
    OPEN cur;
    LOOP
        FETCH cur INTO rec;
        IF NOT FOUND THEN
            GOTO done;  -- 正常结束
        END IF;

        IF rec.status = 'INVALID' THEN
            RAISE NOTICE 'Skip invalid id=%', rec.id;
            GOTO next_iter;  -- 跳过本次
        END IF;

        -- 复杂处理，可能出错
        BEGIN
            INSERT INTO tgt_table VALUES (rec.*);
            batch_count := batch_count + 1;
        EXCEPTION WHEN unique_violation THEN
            RAISE NOTICE 'Duplicate id=%', rec.id;
            GOTO cleanup;  -- 错误退出，需关游标
        END;

        <<next_iter>>
        CONTINUE;  -- 实际上这里用 CONTINUE 更好，但存过用了 GOTO
    END LOOP;

<<cleanup>>
    CLOSE cur;
    RAISE EXCEPTION 'Aborted at count=%', batch_count;

<<done>>
    CLOSE cur;
    RAISE NOTICE 'Completed count=%', batch_count;
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE PROCEDURE parse_cmd(input TEXT) AS $$
DECLARE
    pos INT := 1;
    tok TEXT;
BEGIN
<<read_tok>>
    tok := substr(input, pos, 1);
    pos := pos + 1;

    IF tok = '$' THEN
        GOTO handle_var;  -- 跳到变量处理
    ELSIF tok = ';' THEN
        GOTO done;       -- 结束
    ELSE
        GOTO read_tok;   -- 继续读下一个
    END IF;

<<handle_var>>
    -- 处理变量...
    GOTO read_tok;       -- 回读到主循环

<<done>>
    RAISE NOTICE 'Parsing done';
END;
$$ LANGUAGE plpgsql;
