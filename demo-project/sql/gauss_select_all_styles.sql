-- NOTE: DDL moved to ddl/*.sql



CREATE OR REPLACE PACKAGE pkg_select_styles AS
    PROCEDURE demo_01_basic_select;
    PROCEDURE demo_02_alias;
    PROCEDURE demo_03_distinct;
    PROCEDURE demo_04_where_operators;
    PROCEDURE demo_05_logical_operators;
    PROCEDURE demo_06_between_in_like;
    PROCEDURE demo_07_is_null;
    PROCEDURE demo_08_order_by;
    PROCEDURE demo_09_limit_offset;
    PROCEDURE demo_10_aggregate;
    PROCEDURE demo_11_group_by;
    PROCEDURE demo_12_having;
    PROCEDURE demo_13_join_inner;
    PROCEDURE demo_14_join_outer;
    PROCEDURE demo_15_join_self;
    PROCEDURE demo_16_join_cross;
    PROCEDURE demo_17_join_natural;
    PROCEDURE demo_18_join_lateral;
    PROCEDURE demo_19_subquery_scalar;
    PROCEDURE demo_20_subquery_correlated;
    PROCEDURE demo_21_subquery_in;
    PROCEDURE demo_22_subquery_exists;
    PROCEDURE demo_23_subquery_all_any;
    PROCEDURE demo_24_cte_simple;
    PROCEDURE demo_25_cte_recursive;
    PROCEDURE demo_26_cte_multiple;
    PROCEDURE demo_27_window_rank;
    PROCEDURE demo_28_window_aggregate;
    PROCEDURE demo_29_window_lead_lag;
    PROCEDURE demo_30_window_first_last;
    PROCEDURE demo_31_window_frame;
    PROCEDURE demo_32_union_intersect_except;
    PROCEDURE demo_33_case_expression;
    PROCEDURE demo_34_coalesce_nvl;
    PROCEDURE demo_35_cast_convert;
    PROCEDURE demo_36_string_functions;
    PROCEDURE demo_37_date_functions;
    PROCEDURE demo_38_math_functions;
    PROCEDURE demo_39_conditional_agg;
    PROCEDURE demo_40_pivot_manual;
    PROCEDURE demo_41_unpivot_manual;
    PROCEDURE demo_42_unpivot_lateral;
    PROCEDURE demo_43_json_functions;
    PROCEDURE demo_44_array_agg;
    PROCEDURE demo_45_generate_series;
    PROCEDURE demo_46_values_clause;
    PROCEDURE demo_47_select_into;
    PROCEDURE demo_48_for_update;
    PROCEDURE demo_49_complex_nested;
    PROCEDURE demo_50_comprehensive;

    PROCEDURE proc_log_result(p_demo VARCHAR2, p_desc VARCHAR2, p_count INTEGER);
    PROCEDURE proc_show_results;
END pkg_select_styles;
/

CREATE OR REPLACE PACKAGE BODY pkg_select_styles AS

    PROCEDURE proc_log_result(p_demo VARCHAR2, p_desc VARCHAR2, p_count INTEGER) IS
    BEGIN
        INSERT INTO result_log (log_id, demo_name, result_desc, row_count)
        VALUES (seq_result.NEXTVAL, p_demo, p_desc, p_count);
    END proc_log_result;

    PROCEDURE proc_show_results IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('=== All Demo Results ===');
        FOR r IN (SELECT * FROM result_log ORDER BY log_id) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.demo_name, 30) || ' | Rows:' || LPAD(r.row_count, 4) || ' | ' || r.result_desc
            );
        END LOOP;
    END proc_show_results;

    -- ========== 1. 基础 SELECT ==========
    PROCEDURE demo_01_basic_select IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 1: Basic SELECT ---');

        SELECT COUNT(*) INTO v_count FROM employees;
        proc_log_result('01_basic', 'SELECT * FROM employees', v_count);

        FOR r IN (SELECT emp_id, emp_name FROM employees WHERE ROWNUM <= 3) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_id || ' | ' || r.emp_name);
        END LOOP;
    END demo_01_basic_select;

    -- ========== 2. 列别名 / 表别名 ==========
    PROCEDURE demo_02_alias IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 2: Column & Table Aliases ---');

        SELECT COUNT(*) INTO v_count FROM (
            SELECT
                e.emp_id AS id,
                e.emp_name "Employee Name",
                e.base_salary + e.base_salary * e.bonus_pct total_comp,
                d.dept_name dept
            FROM employees e
            JOIN departments d ON e.dept_id = d.dept_id
            WHERE ROWNUM <= 3
        );
        proc_log_result('02_alias', 'Column aliases (AS, double-quote, implicit)', v_count);

        FOR r IN (
            SELECT
                e.emp_id AS id,
                e.emp_name "Employee Name",
                e.base_salary + e.base_salary * e.bonus_pct total_comp,
                d.dept_name dept
            FROM employees e
            JOIN departments d ON e.dept_id = d.dept_id
            WHERE ROWNUM <= 3
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(r.id || ' | ' || r."Employee Name" || ' | ' || r.total_comp || ' | ' || r.dept);
        END LOOP;
    END demo_02_alias;

    -- ========== 3. DISTINCT / DISTINCT ON ==========
    PROCEDURE demo_03_distinct IS
        v_count1 INTEGER;
        v_count2 INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 3: DISTINCT / DISTINCT ON ---');

        SELECT COUNT(DISTINCT dept_id) INTO v_count1 FROM employees;
        proc_log_result('03_distinct', 'DISTINCT dept_id count', v_count1);

        SELECT COUNT(*) INTO v_count2 FROM (
            SELECT DISTINCT ON (dept_id) emp_id, emp_name, dept_id
            FROM employees
            ORDER BY dept_id, base_salary DESC
        );
        proc_log_result('03_distinct_on', 'DISTINCT ON (dept_id) keep highest salary', v_count2);

        FOR r IN (
            SELECT DISTINCT ON (dept_id) emp_id, emp_name, dept_id, base_salary
            FROM employees
            ORDER BY dept_id, base_salary DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('Dept ' || r.dept_id || ' top earner: ' || r.emp_name || ' (' || r.base_salary || ')');
        END LOOP;
    END demo_03_distinct;

    -- ========== 4. WHERE 各种运算符 ==========
    PROCEDURE demo_04_where_operators IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 4: WHERE Operators ---');

        SELECT COUNT(*) INTO v_count FROM employees WHERE base_salary > 10000;
        proc_log_result('04_where', 'salary > 10000', v_count);

        SELECT COUNT(*) INTO v_count FROM employees WHERE emp_name LIKE '张%';
        DBE_OUTPUT.PRINT_LINE('Name starts with 张: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees WHERE hire_date >= '2020-01-01';
        DBE_OUTPUT.PRINT_LINE('Hired since 2020: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees WHERE emp_name ~ '^[张李王]';
        DBE_OUTPUT.PRINT_LINE('Name regex ^[张李王]: ' || v_count);
    END demo_04_where_operators;

    -- ========== 5. AND / OR / NOT ==========
    PROCEDURE demo_05_logical_operators IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 5: Logical Operators ---');

        SELECT COUNT(*) INTO v_count FROM employees
        WHERE dept_id = 10 AND base_salary > 7000 AND status = 'ACTIVE';
        DBE_OUTPUT.PRINT_LINE('Dept10 AND salary>7000 AND active: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees
        WHERE dept_id = 10 OR dept_id = 20;
        DBE_OUTPUT.PRINT_LINE('Dept10 OR Dept20: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees
        WHERE NOT (status = 'INACTIVE');
        DBE_OUTPUT.PRINT_LINE('NOT inactive (i.e., active): ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees
        WHERE dept_id IN (10, 20) AND (base_salary > 10000 OR bonus_pct > 0.10);
        DBE_OUTPUT.PRINT_LINE('Complex AND/OR: ' || v_count);
        proc_log_result('05_logic', 'AND/OR/NOT combinations', v_count);
    END demo_05_logical_operators;

    -- ========== 6. BETWEEN / IN / LIKE / ILIKE ==========
    PROCEDURE demo_06_between_in_like IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 6: BETWEEN / IN / LIKE / ILIKE ---');

        SELECT COUNT(*) INTO v_count FROM employees WHERE base_salary BETWEEN 7000 AND 10000;
        DBE_OUTPUT.PRINT_LINE('Salary BETWEEN 7000 AND 10000: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees WHERE hire_date NOT BETWEEN '2020-01-01' AND '2023-01-01';
        DBE_OUTPUT.PRINT_LINE('Hire NOT BETWEEN 2020-2023: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees WHERE dept_id IN (10, 20, 30);
        DBE_OUTPUT.PRINT_LINE('Dept IN (10,20,30): ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees WHERE dept_id NOT IN (10, 20);
        DBE_OUTPUT.PRINT_LINE('Dept NOT IN (10,20): ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees WHERE emp_name LIKE '张%';
        DBE_OUTPUT.PRINT_LINE('Name LIKE 张%: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees WHERE emp_name LIKE '_三';
        DBE_OUTPUT.PRINT_LINE('Name LIKE _三: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees WHERE email ILIKE '%@HX.COM';
        DBE_OUTPUT.PRINT_LINE('Email ILIKE %@HX.COM: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees WHERE phone SIMILAR TO '1380013800[1-6]';
        DBE_OUTPUT.PRINT_LINE('Phone SIMILAR TO pattern: ' || v_count);

        proc_log_result('06_between_in_like', 'BETWEEN/IN/LIKE/ILIKE/SIMILAR TO', v_count);
    END demo_06_between_in_like;

    -- ========== 7. IS NULL / IS NOT NULL ==========
    PROCEDURE demo_07_is_null IS
        v_count1 INTEGER;
        v_count2 INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 7: IS NULL / IS NOT NULL ---');

        SELECT COUNT(*) INTO v_count1 FROM employees WHERE manager_id IS NULL;
        DBE_OUTPUT.PRINT_LINE('No manager (NULL): ' || v_count1);

        SELECT COUNT(*) INTO v_count2 FROM employees WHERE manager_id IS NOT NULL;
        DBE_OUTPUT.PRINT_LINE('Has manager: ' || v_count2);

        SELECT COUNT(*) INTO v_count1 FROM employees
        WHERE manager_id IS DISTINCT FROM 1001;
        DBE_OUTPUT.PRINT_LINE('Manager IS DISTINCT FROM 1001: ' || v_count1);

        proc_log_result('07_null', 'IS NULL / IS NOT NULL / IS DISTINCT FROM', v_count1 + v_count2);
    END demo_07_is_null;

    -- ========== 8. ORDER BY 多列 / NULLS FIRST/LAST ==========
    PROCEDURE demo_08_order_by IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 8: ORDER BY ---');

        SELECT COUNT(*) INTO v_count FROM employees;

        DBE_OUTPUT.PRINT_LINE('Order by dept ASC, salary DESC:');
        FOR r IN (
            SELECT emp_id, emp_name, dept_id, base_salary
            FROM employees
            ORDER BY dept_id ASC, base_salary DESC
            FETCH FIRST 5 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(r.dept_id || ' | ' || RPAD(r.emp_name, 8) || ' | ' || r.base_salary);
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Order by manager_id NULLS FIRST:');
        FOR r IN (
            SELECT emp_id, emp_name, manager_id
            FROM employees
            ORDER BY manager_id NULLS FIRST
            FETCH FIRST 5 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_id || ' | ' || RPAD(r.emp_name, 8) || ' | Manager:' || NVL(TO_CHAR(r.manager_id), 'NULL'));
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Order by column position (2nd col):');
        FOR r IN (
            SELECT emp_id, emp_name FROM employees ORDER BY 2 FETCH FIRST 3 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_id || ' | ' || r.emp_name);
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Order by expression (salary * (1+bonus)):');
        FOR r IN (
            SELECT emp_id, emp_name, base_salary * (1 + bonus_pct) AS total
            FROM employees
            ORDER BY base_salary * (1 + bonus_pct) DESC
            FETCH FIRST 3 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_id || ' | ' || RPAD(r.emp_name, 8) || ' | Total:' || r.total);
        END LOOP;

        proc_log_result('08_order_by', 'Multi-col, NULLS FIRST/LAST, position, expression', v_count);
    END demo_08_order_by;

    -- ========== 9. LIMIT / OFFSET / FETCH FIRST ==========
    PROCEDURE demo_09_limit_offset IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 9: LIMIT / OFFSET / FETCH FIRST ---');

        DBE_OUTPUT.PRINT_LINE('LIMIT 3:');
        FOR r IN (SELECT emp_id, emp_name FROM employees ORDER BY emp_id LIMIT 3) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_id || ' | ' || r.emp_name);
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('LIMIT 3 OFFSET 5:');
        FOR r IN (SELECT emp_id, emp_name FROM employees ORDER BY emp_id LIMIT 3 OFFSET 5) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_id || ' | ' || r.emp_name);
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('FETCH FIRST 3 ROWS ONLY:');
        FOR r IN (SELECT emp_id, emp_name FROM employees ORDER BY emp_id FETCH FIRST 3 ROWS ONLY) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_id || ' | ' || r.emp_name);
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('FETCH FIRST 3 ROWS WITH TIES:');
        FOR r IN (
            SELECT emp_id, emp_name, dept_id
            FROM employees
            ORDER BY dept_id
            FETCH FIRST 3 ROWS WITH TIES
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_id || ' | ' || r.emp_name || ' | Dept:' || r.dept_id);
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('OFFSET 5 ROWS FETCH NEXT 3 ROWS ONLY:');
        FOR r IN (
            SELECT emp_id, emp_name FROM employees ORDER BY emp_id OFFSET 5 ROWS FETCH NEXT 3 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_id || ' | ' || r.emp_name);
        END LOOP;

        proc_log_result('09_limit', 'LIMIT/OFFSET/FETCH FIRST/WITH TIES', 0);
    END demo_09_limit_offset;

    -- ========== 10. 聚合函数 ==========
    PROCEDURE demo_10_aggregate IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 10: Aggregate Functions ---');

        FOR r IN (
            SELECT
                COUNT(*) AS cnt,
                COUNT(DISTINCT dept_id) AS dept_cnt,
                SUM(base_salary) AS total_sal,
                AVG(base_salary) AS avg_sal,
                MAX(base_salary) AS max_sal,
                MIN(base_salary) AS min_sal,
                STDDEV(base_salary) AS std_sal,
                VARIANCE(base_salary) AS var_sal,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY base_salary) AS median_sal
            FROM employees
            WHERE status = 'ACTIVE'
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('Count: ' || r.cnt);
            DBE_OUTPUT.PRINT_LINE('Distinct depts: ' || r.dept_cnt);
            DBE_OUTPUT.PRINT_LINE('Total salary: ' || r.total_sal);
            DBE_OUTPUT.PRINT_LINE('Avg salary: ' || ROUND(r.avg_sal, 2));
            DBE_OUTPUT.PRINT_LINE('Max/Min: ' || r.max_sal || ' / ' || r.min_sal);
            DBE_OUTPUT.PRINT_LINE('StdDev: ' || ROUND(r.std_sal, 2));
            DBE_OUTPUT.PRINT_LINE('Median: ' || r.median_sal);
        END LOOP;

        proc_log_result('10_aggregate', 'COUNT/SUM/AVG/MAX/MIN/STDDEV/VARIANCE/PERCENTILE_CONT', 1);
    END demo_10_aggregate;

    -- ========== 11. GROUP BY / ROLLUP / CUBE / GROUPING SETS ==========
    PROCEDURE demo_11_group_by IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 11: GROUP BY / ROLLUP / CUBE ---');

        DBE_OUTPUT.PRINT_LINE('GROUP BY dept_id:');
        FOR r IN (
            SELECT dept_id, COUNT(*) AS cnt, AVG(base_salary) AS avg_sal
            FROM employees
            WHERE status = 'ACTIVE'
            GROUP BY dept_id
            ORDER BY dept_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('Dept ' || r.dept_id || ' | Count:' || r.cnt || ' | Avg:' || ROUND(r.avg_sal, 2));
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('ROLLUP(dept_id, status):');
        FOR r IN (
            SELECT dept_id, status, COUNT(*) AS cnt, SUM(base_salary) AS total
            FROM employees
            GROUP BY ROLLUP(dept_id, status)
            ORDER BY dept_id, status
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                NVL(TO_CHAR(r.dept_id), 'ALL') || ' | ' ||
                NVL(r.status, 'ALL') || ' | Count:' || r.cnt || ' | Total:' || r.total
            );
        END LOOP;

        SELECT COUNT(*) INTO v_count FROM (
            SELECT dept_id, status, COUNT(*) AS cnt
            FROM employees
            GROUP BY CUBE(dept_id, status)
        );
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('CUBE generated ' || v_count || ' groups');

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('GROUPING SETS:');
        FOR r IN (
            SELECT dept_id, status, COUNT(*) AS cnt
            FROM employees
            GROUP BY GROUPING SETS (dept_id, status, ())
            ORDER BY dept_id, status
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                NVL(TO_CHAR(r.dept_id), '-') || ' | ' ||
                NVL(r.status, '-') || ' | Count:' || r.cnt
            );
        END LOOP;

        proc_log_result('11_group_by', 'GROUP BY / ROLLUP / CUBE / GROUPING SETS', v_count);
    END demo_11_group_by;

    -- ========== 12. HAVING ==========
    PROCEDURE demo_12_having IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 12: HAVING ---');

        FOR r IN (
            SELECT dept_id, COUNT(*) AS cnt, AVG(base_salary) AS avg_sal
            FROM employees
            GROUP BY dept_id
            HAVING COUNT(*) >= 2
               AND AVG(base_salary) > 7000
            ORDER BY avg_sal DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                'Dept ' || r.dept_id || ' | Count:' || r.cnt ||
                ' | Avg:' || ROUND(r.avg_sal, 2) || ' (>=2 people, avg>7000)'
            );
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('HAVING with subquery:');
        FOR r IN (
            SELECT dept_id, MAX(base_salary) AS max_sal
            FROM employees
            GROUP BY dept_id
            HAVING MAX(base_salary) > (SELECT AVG(base_salary) FROM employees)
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('Dept ' || r.dept_id || ' max (' || r.max_sal || ') > company avg');
        END LOOP;

        proc_log_result('12_having', 'HAVING with aggregates and subqueries', 0);
    END demo_12_having;

    -- ========== 13. INNER JOIN ==========
    PROCEDURE demo_13_join_inner IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 13: INNER JOIN ---');

        SELECT COUNT(*) INTO v_count
        FROM employees e
        INNER JOIN departments d ON e.dept_id = d.dept_id;
        DBE_OUTPUT.PRINT_LINE('INNER JOIN count: ' || v_count);

        SELECT COUNT(*) INTO v_count
        FROM employees e, departments d
        WHERE e.dept_id = d.dept_id;
        DBE_OUTPUT.PRINT_LINE('Comma join count: ' || v_count);

        FOR r IN (
            SELECT e.emp_name, d.dept_name, p.perf_grade, p.perf_score
            FROM employees e
            INNER JOIN departments d ON e.dept_id = d.dept_id
            INNER JOIN emp_performance p ON e.emp_id = p.emp_id
            WHERE p.perf_year = 2024 AND p.perf_quarter = 1
              AND ROWNUM <= 5
            ORDER BY p.perf_score DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | ' || RPAD(r.dept_name, 8) ||
                ' | Grade:' || r.perf_grade || ' | Score:' || r.perf_score
            );
        END LOOP;

        proc_log_result('13_inner_join', 'INNER JOIN, comma join, multi-table join', v_count);
    END demo_13_join_inner;

    -- ========== 14. LEFT / RIGHT / FULL JOIN ==========
    PROCEDURE demo_14_join_outer IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 14: LEFT / RIGHT / FULL JOIN ---');

        DBE_OUTPUT.PRINT_LINE('LEFT JOIN (all employees, with/without dept):');
        FOR r IN (
            SELECT e.emp_name, NVL(d.dept_name, 'NO DEPT') AS dept
            FROM employees e
            LEFT JOIN departments d ON e.dept_id = d.dept_id
            ORDER BY e.emp_id
            FETCH FIRST 5 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_name || ' | ' || r.dept);
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('RIGHT JOIN (all depts, with/without employees):');
        FOR r IN (
            SELECT NVL(e.emp_name, 'NO EMP') AS emp, d.dept_name
            FROM employees e
            RIGHT JOIN departments d ON e.dept_id = d.dept_id
            ORDER BY d.dept_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(RPAD(r.emp, 10) || ' | ' || r.dept_name);
        END LOOP;

        SELECT COUNT(*) INTO v_count
        FROM employees e
        FULL JOIN departments d ON e.dept_id = d.dept_id;
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('FULL JOIN row count: ' || v_count);

        proc_log_result('14_outer_join', 'LEFT/RIGHT/FULL JOIN', v_count);
    END demo_14_join_outer;

    -- ========== 15. 自连接 ==========
    PROCEDURE demo_15_join_self IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 15: Self JOIN ---');

        FOR r IN (
            SELECT
                e.emp_name AS employee,
                m.emp_name AS manager,
                e.base_salary AS emp_sal,
                m.base_salary AS mgr_sal
            FROM employees e
            LEFT JOIN employees m ON e.manager_id = m.emp_id
            WHERE e.manager_id IS NOT NULL
            ORDER BY e.emp_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.employee, 8) || ' reports to ' || RPAD(r.manager, 8) ||
                ' | Emp:' || r.emp_sal || ' | Mgr:' || r.mgr_sal
            );
        END LOOP;

        SELECT COUNT(*) INTO v_count
        FROM employees e1
        JOIN employees e2 ON e1.dept_id = e2.dept_id AND e1.emp_id < e2.emp_id;
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Same-dept pairs (self non-equi join): ' || v_count);

        proc_log_result('15_self_join', 'Self JOIN for hierarchy and pairs', v_count);
    END demo_15_join_self;

    -- ========== 16. CROSS JOIN ==========
    PROCEDURE demo_16_join_cross IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 16: CROSS JOIN ---');

        SELECT COUNT(*) INTO v_count
        FROM employees CROSS JOIN departments;
        DBE_OUTPUT.PRINT_LINE('CROSS JOIN count (employees * depts): ' || v_count);

        FOR r IN (
            SELECT e.emp_name, d.dept_name,
                   CASE WHEN e.dept_id = d.dept_id THEN 'CURRENT' ELSE 'OTHER' END AS match
            FROM employees e
            CROSS JOIN departments d
            WHERE e.emp_id <= 1003
              AND d.dept_id <= 30
            ORDER BY e.emp_name, d.dept_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(RPAD(r.emp_name, 8) || ' | ' || RPAD(r.dept_name, 8) || ' | ' || r.match);
        END LOOP;

        proc_log_result('16_cross_join', 'CROSS JOIN Cartesian product', v_count);
    END demo_16_join_cross;

    -- ========== 17. NATURAL JOIN ==========
    PROCEDURE demo_17_join_natural IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 17: NATURAL JOIN ---');

        SELECT COUNT(*) INTO v_count
        FROM employees NATURAL JOIN departments;
        DBE_OUTPUT.PRINT_LINE('NATURAL JOIN count (auto dept_id match): ' || v_count);

        FOR r IN (
            SELECT emp_id, emp_name, dept_name, location
            FROM employees NATURAL JOIN departments
            FETCH FIRST 3 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_id || ' | ' || r.emp_name || ' | ' || r.dept_name || ' | ' || r.location);
        END LOOP;

        proc_log_result('17_natural_join', 'NATURAL JOIN auto column matching', v_count);
    END demo_17_join_natural;

    -- ========== 18. LATERAL JOIN ==========
    PROCEDURE demo_18_join_lateral IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 18: LATERAL JOIN ---');

        FOR r IN (
            SELECT d.dept_name, e.emp_name, e.base_salary
            FROM departments d
            LEFT JOIN LATERAL (
                SELECT emp_name, base_salary
                FROM employees
                WHERE dept_id = d.dept_id
                ORDER BY base_salary DESC
                LIMIT 2
            ) e ON TRUE
            WHERE d.is_active = 1
            ORDER BY d.dept_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.dept_name, 8) || ' | ' ||
                NVL(RPAD(r.emp_name, 8), 'NO EMP') || ' | ' ||
                NVL(TO_CHAR(r.base_salary), '-')
            );
        END LOOP;

        proc_log_result('18_lateral_join', 'LATERAL JOIN with correlated subquery', 0);
    END demo_18_join_lateral;

    -- ========== 19. 标量子查询 ==========
    PROCEDURE demo_19_subquery_scalar IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 19: Scalar Subquery ---');

        FOR r IN (
            SELECT
                emp_id,
                emp_name,
                base_salary,
                (SELECT AVG(base_salary) FROM employees) AS company_avg,
                base_salary - (SELECT AVG(base_salary) FROM employees) AS diff,
                (SELECT dept_name FROM departments d WHERE d.dept_id = e.dept_id) AS dept_name
            FROM employees e
            WHERE status = 'ACTIVE'
            ORDER BY emp_id
            FETCH FIRST 5 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Sal:' || r.base_salary ||
                ' | Avg:' || ROUND(r.company_avg, 2) ||
                ' | Diff:' || ROUND(r.diff, 2) ||
                ' | Dept:' || r.dept_name
            );
        END LOOP;

        proc_log_result('19_scalar_subq', 'Scalar subquery in SELECT list', 0);
    END demo_19_subquery_scalar;

    -- ========== 20. 关联子查询 ==========
    PROCEDURE demo_20_subquery_correlated IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 20: Correlated Subquery ---');

        FOR r IN (
            SELECT
                e.emp_name,
                e.base_salary,
                (SELECT AVG(e2.base_salary)
                 FROM employees e2
                 WHERE e2.dept_id = e.dept_id) AS dept_avg,
                (SELECT COUNT(*)
                 FROM emp_performance p
                 WHERE p.emp_id = e.emp_id AND p.perf_year = 2024) AS perf_count
            FROM employees e
            WHERE e.status = 'ACTIVE'
            ORDER BY e.emp_id
            FETCH FIRST 5 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Sal:' || r.base_salary ||
                ' | DeptAvg:' || ROUND(r.dept_avg, 2) ||
                ' | PerfCount:' || r.perf_count
            );
        END LOOP;

        proc_log_result('20_correlated', 'Correlated subquery per row', 0);
    END demo_20_subquery_correlated;

    -- ========== 21. IN / NOT IN 子查询 ==========
    PROCEDURE demo_21_subquery_in IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 21: IN / NOT IN Subquery ---');

        SELECT COUNT(*) INTO v_count FROM employees
        WHERE dept_id IN (SELECT dept_id FROM departments WHERE budget > 4000000);
        DBE_OUTPUT.PRINT_LINE('Employees in high-budget depts (>4M): ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees
        WHERE emp_id NOT IN (SELECT emp_id FROM emp_performance WHERE perf_grade = 'A');
        DBE_OUTPUT.PRINT_LINE('Employees without grade A: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM emp_performance
        WHERE (emp_id, perf_year) IN (
            SELECT emp_id, 2024 FROM employees WHERE status = 'ACTIVE'
        );
        DBE_OUTPUT.PRINT_LINE('Multi-column IN match: ' || v_count);

        proc_log_result('21_in_subq', 'IN / NOT IN / multi-column IN', v_count);
    END demo_21_subquery_in;

    -- ========== 22. EXISTS / NOT EXISTS ==========
    PROCEDURE demo_22_subquery_exists IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 22: EXISTS / NOT EXISTS ---');

        SELECT COUNT(*) INTO v_count FROM employees e
        WHERE EXISTS (
            SELECT 1 FROM emp_performance p
            WHERE p.emp_id = e.emp_id AND p.perf_grade = 'A'
        );
        DBE_OUTPUT.PRINT_LINE('Employees with grade A performance: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees e
        WHERE NOT EXISTS (
            SELECT 1 FROM emp_projects ep
            WHERE ep.emp_id = e.emp_id
        );
        DBE_OUTPUT.PRINT_LINE('Employees with NO projects: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees e
        WHERE EXISTS (
            SELECT 1 FROM emp_performance p1
            WHERE p1.emp_id = e.emp_id AND p1.perf_grade = 'A'
        )
        AND EXISTS (
            SELECT 1 FROM emp_performance p2
            WHERE p2.emp_id = e.emp_id AND p2.perf_grade = 'D'
        );
        DBE_OUTPUT.PRINT_LINE('Employees with BOTH A and D grades: ' || v_count);

        proc_log_result('22_exists', 'EXISTS / NOT EXISTS / multiple EXISTS', v_count);
    END demo_22_subquery_exists;

    -- ========== 23. ALL / ANY / SOME ==========
    PROCEDURE demo_23_subquery_all_any IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 23: ALL / ANY / SOME ---');

        SELECT COUNT(*) INTO v_count FROM employees
        WHERE base_salary > ALL (
            SELECT base_salary FROM employees WHERE dept_id = 30
        );
        DBE_OUTPUT.PRINT_LINE('Salary > ALL dept 30 salaries: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees
        WHERE base_salary > ANY (
            SELECT base_salary FROM employees WHERE dept_id = 10
        );
        DBE_OUTPUT.PRINT_LINE('Salary > ANY dept 10 salary: ' || v_count);

        SELECT COUNT(*) INTO v_count FROM employees
        WHERE base_salary = SOME (
            SELECT base_salary FROM employees WHERE dept_id = 20
        );
        DBE_OUTPUT.PRINT_LINE('Salary = SOME dept 20 salary: ' || v_count);

        proc_log_result('23_all_any', 'ALL / ANY / SOME comparison', v_count);
    END demo_23_subquery_all_any;

    -- ========== 24. 简单 CTE (WITH) ==========
    PROCEDURE demo_24_cte_simple IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 24: Simple CTE ---');

        FOR r IN (
            WITH active_emps AS (
                SELECT emp_id, emp_name, dept_id, base_salary
                FROM employees
                WHERE status = 'ACTIVE'
            ),
            dept_avgs AS (
                SELECT dept_id, AVG(base_salary) AS avg_sal
                FROM active_emps
                GROUP BY dept_id
            )
            SELECT e.emp_name, e.base_salary, d.dept_name, a.avg_sal,
                   e.base_salary - a.avg_sal AS diff
            FROM active_emps e
            JOIN dept_avgs a ON e.dept_id = a.dept_id
            JOIN departments d ON e.dept_id = d.dept_id
            ORDER BY diff DESC
            FETCH FIRST 5 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Sal:' || r.base_salary ||
                ' | DeptAvg:' || ROUND(r.avg_sal, 2) ||
                ' | Diff:' || ROUND(r.diff, 2)
            );
        END LOOP;

        proc_log_result('24_cte_simple', 'Simple CTE with multiple WITH clauses', 0);
    END demo_24_cte_simple;

    -- ========== 25. 递归 CTE ==========
    PROCEDURE demo_25_cte_recursive IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 25: Recursive CTE ---');

        FOR r IN (
            WITH RECURSIVE emp_hierarchy AS (
                SELECT emp_id, emp_name, manager_id, 0 AS level,
                       emp_name AS path
                FROM employees
                WHERE manager_id IS NULL

                UNION ALL

                SELECT e.emp_id, e.emp_name, e.manager_id, h.level + 1,
                       h.path || ' -> ' || e.emp_name
                FROM employees e
                JOIN emp_hierarchy h ON e.manager_id = h.emp_id
            )
            SELECT emp_id, emp_name, level, path
            FROM emp_hierarchy
            ORDER BY level, emp_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                'Level ' || r.level || ' | ' || RPAD(r.emp_name, 8) || ' | ' || r.path
            );
        END LOOP;

        SELECT COUNT(*) INTO v_count FROM (
            WITH RECURSIVE emp_hierarchy AS (
                SELECT emp_id, emp_name, manager_id, 0 AS level
                FROM employees WHERE manager_id IS NULL
                UNION ALL
                SELECT e.emp_id, e.emp_name, e.manager_id, h.level + 1
                FROM employees e JOIN emp_hierarchy h ON e.manager_id = h.emp_id
            )
            SELECT * FROM emp_hierarchy
        );

        proc_log_result('25_cte_recursive', 'Recursive CTE for hierarchy', v_count);
    END demo_25_cte_recursive;

    -- ========== 26. 多 CTE ==========
    PROCEDURE demo_26_cte_multiple IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 26: Multiple CTEs ---');

        FOR r IN (
            WITH
            emp_base AS (
                SELECT emp_id, emp_name, dept_id, base_salary
                FROM employees WHERE status = 'ACTIVE'
            ),
            dept_avgs AS (
                SELECT dept_id, AVG(base_salary) AS avg_sal, COUNT(*) AS emp_count
                FROM emp_base GROUP BY dept_id
            ),
            perf_stats AS (
                SELECT emp_id, AVG(perf_score) AS avg_score, MAX(perf_grade) AS best_grade
                FROM emp_performance WHERE perf_year = 2024 GROUP BY emp_id
            )
            SELECT
                e.emp_name, e.base_salary, d.dept_name, a.avg_sal, p.avg_score,
                CASE WHEN e.base_salary > a.avg_sal AND p.avg_score > 85 THEN 'STAR'
                     WHEN e.base_salary > a.avg_sal THEN 'HIGH_SAL'
                     WHEN p.avg_score > 85 THEN 'HIGH_PERF'
                     ELSE 'NORMAL' END AS category
            FROM emp_base e
            JOIN dept_avgs a ON e.dept_id = a.dept_id
            JOIN departments d ON e.dept_id = d.dept_id
            LEFT JOIN perf_stats p ON e.emp_id = p.emp_id
            ORDER BY e.base_salary DESC
            FETCH FIRST 5 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Sal:' || r.base_salary ||
                ' | DeptAvg:' || ROUND(r.avg_sal, 2) ||
                ' | Score:' || ROUND(r.avg_score, 2) ||
                ' | ' || r.category
            );
        END LOOP;

        proc_log_result('26_cte_multi', 'Multiple chained CTEs', 0);
    END demo_26_cte_multiple;

    -- ========== 27. 窗口函数 RANK / DENSE_RANK / ROW_NUMBER ==========
    PROCEDURE demo_27_window_rank IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 27: Window Ranking Functions ---');

        FOR r IN (
            SELECT
                emp_name, dept_id, base_salary,
                ROW_NUMBER() OVER (ORDER BY base_salary DESC) AS rn,
                RANK() OVER (ORDER BY base_salary DESC) AS rnk,
                DENSE_RANK() OVER (ORDER BY base_salary DESC) AS drnk,
                RANK() OVER (PARTITION BY dept_id ORDER BY base_salary DESC) AS dept_rnk,
                NTILE(4) OVER (ORDER BY base_salary DESC) AS quartile
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY base_salary DESC
            FETCH FIRST 8 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Dept:' || r.dept_id ||
                ' | Sal:' || LPAD(r.base_salary, 6) ||
                ' | RN:' || LPAD(r.rn, 2) ||
                ' | Rank:' || LPAD(r.rnk, 2) ||
                ' | DRank:' || LPAD(r.drnk, 2) ||
                ' | DeptRnk:' || LPAD(r.dept_rnk, 2) ||
                ' | Q:' || r.quartile
            );
        END LOOP;

        proc_log_result('27_window_rank', 'ROW_NUMBER/RANK/DENSE_RANK/NTILE', 0);
    END demo_27_window_rank;

    -- ========== 28. 窗口聚合函数 ==========
    PROCEDURE demo_28_window_aggregate IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 28: Window Aggregate Functions ---');

        FOR r IN (
            SELECT
                emp_name, dept_id, base_salary,
                SUM(base_salary) OVER () AS total_salary,
                SUM(base_salary) OVER (PARTITION BY dept_id) AS dept_total,
                AVG(base_salary) OVER (PARTITION BY dept_id) AS dept_avg,
                COUNT(*) OVER (PARTITION BY dept_id) AS dept_count,
                base_salary / SUM(base_salary) OVER () AS pct_of_total,
                base_salary - AVG(base_salary) OVER (PARTITION BY dept_id) AS diff_from_avg
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY dept_id, base_salary DESC
            FETCH FIRST 8 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Dept:' || r.dept_id ||
                ' | Sal:' || LPAD(r.base_salary, 6) ||
                ' | DeptTotal:' || r.dept_total ||
                ' | Pct:' || ROUND(r.pct_of_total * 100, 2) || '%' ||
                ' | DiffAvg:' || ROUND(r.diff_from_avg, 2)
            );
        END LOOP;

        proc_log_result('28_window_agg', 'Window aggregates SUM/AVG/COUNT', 0);
    END demo_28_window_aggregate;

    -- ========== 29. LEAD / LAG ==========
    PROCEDURE demo_29_window_lead_lag IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 29: LEAD / LAG ---');

        FOR r IN (
            SELECT
                emp_name, dept_id, base_salary,
                LAG(base_salary, 1) OVER (ORDER BY base_salary) AS prev_sal,
                LEAD(base_salary, 1) OVER (ORDER BY base_salary) AS next_sal,
                LAG(emp_name, 1) OVER (ORDER BY base_salary) AS prev_emp,
                LEAD(emp_name, 1) OVER (ORDER BY base_salary) AS next_emp,
                base_salary - LAG(base_salary, 1) OVER (ORDER BY base_salary) AS diff_prev,
                LEAD(base_salary, 1) OVER (ORDER BY base_salary) - base_salary AS diff_next
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY base_salary
            FETCH FIRST 8 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Sal:' || LPAD(r.base_salary, 6) ||
                ' | Prev:' || LPAD(NVL(TO_CHAR(r.prev_sal), '-'), 6) ||
                ' (' || NVL(r.prev_emp, '-') || ')' ||
                ' | Next:' || LPAD(NVL(TO_CHAR(r.next_sal), '-'), 6) ||
                ' (' || NVL(r.next_emp, '-') || ')'
            );
        END LOOP;

        proc_log_result('29_lead_lag', 'LEAD/LAG with offsets', 0);
    END demo_29_window_lead_lag;

    -- ========== 30. FIRST_VALUE / LAST_VALUE / NTH_VALUE ==========
    PROCEDURE demo_30_window_first_last IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 30: FIRST_VALUE / LAST_VALUE / NTH_VALUE ---');

        FOR r IN (
            SELECT
                emp_name, dept_id, base_salary,
                FIRST_VALUE(emp_name) OVER (PARTITION BY dept_id ORDER BY base_salary DESC) AS top_earner,
                LAST_VALUE(emp_name) OVER (
                    PARTITION BY dept_id
                    ORDER BY base_salary DESC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                ) AS bottom_earner,
                NTH_VALUE(emp_name, 2) OVER (PARTITION BY dept_id ORDER BY base_salary DESC) AS second_earner
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY dept_id, base_salary DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Dept:' || r.dept_id ||
                ' | Sal:' || r.base_salary ||
                ' | Top:' || RPAD(r.top_earner, 8) ||
                ' | 2nd:' || NVL(RPAD(r.second_earner, 8), 'N/A') ||
                ' | Bottom:' || r.bottom_earner
            );
        END LOOP;

        proc_log_result('30_first_last', 'FIRST_VALUE/LAST_VALUE/NTH_VALUE', 0);
    END demo_30_window_first_last;

    -- ========== 31. 窗口帧 ROWS / RANGE ==========
    PROCEDURE demo_31_window_frame IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 31: Window Frame (ROWS/RANGE) ---');

        FOR r IN (
            SELECT
                emp_name, base_salary,
                SUM(base_salary) OVER (ORDER BY base_salary ROWS UNBOUNDED PRECEDING) AS cumsum_rows,
                AVG(base_salary) OVER (ORDER BY base_salary ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) AS moving_avg,
                SUM(base_salary) OVER (ORDER BY base_salary RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumsum_range,
                COUNT(*) OVER (ORDER BY base_salary ROWS BETWEEN CURRENT ROW AND 2 FOLLOWING) AS look_ahead
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY base_salary
            FETCH FIRST 8 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Sal:' || LPAD(r.base_salary, 6) ||
                ' | CumSum:' || LPAD(r.cumsum_rows, 7) ||
                ' | MovAvg:' || LPAD(ROUND(r.moving_avg, 2), 7) ||
                ' | LookAhead:' || r.look_ahead
            );
        END LOOP;

        proc_log_result('31_window_frame', 'ROWS/RANGE frame specifications', 0);
    END demo_31_window_frame;

-- ========== 32. UNION / INTERSECT / EXCEPT ==========
    PROCEDURE demo_32_union_intersect_except IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 32: UNION / INTERSECT / EXCEPT ---');

        -- UNION ALL (保留重复)
        SELECT COUNT(*) INTO v_count FROM (
            SELECT emp_id FROM employees WHERE dept_id = 10
            UNION ALL
            SELECT emp_id FROM employees WHERE dept_id = 20
        );
        DBE_OUTPUT.PRINT_LINE('UNION ALL (dept 10 + 20): ' || v_count);

        -- UNION (去重)
        SELECT COUNT(*) INTO v_count FROM (
            SELECT emp_id FROM employees WHERE base_salary > 8000
            UNION
            SELECT emp_id FROM employees WHERE dept_id = 10
        );
        DBE_OUTPUT.PRINT_LINE('UNION (salary>8000 OR dept=10, dedup): ' || v_count);

        -- INTERSECT
        SELECT COUNT(*) INTO v_count FROM (
            SELECT emp_id FROM employees WHERE dept_id = 10
            INTERSECT
            SELECT emp_id FROM employees WHERE base_salary > 7000
        );
        DBE_OUTPUT.PRINT_LINE('INTERSECT (dept=10 AND salary>7000): ' || v_count);

        -- EXCEPT (差集)
        SELECT COUNT(*) INTO v_count FROM (
            SELECT emp_id FROM employees WHERE dept_id = 10
            EXCEPT
            SELECT emp_id FROM employees WHERE base_salary > 8000
        );
        DBE_OUTPUT.PRINT_LINE('EXCEPT (dept=10 BUT salary<=8000): ' || v_count);

        -- 复杂组合
        FOR r IN (
            SELECT emp_id, emp_name, 'HIGH_SALARY' AS source
            FROM employees WHERE base_salary > 10000
            UNION ALL
            SELECT emp_id, emp_name, 'DEPT_10'
            FROM employees WHERE dept_id = 10 AND base_salary <= 10000
            ORDER BY emp_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_id || ' | ' || RPAD(r.emp_name, 8) || ' | ' || r.source);
        END LOOP;

        proc_log_result('32_union', 'UNION/INTERSECT/EXCEPT/ALL', v_count);
    END demo_32_union_intersect_except;

    -- ========== 33. CASE 表达式 ==========
    PROCEDURE demo_33_case_expression IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 33: CASE Expression ---');

        -- 简单 CASE
        FOR r IN (
            SELECT
                emp_name,
                base_salary,
                CASE dept_id
                    WHEN 10 THEN '销售部'
                    WHEN 20 THEN '技术部'
                    WHEN 30 THEN '财务部'
                    ELSE '其他'
                END AS dept_name,
                -- 搜索 CASE
                CASE
                    WHEN base_salary >= 12000 THEN '高级'
                    WHEN base_salary >= 9000  THEN '中级'
                    WHEN base_salary >= 7000  THEN '初级'
                    ELSE '实习'
                END AS level,
                -- 嵌套 CASE
                CASE
                    WHEN status = 'ACTIVE' THEN
                        CASE
                            WHEN base_salary > 10000 THEN '高薪在职'
                            ELSE '普通在职'
                        END
                    ELSE '已离职'
                END AS status_desc
            FROM employees
            ORDER BY base_salary DESC
            FETCH FIRST 5 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Sal:' || LPAD(r.base_salary, 6) ||
                ' | ' || RPAD(r.dept_name, 8) || ' | ' || RPAD(r.level, 6) ||
                ' | ' || r.status_desc
            );
        END LOOP;

        proc_log_result('33_case', 'Simple/searched/nested CASE', 0);
    END demo_33_case_expression;

    -- ========== 34. COALESCE / NVL / NULLIF ==========
    PROCEDURE demo_34_coalesce_nvl IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 34: COALESCE / NVL / NULLIF ---');

        FOR r IN (
            SELECT
                emp_id,
                emp_name,
                COALESCE(manager_id, 0) AS mgr_or_zero,           -- 第一个非 NULL
                NVL(manager_id, -1) AS mgr_or_neg1,                 -- Oracle 风格
                COALESCE(NULL, NULL, manager_id, 999) AS fallback,  -- 多参数
                NULLIF(dept_id, 10) AS dept_if_not_10,              -- 相等则 NULL
                COALESCE(
                    NULLIF(phone, '13800138001'),
                    'NO_PHONE'
                ) AS phone_or_default
            FROM employees
            ORDER BY emp_id
            FETCH FIRST 5 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || RPAD(r.emp_name, 8) ||
                ' | Mgr:' || r.mgr_or_zero ||
                ' | NVL:' || r.mgr_or_neg1 ||
                ' | Fallback:' || r.fallback ||
                ' | Dept!=10:' || NVL(TO_CHAR(r.dept_if_not_10), 'NULL') ||
                ' | Phone:' || r.phone_or_default
            );
        END LOOP;

        proc_log_result('34_coalesce', 'COALESCE/NVL/NULLIF', 0);
    END demo_34_coalesce_nvl;

    -- ========== 35. CAST / :: 类型转换 ==========
    PROCEDURE demo_35_cast_convert IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 35: CAST / :: Type Conversion ---');

        FOR r IN (
            SELECT
                emp_id,
                -- CAST 标准语法
                CAST(base_salary AS INTEGER) AS sal_int,
                CAST(hire_date AS VARCHAR2(20)) AS hire_str,
                CAST('2024-01-01' AS DATE) AS str_to_date,
                -- PostgreSQL :: 语法（高斯兼容）
                base_salary::INTEGER AS sal_int2,
                hire_date::VARCHAR2(20) AS hire_str2,
                -- 数值转换
                CAST(3.14159 AS NUMERIC(10,2)) AS pi_rounded,
                -- 字符串拼接转数值
                CAST('12345' AS INTEGER) AS str_num
            FROM employees
            FETCH FIRST 3 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | SalInt:' || r.sal_int ||
                ' | HireStr:' || r.hire_str ||
                ' | Pi:' || r.pi_rounded ||
                ' | StrNum:' || r.str_num
            );
        END LOOP;

        proc_log_result('35_cast', 'CAST / :: type conversion', 0);
    END demo_35_cast_convert;

    -- ========== 36. 字符串函数 ==========
    PROCEDURE demo_36_string_functions IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 36: String Functions ---');

        FOR r IN (
            SELECT
                emp_name,
                -- 拼接
                CONCAT(emp_name, '@company') AS concat_str,
                emp_name || ' - ' || email AS pipe_concat,
                -- 大小写
                UPPER(emp_name) AS upper_name,
                LOWER(email) AS lower_email,
                INITCAP('zhang san') AS initcap_str,
                -- 截取
                SUBSTR(emp_name, 1, 2) AS first_2,
                SUBSTR(emp_name, -2) AS last_2,
                LEFT(email, 5) AS left_5,
                RIGHT(email, 4) AS right_4,
                -- 查找替换
                REPLACE(email, '@hx.com', '@new.com') AS replaced,
                STRPOS(email, '@') AS at_pos,
                POSITION('.' IN email) AS dot_pos,
                -- 填充修剪
                LPAD(emp_name, 10, '*') AS lpad_name,
                RPAD(emp_name, 10, '-') AS rpad_name,
                TRIM('  hello  ') AS trimmed,
                -- 长度
                LENGTH(emp_name) AS name_len,
                CHAR_LENGTH(email) AS email_len,
                OCTET_LENGTH('中文字符') AS octet_len,
                -- 正则
                REGEXP_REPLACE(phone, '1380013800', '139') AS regex_replaced,
                REGEXP_MATCHES(email, '^[a-z]+') AS regex_match
            FROM employees
            FETCH FIRST 3 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Upper:' || r.upper_name ||
                ' | Substr(1,2):' || r.first_2 ||
                ' | Len:' || r.name_len ||
                ' | Replace:' || r.replaced
            );
        END LOOP;

        proc_log_result('36_string', 'String functions (concat/upper/substr/replace/regexp)', 0);
    END demo_36_string_functions;

    -- ========== 37. 日期函数 ==========
    PROCEDURE demo_37_date_functions IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 37: Date Functions ---');

        FOR r IN (
            SELECT
                emp_name,
                hire_date,
                -- 当前日期时间
                CURRENT_DATE AS today,
                CURRENT_TIMESTAMP AS now,
                LOCALTIMESTAMP AS local_now,
                -- 提取
                EXTRACT(YEAR FROM hire_date) AS hire_year,
                EXTRACT(MONTH FROM hire_date) AS hire_month,
                EXTRACT(DAY FROM hire_date) AS hire_day,
                EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - hire_date) / 86400 AS days_diff,
                -- 格式化
                TO_CHAR(hire_date, 'YYYY-MM-DD') AS fmt_ymd,
                TO_CHAR(hire_date, 'YYYY"年"MM"月"DD"日"') AS fmt_chinese,
                TO_CHAR(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS') AS fmt_datetime,
                -- 计算
                hire_date + INTERVAL '1' YEAR AS plus_1y,
                hire_date + INTERVAL '3' MONTH AS plus_3m,
                hire_date + 7 AS plus_7d,
                CURRENT_DATE - hire_date AS days_since_hire,
                AGE(CURRENT_DATE, hire_date) AS age_interval,
                DATE_TRUNC('month', hire_date) AS month_start,
                DATE_TRUNC('year', hire_date) AS year_start,
                -- 其他
                LAST_DAY(hire_date) AS month_end,
                NEXT_DAY(hire_date, 'FRIDAY') AS next_fri,
                GREATEST(hire_date, DATE '2020-01-01') AS later_date,
                LEAST(hire_date, DATE '2025-01-01') AS earlier_date
            FROM employees
            FETCH FIRST 3 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Hire:' || r.fmt_ymd ||
                ' | Year:' || r.hire_year ||
                ' | Days:' || ROUND(r.days_diff, 0) ||
                ' | +1Y:' || r.plus_1y
            );
        END LOOP;

        proc_log_result('37_date', 'Date functions (extract/to_char/interval/age/trunc)', 0);
    END demo_37_date_functions;

    -- ========== 38. 数学函数 ==========
    PROCEDURE demo_38_math_functions IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 38: Math Functions ---');

        FOR r IN (
            SELECT
                emp_name,
                base_salary,
                ROUND(base_salary, -3) AS round_k,
                TRUNC(base_salary, -3) AS trunc_k,
                CEIL(base_salary / 1000) * 1000 AS ceil_k,
                FLOOR(base_salary / 1000) * 1000 AS floor_k,
                MOD(base_salary::INTEGER, 1000) AS remainder,
                ABS(base_salary - 10000) AS abs_diff,
                SIGN(base_salary - 10000) AS sign,
                POWER(base_salary / 10000, 2) AS squared,
                SQRT(base_salary) AS sqroot,
                LN(base_salary) AS nat_log,
                LOG(10, base_salary) AS log10,
                EXP(1) AS e_value,
                PI() AS pi_val,
                RANDOM() AS rand_val,
                -- 三角函数
                SIN(base_salary / 10000) AS sine,
                COS(base_salary / 10000) AS cosine
            FROM employees
            FETCH FIRST 3 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | Sal:' || r.base_salary ||
                ' | RoundK:' || r.round_k ||
                ' | TruncK:' || r.trunc_k ||
                ' | Sqrt:' || ROUND(r.sqroot, 2)
            );
        END LOOP;

        proc_log_result('38_math', 'Math functions (round/trunc/ceil/floor/mod/power/sqrt)', 0);
    END demo_38_math_functions;

    -- ========== 39. 条件聚合 ==========
    PROCEDURE demo_39_conditional_agg IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 39: Conditional Aggregation ---');

        FOR r IN (
            SELECT
                dept_id,
                COUNT(*) AS total_emps,
                COUNT(CASE WHEN status = 'ACTIVE' THEN 1 END) AS active_count,
                COUNT(CASE WHEN base_salary > 10000 THEN 1 END) AS high_salary_count,
                SUM(base_salary) AS total_salary,
                SUM(CASE WHEN status = 'ACTIVE' THEN base_salary END) AS active_salary,
                AVG(CASE WHEN hire_date >= '2020-01-01' THEN base_salary END) AS new_avg,
                MAX(CASE WHEN status = 'ACTIVE' THEN base_salary END) AS active_max,
                -- CASE 条件聚合
                SUM(CASE WHEN status = 'ACTIVE' THEN base_salary ELSE 0 END) AS case_active_sal,
                COUNT(CASE WHEN base_salary > 10000 THEN 1 END) AS case_high_count
            FROM employees
            GROUP BY dept_id
            ORDER BY dept_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                'Dept ' || r.dept_id || ' | Total:' || r.total_emps ||
                ' | Active:' || r.active_count ||
                ' | HighSal:' || r.high_salary_count ||
                ' | TotalSal:' || r.total_salary ||
                ' | ActiveSal:' || r.active_salary ||
                ' | NewAvg:' || ROUND(r.new_avg, 2)
            );
        END LOOP;

        proc_log_result('39_cond_agg', 'FILTER / CASE conditional aggregation', 0);
    END demo_39_conditional_agg;

    -- ========== 40. 手动 PIVOT (CASE + GROUP BY) ==========
    PROCEDURE demo_40_pivot_manual IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 40: Manual PIVOT (CASE + GROUP BY) ---');

        -- 行转列：按季度统计各部门平均工资
        FOR r IN (
            SELECT
                e.dept_id,
                AVG(CASE WHEN perf_quarter = 1 THEN perf_score END) AS q1_avg,
                AVG(CASE WHEN perf_quarter = 2 THEN perf_score END) AS q2_avg,
                AVG(CASE WHEN perf_quarter = 3 THEN perf_score END) AS q3_avg,
                AVG(CASE WHEN perf_quarter = 4 THEN perf_score END) AS q4_avg,
                MAX(CASE WHEN perf_quarter = 1 THEN perf_score END) AS q1_max,
                MAX(CASE WHEN perf_quarter = 2 THEN perf_score END) AS q2_max,
                COUNT(CASE WHEN perf_quarter = 1 THEN 1 END) AS q1_count,
                COUNT(CASE WHEN perf_quarter = 2 THEN 1 END) AS q2_count
            FROM emp_performance p
            JOIN employees e ON p.emp_id = e.emp_id
            WHERE perf_year = 2024
            GROUP BY e.dept_id
            ORDER BY e.dept_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                'Dept ' || r.dept_id ||
                ' | Q1 Avg:' || ROUND(r.q1_avg, 2) || ' (n=' || r.q1_count || ')' ||
                ' | Q2 Avg:' || ROUND(r.q2_avg, 2) || ' (n=' || r.q2_count || ')' ||
                ' | Q3 Avg:' || NVL(TO_CHAR(ROUND(r.q3_avg, 2)), 'N/A') ||
                ' | Q4 Avg:' || NVL(TO_CHAR(ROUND(r.q4_avg, 2)), 'N/A')
            );
        END LOOP;

        -- 更复杂的 PIVOT：产品销量
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Sales PIVOT by region and product:');
        FOR r IN (
            SELECT
                region,
                SUM(CASE WHEN product_a_qty > 0 THEN product_a_amt ELSE 0 END) AS product_a_total,
                SUM(CASE WHEN product_b_qty > 0 THEN product_b_amt ELSE 0 END) AS product_b_total,
                SUM(CASE WHEN product_c_qty > 0 THEN product_c_amt ELSE 0 END) AS product_c_total,
                SUM(product_a_amt + product_b_amt + product_c_amt) AS grand_total
            FROM sales_data
            GROUP BY region
            ORDER BY grand_total DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.region, 8) ||
                ' | A:' || LPAD(TO_CHAR(r.product_a_total, 'FM999,999'), 8) ||
                ' | B:' || LPAD(TO_CHAR(r.product_b_total, 'FM999,999'), 8) ||
                ' | C:' || LPAD(TO_CHAR(r.product_c_total, 'FM999,999'), 8) ||
                ' | Total:' || LPAD(TO_CHAR(r.grand_total, 'FM999,999'), 10)
            );
        END LOOP;

        proc_log_result('40_pivot', 'Manual PIVOT with CASE aggregation', 0);
    END demo_40_pivot_manual;

    -- ========== 41. 手动 UNPIVOT (UNION ALL) ==========
    PROCEDURE demo_41_unpivot_manual IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 41: Manual UNPIVOT (UNION ALL) ---');

        -- 列转行：将 sales_data 的产品列转为行
        FOR r IN (
            SELECT sale_id, emp_id, sale_date, region, 'PRODUCT_A' AS product, product_a_qty AS qty, product_a_amt AS amt
            FROM sales_data WHERE product_a_qty > 0
            UNION ALL
            SELECT sale_id, emp_id, sale_date, region, 'PRODUCT_B' AS product, product_b_qty AS qty, product_b_amt AS amt
            FROM sales_data WHERE product_b_qty > 0
            UNION ALL
            SELECT sale_id, emp_id, sale_date, region, 'PRODUCT_C' AS product, product_c_qty AS qty, product_c_amt AS amt
            FROM sales_data WHERE product_c_qty > 0
            ORDER BY sale_id, product
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                'Sale:' || r.sale_id || ' | ' || RPAD(r.product, 10) ||
                ' | Qty:' || LPAD(r.qty, 4) || ' | Amt:' || LPAD(TO_CHAR(r.amt, 'FM999,999'), 8)
            );
        END LOOP;

        SELECT COUNT(*) INTO v_count FROM (
            SELECT sale_id, 'PRODUCT_A' AS product, product_a_qty AS qty FROM sales_data WHERE product_a_qty > 0
            UNION ALL
            SELECT sale_id, 'PRODUCT_B' AS product, product_b_qty AS qty FROM sales_data WHERE product_b_qty > 0
            UNION ALL
            SELECT sale_id, 'PRODUCT_C' AS product, product_c_qty AS qty FROM sales_data WHERE product_c_qty > 0
        );

        proc_log_result('41_unpivot', 'Manual UNPIVOT with UNION ALL', v_count);
    END demo_41_unpivot_manual;

    -- ========== 42. LATERAL UNPIVOT ==========
    PROCEDURE demo_42_unpivot_lateral IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 42: LATERAL UNPIVOT ---');

        -- 使用 LATERAL + UNNEST 实现更优雅的 UNPIVOT
        FOR r IN (
            SELECT
                s.sale_id,
                s.emp_id,
                s.sale_date,
                s.region,
                u.product_name,
                u.qty,
                u.amt
            FROM sales_data s,
            LATERAL (VALUES
                ('PRODUCT_A', s.product_a_qty, s.product_a_amt),
                ('PRODUCT_B', s.product_b_qty, s.product_b_amt),
                ('PRODUCT_C', s.product_c_qty, s.product_c_amt)
            ) AS u(product_name, qty, amt)
            WHERE u.qty > 0
            ORDER BY s.sale_id, u.product_name
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                'Sale:' || r.sale_id || ' | ' || RPAD(r.product_name, 10) ||
                ' | Qty:' || LPAD(r.qty, 4) || ' | Amt:' || LPAD(TO_CHAR(r.amt, 'FM999,999'), 8)
            );
        END LOOP;

        SELECT COUNT(*) INTO v_count FROM (
            SELECT s.sale_id, u.product_name, u.qty, u.amt
            FROM sales_data s,
            LATERAL (VALUES
                ('PRODUCT_A', s.product_a_qty, s.product_a_amt),
                ('PRODUCT_B', s.product_b_qty, s.product_b_amt),
                ('PRODUCT_C', s.product_c_qty, s.product_c_amt)
            ) AS u(product_name, qty, amt)
            WHERE u.qty > 0
        );

        proc_log_result('42_lateral_unpivot', 'LATERAL + VALUES UNPIVOT', v_count);
    END demo_42_unpivot_lateral;

    -- ========== 43. JSON 函数 ==========
    PROCEDURE demo_43_json_functions IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 43: JSON Functions ---');

        FOR r IN (
            SELECT
                emp_id,
                emp_name,
                -- 构造 JSON
                JSON_BUILD_OBJECT(
                    'id', emp_id,
                    'name', emp_name,
                    'salary', base_salary,
                    'dept', dept_id
                ) AS emp_json,
                -- 行转 JSON
                ROW_TO_JSON(employees) AS row_json,
                -- JSON 数组
                JSON_BUILD_ARRAY(emp_id, emp_name, base_salary) AS emp_array,
                -- JSON 聚合
                (SELECT JSON_AGG(JSON_BUILD_OBJECT('year', perf_year, 'score', perf_score))
                 FROM emp_performance p WHERE p.emp_id = employees.emp_id) AS perf_history
            FROM employees
            FETCH FIRST 3 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | JSON:' || SUBSTR(r.emp_json, 1, 50) || '...'
            );
        END LOOP;

        -- JSON 解析
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('JSON extraction:');
        FOR r IN (
            SELECT
                emp_id,
                JSON_BUILD_OBJECT('name', emp_name, 'sal', base_salary)::JSON AS j,
                JSON_BUILD_OBJECT('name', emp_name, 'sal', base_salary)::JSON->>'name' AS json_name,
                JSON_BUILD_OBJECT('name', emp_name, 'sal', base_salary)::JSON->'sal' AS json_sal,
                JSON_BUILD_OBJECT('nested', JSON_BUILD_OBJECT('deep', 'value'))::JSON#>'{nested,deep}' AS deep_val
            FROM employees
            FETCH FIRST 2 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | Name:' || r.json_name ||
                ' | Sal:' || r.json_sal ||
                ' | Deep:' || r.deep_val
            );
        END LOOP;

        proc_log_result('43_json', 'JSON build/row_to_json/agg/extract', 0);
    END demo_43_json_functions;

    -- ========== 44. ARRAY_AGG / STRING_AGG ==========
    PROCEDURE demo_44_array_agg IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 44: ARRAY_AGG / STRING_AGG ---');

        -- ARRAY_AGG
        FOR r IN (
            SELECT
                dept_id,
                ARRAY_AGG(emp_name ORDER BY base_salary DESC) AS name_array,
                ARRAY_AGG(base_salary ORDER BY base_salary DESC) AS sal_array,
                ARRAY_AGG(DISTINCT status) AS status_array
            FROM employees
            GROUP BY dept_id
            ORDER BY dept_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                'Dept ' || r.dept_id || ' | Names:' ||
                REPLACE(REPLACE(r.name_array::VARCHAR2, '{', '['), '}', ']') ||
                ' | Statuses:' || r.status_array::VARCHAR2
            );
        END LOOP;

        -- STRING_AGG
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('STRING_AGG (concatenation):');
        FOR r IN (
            SELECT
                dept_id,
                STRING_AGG(emp_name, ', ' ORDER BY base_salary DESC) AS name_list,
                STRING_AGG(DISTINCT status, ' | ') AS status_list
            FROM employees
            GROUP BY dept_id
            ORDER BY dept_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('Dept ' || r.dept_id || ' | ' || r.name_list);
            DBE_OUTPUT.PRINT_LINE('  Statuses: ' || r.status_list);
        END LOOP;

        proc_log_result('44_array_string_agg', 'ARRAY_AGG / STRING_AGG with ORDER BY', 0);
    END demo_44_array_agg;

    -- ========== 45. generate_series ==========
    PROCEDURE demo_45_generate_series IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 45: generate_series ---');

        -- 数字序列
        DBE_OUTPUT.PRINT_LINE('Number series 1 to 5:');
        FOR r IN (SELECT generate_series(1, 5) AS n) LOOP
            DBE_OUTPUT.PRINT_LINE('  ' || r.n);
        END LOOP;

        -- 步长序列
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Step series 0 to 10 by 2:');
        FOR r IN (SELECT generate_series(0, 10, 2) AS n) LOOP
            DBE_OUTPUT.PRINT_LINE('  ' || r.n);
        END LOOP;

        -- 日期序列
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Date series (daily in Jan 2024):');
        FOR r IN (
            SELECT generate_series(DATE '2024-01-01', DATE '2024-01-05', INTERVAL '1 day')::DATE AS dt
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('  ' || r.dt);
        END LOOP;

        -- 与数据结合
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Employees x 3 months:');
        FOR r IN (
            SELECT e.emp_name, m.month_num
            FROM employees e
            CROSS JOIN (SELECT generate_series(1, 3) AS month_num) m
            WHERE e.emp_id <= 1002
            ORDER BY e.emp_id, m.month_num
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('  ' || RPAD(r.emp_name, 8) || ' | Month:' || r.month_num);
        END LOOP;

        SELECT COUNT(*) INTO v_count FROM generate_series(1, 100);
        proc_log_result('45_generate_series', 'generate_series numeric/date/with CROSS JOIN', v_count);
    END demo_45_generate_series;

    -- ========== 46. VALUES 子句作为表 ==========
    PROCEDURE demo_46_values_clause IS
        v_count INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 46: VALUES Clause as Table ---');

        -- VALUES 作为内联表
        FOR r IN (
            SELECT * FROM (VALUES (1, 'A', 100), (2, 'B', 200), (3, 'C', 300)) AS t(id, name, val)
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('  ID:' || r.id || ' | Name:' || r.name || ' | Val:' || r.val);
        END LOOP;

        -- 与真实表 JOIN
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('VALUES JOIN with employees:');
        FOR r IN (
            SELECT e.emp_name, v.grade, v.min_sal, v.max_sal
            FROM employees e
            JOIN (VALUES
                ('高级', 12000, 999999),
                ('中级', 9000, 11999),
                ('初级', 7000, 8999),
                ('实习', 0, 6999)
            ) AS v(grade, min_sal, max_sal)
            ON e.base_salary BETWEEN v.min_sal AND v.max_sal
            WHERE e.status = 'ACTIVE'
            ORDER BY e.base_salary DESC
            FETCH FIRST 5 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | ' || RPAD(r.grade, 6) ||
                ' | Range:' || r.min_sal || '-' || r.max_sal
            );
        END LOOP;

        -- 多列 VALUES
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Multi-column VALUES with calculation:');
        FOR r IN (
            SELECT v.year_num, COUNT(e.emp_id) AS hired_count
            FROM (VALUES
                (2018), (2019), (2020), (2021), (2022), (2023)
            ) AS v(year_num)
            LEFT JOIN employees e ON EXTRACT(YEAR FROM e.hire_date) = v.year_num
            GROUP BY v.year_num
            ORDER BY v.year_num
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('  ' || r.year_num || ' | Hired:' || r.hired_count);
        END LOOP;

        SELECT COUNT(*) INTO v_count FROM (VALUES (1), (2), (3)) AS t(n);
        proc_log_result('46_values', 'VALUES clause as inline table with JOIN', v_count);
    END demo_46_values_clause;

    -- ========== 47. SELECT INTO 变量 ==========
    PROCEDURE demo_47_select_into IS
        v_count     INTEGER;
        v_max_sal   NUMERIC(18,2);
        v_min_sal   NUMERIC(18,2);
        v_avg_sal   NUMERIC(18,2);
        v_name      VARCHAR2(100);
        v_dept      VARCHAR2(100);
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 47: SELECT INTO Variables ---');

        -- 单值 INTO
        SELECT COUNT(*) INTO v_count FROM employees WHERE status = 'ACTIVE';
        DBE_OUTPUT.PRINT_LINE('Active count (single INTO): ' || v_count);

        -- 多值 INTO
        SELECT MAX(base_salary), MIN(base_salary), AVG(base_salary)
        INTO v_max_sal, v_min_sal, v_avg_sal
        FROM employees;
        DBE_OUTPUT.PRINT_LINE('Max:' || v_max_sal || ' Min:' || v_min_sal || ' Avg:' || ROUND(v_avg_sal, 2));

        -- 带 WHERE 的 INTO
        SELECT emp_name, (SELECT dept_name FROM departments d WHERE d.dept_id = e.dept_id)
        INTO v_name, v_dept
        FROM employees e
        WHERE emp_id = 1001;
        DBE_OUTPUT.PRINT_LINE('Emp 1001: ' || v_name || ' in ' || v_dept);

        -- BULK COLLECT INTO
        DECLARE
            TYPE t_names IS TABLE OF employees.emp_name%TYPE;
            TYPE t_sals IS TABLE OF employees.base_salary%TYPE;
            v_names t_names;
            v_sals  t_sals;
        BEGIN
            SELECT emp_name, base_salary
            BULK COLLECT INTO v_names, v_sals
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY base_salary DESC;

            DBE_OUTPUT.PRINT_LINE('BULK COLLECT ' || v_names.COUNT || ' rows');
            FOR i IN 1..LEAST(v_names.COUNT, 3) LOOP
                DBE_OUTPUT.PRINT_LINE('  ' || v_names(i) || ': ' || v_sals(i));
            END LOOP;
        END;

        proc_log_result('47_select_into', 'SELECT INTO / BULK COLLECT INTO', v_count);
    END demo_47_select_into;

    -- ========== 48. FOR UPDATE / FOR SHARE ==========
    PROCEDURE demo_48_for_update IS
        v_count INTEGER := 0;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 48: FOR UPDATE / FOR SHARE ---');

        -- FOR UPDATE (行级锁)
        FOR r IN (
            SELECT emp_id, emp_name, base_salary
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY emp_id
            FETCH FIRST 3 ROWS ONLY
            FOR UPDATE
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('Locked: ' || r.emp_id || ' | ' || r.emp_name);
            v_count := v_count + 1;
        END LOOP;

        -- FOR UPDATE OF 指定表
        FOR r IN (
            SELECT e.emp_id, e.emp_name, d.dept_name
            FROM employees e
            JOIN departments d ON e.dept_id = d.dept_id
            WHERE e.emp_id <= 1003
            FOR UPDATE OF e
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('Locked employees only: ' || r.emp_id || ' | ' || r.emp_name);
        END LOOP;

        -- FOR UPDATE SKIP LOCKED (跳过已被锁的行)
        FOR r IN (
            SELECT emp_id, emp_name
            FROM employees
            WHERE status = 'ACTIVE'
            FETCH FIRST 2 ROWS ONLY
            FOR UPDATE SKIP LOCKED
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('Skip locked: ' || r.emp_id || ' | ' || r.emp_name);
        END LOOP;

        -- FOR SHARE (共享锁)
        FOR r IN (
            SELECT emp_id, emp_name
            FROM employees
            WHERE emp_id = 1001
            FOR SHARE
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('Shared lock: ' || r.emp_id || ' | ' || r.emp_name);
        END LOOP;

        proc_log_result('48_for_update', 'FOR UPDATE / FOR SHARE / SKIP LOCKED / OF', v_count);
    END demo_48_for_update;

    -- ========== 49. 复杂嵌套查询 ==========
    PROCEDURE demo_49_complex_nested IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 49: Complex Nested Query ---');

        FOR r IN (
            -- 最外层：排序和限制
            SELECT
                dept_name,
                emp_name,
                base_salary,
                perf_score,
                dept_rank,
                overall_rank,
                salary_pct,
                category
            FROM (
                -- 第二层：窗口函数和分类
                SELECT
                    d.dept_name,
                    e.emp_name,
                    e.base_salary,
                    p.perf_score,
                    RANK() OVER (PARTITION BY e.dept_id ORDER BY e.base_salary DESC) AS dept_rank,
                    RANK() OVER (ORDER BY e.base_salary DESC) AS overall_rank,
                    PERCENT_RANK() OVER (ORDER BY e.base_salary) AS salary_pct,
                    CASE
                        WHEN e.base_salary >= (SELECT AVG(base_salary) * 1.2 FROM employees) THEN 'HIGH'
                        WHEN e.base_salary >= (SELECT AVG(base_salary) * 0.8 FROM employees) THEN 'MEDIUM'
                        ELSE 'LOW'
                    END AS category
                FROM (
                    -- 第三层：过滤和子查询
                    SELECT emp_id, emp_name, dept_id, base_salary
                    FROM employees
                    WHERE status = 'ACTIVE'
                      AND emp_id IN (
                          SELECT emp_id FROM emp_performance
                          WHERE perf_year = 2024 AND perf_score >= 60
                      )
                ) e
                INNER JOIN departments d ON e.dept_id = d.dept_id
                LEFT JOIN (
                    -- 第四层：聚合子查询
                    SELECT emp_id, AVG(perf_score) AS perf_score
                    FROM emp_performance
                    WHERE perf_year = 2024
                    GROUP BY emp_id
                    HAVING COUNT(*) >= 1
                ) p ON e.emp_id = p.emp_id
            ) ranked
            WHERE dept_rank <= 3  -- 每部门前3
            ORDER BY dept_name, dept_rank
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.dept_name, 8) || ' | ' || RPAD(r.emp_name, 8) ||
                ' | Sal:' || LPAD(r.base_salary, 6) ||
                ' | Score:' || LPAD(ROUND(r.perf_score, 1), 5) ||
                ' | DeptRnk:' || r.dept_rank ||
                ' | Overall:' || r.overall_rank ||
                ' | Pct:' || LPAD(ROUND(r.salary_pct * 100, 1) || '%', 6) ||
                ' | ' || r.category
            );
        END LOOP;

        proc_log_result('49_nested', '4-level nested query with subqueries/window/CASE', 0);
    END demo_49_complex_nested;

    -- ========== 50. 综合超级查询 ==========
    PROCEDURE demo_50_comprehensive IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 50: Comprehensive Super Query ---');

        FOR r IN (
            WITH
            -- CTE 1: 员工基础信息
            emp_base AS (
                SELECT
                    e.emp_id,
                    e.emp_name,
                    e.dept_id,
                    e.base_salary,
                    e.bonus_pct,
                    e.hire_date,
                    e.status,
                    e.manager_id,
                    d.dept_name,
                    d.location,
                    d.budget
                FROM employees e
                JOIN departments d ON e.dept_id = d.dept_id
                WHERE e.status = 'ACTIVE'
            ),
            -- CTE 2: 绩效统计
            perf_stats AS (
                SELECT
                    emp_id,
                    AVG(perf_score) AS avg_score,
                    MAX(perf_score) AS max_score,
                    MIN(perf_score) AS min_score,
                    COUNT(*) AS eval_count,
                    STRING_AGG(DISTINCT perf_grade, ', ' ORDER BY perf_grade) AS grade_history
                FROM emp_performance
                WHERE perf_year = 2024
                GROUP BY emp_id
            ),
            -- CTE 3: 项目统计
            proj_stats AS (
                SELECT
                    emp_id,
                    COUNT(DISTINCT project_id) AS project_count,
                    SUM(hours_per_week) AS total_hours,
                    STRING_AGG(DISTINCT role, ', ') AS roles
                FROM emp_projects
                WHERE end_date IS NULL OR end_date >= CURRENT_DATE
                GROUP BY emp_id
            ),
            -- CTE 4: 薪资分位
            salary_percentiles AS (
                SELECT
                    emp_id,
                    base_salary,
                    NTILE(4) OVER (ORDER BY base_salary) AS quartile,
                    PERCENT_RANK() OVER (ORDER BY base_salary) AS pct_rank,
                    CUME_DIST() OVER (ORDER BY base_salary) AS cume_dist
                FROM emp_base
            ),
            -- CTE 5: 经理层级
            mgr_hierarchy AS (
                SELECT
                    e.emp_id,
                    e.emp_name,
                    m.emp_name AS mgr_name,
                    mm.emp_name AS grand_mgr_name,
                    CASE
                        WHEN m.emp_id IS NULL THEN 1
                        WHEN mm.emp_id IS NULL THEN 2
                        ELSE 3
                    END AS hierarchy_level
                FROM emp_base e
                LEFT JOIN employees m ON e.manager_id = m.emp_id
                LEFT JOIN employees mm ON m.manager_id = mm.emp_id
            )
            -- 最终 SELECT
            SELECT
                b.emp_id,
                b.emp_name,
                b.dept_name,
                b.location,
                b.base_salary,
                b.base_salary * (1 + b.bonus_pct) AS total_comp,
                p.avg_score,
                p.max_score,
                p.grade_history,
                COALESCE(pr.project_count, 0) AS project_count,
                COALESCE(pr.total_hours, 0) AS total_hours,
                COALESCE(pr.roles, 'None') AS roles,
                sp.quartile,
                ROUND(sp.pct_rank * 100, 2) AS salary_percentile,
                h.mgr_name,
                h.hierarchy_level,
                EXTRACT(YEAR FROM CURRENT_DATE) - EXTRACT(YEAR FROM b.hire_date) AS years,
                -- 综合评分
                ROUND(
                    COALESCE(p.avg_score, 0) * 0.4 +
                    (b.base_salary / NULLIF((SELECT AVG(base_salary) FROM emp_base), 0)) * 30 * 0.3 +
                    COALESCE(pr.project_count, 0) * 5 * 0.2 +
                    (EXTRACT(YEAR FROM CURRENT_DATE) - EXTRACT(YEAR FROM b.hire_date)) * 2 * 0.1
                , 2) AS composite_score,
                -- 分类
                CASE
                    WHEN p.avg_score >= 90 AND sp.quartile = 4 THEN 'A+ TALENT'
                    WHEN p.avg_score >= 80 AND sp.quartile >= 3 THEN 'A TALENT'
                    WHEN p.avg_score >= 70 AND sp.quartile >= 2 THEN 'B+ CORE'
                    WHEN p.avg_score >= 60 THEN 'B SOLID'
                    ELSE 'C NEEDS IMPROVEMENT'
                END AS talent_category
            FROM emp_base b
            LEFT JOIN perf_stats p ON b.emp_id = p.emp_id
            LEFT JOIN proj_stats pr ON b.emp_id = pr.emp_id
            JOIN salary_percentiles sp ON b.emp_id = sp.emp_id
            LEFT JOIN mgr_hierarchy h ON b.emp_id = h.emp_id
            WHERE b.budget > 3000000  -- 只显示预算充足的部门
            ORDER BY composite_score DESC
            FETCH FIRST 10 ROWS ONLY
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | ' || RPAD(r.dept_name, 8) ||
                ' | Sal:' || LPAD(TO_CHAR(r.base_salary, 'FM999,999'), 8) ||
                ' | Comp:' || LPAD(TO_CHAR(r.total_comp, 'FM999,999'), 8) ||
                ' | Score:' || LPAD(ROUND(r.avg_score, 1), 5) ||
                ' | Quartile:' || r.quartile ||
                ' | Pct:' || LPAD(r.salary_percentile || '%', 7) ||
                ' | Mgr:' || RPAD(NVL(r.mgr_name, 'TOP'), 8) ||
                ' | ' || RPAD(r.talent_category, 16) ||
                ' | Composite:' || r.composite_score
            );
        END LOOP;

        proc_log_result('50_comprehensive', 'Super query: 5 CTEs + all features', 0);
    END demo_50_comprehensive;

END pkg_select_styles;
/

-- ============================================
-- 第四部分：批量调用所有演示
-- ============================================

BEGIN pkg_select_styles.demo_01_basic_select; END;
/
BEGIN pkg_select_styles.demo_02_alias; END;
/
BEGIN pkg_select_styles.demo_03_distinct; END;
/
BEGIN pkg_select_styles.demo_04_where_operators; END;
/
BEGIN pkg_select_styles.demo_05_logical_operators; END;
/
BEGIN pkg_select_styles.demo_06_between_in_like; END;
/
BEGIN pkg_select_styles.demo_07_is_null; END;
/
BEGIN pkg_select_styles.demo_08_order_by; END;
/
BEGIN pkg_select_styles.demo_09_limit_offset; END;
/
BEGIN pkg_select_styles.demo_10_aggregate; END;
/
BEGIN pkg_select_styles.demo_11_group_by; END;
/
BEGIN pkg_select_styles.demo_12_having; END;
/
BEGIN pkg_select_styles.demo_13_join_inner; END;
/
BEGIN pkg_select_styles.demo_14_join_outer; END;
/
BEGIN pkg_select_styles.demo_15_join_self; END;
/
BEGIN pkg_select_styles.demo_16_join_cross; END;
/
BEGIN pkg_select_styles.demo_17_join_natural; END;
/
BEGIN pkg_select_styles.demo_18_join_lateral; END;
/
BEGIN pkg_select_styles.demo_19_subquery_scalar; END;
/
BEGIN pkg_select_styles.demo_20_subquery_correlated; END;
/
BEGIN pkg_select_styles.demo_21_subquery_in; END;
/
BEGIN pkg_select_styles.demo_22_subquery_exists; END;
/
BEGIN pkg_select_styles.demo_23_subquery_all_any; END;
/
BEGIN pkg_select_styles.demo_24_cte_simple; END;
/
BEGIN pkg_select_styles.demo_25_cte_recursive; END;
/
BEGIN pkg_select_styles.demo_26_cte_multiple; END;
/
BEGIN pkg_select_styles.demo_27_window_rank; END;
/
BEGIN pkg_select_styles.demo_28_window_aggregate; END;
/
BEGIN pkg_select_styles.demo_29_window_lead_lag; END;
/
BEGIN pkg_select_styles.demo_30_window_first_last; END;
/
BEGIN pkg_select_styles.demo_31_window_frame; END;
/
BEGIN pkg_select_styles.demo_32_union_intersect_except; END;
/
BEGIN pkg_select_styles.demo_33_case_expression; END;
/
BEGIN pkg_select_styles.demo_34_coalesce_nvl; END;
/
BEGIN pkg_select_styles.demo_35_cast_convert; END;
/
BEGIN pkg_select_styles.demo_36_string_functions; END;
/
BEGIN pkg_select_styles.demo_37_date_functions; END;
/
BEGIN pkg_select_styles.demo_38_math_functions; END;
/
BEGIN pkg_select_styles.demo_39_conditional_agg; END;
/
BEGIN pkg_select_styles.demo_40_pivot_manual; END;
/
BEGIN pkg_select_styles.demo_41_unpivot_manual; END;
/
BEGIN pkg_select_styles.demo_42_unpivot_lateral; END;
/
BEGIN pkg_select_styles.demo_43_json_functions; END;
/
BEGIN pkg_select_styles.demo_44_array_agg; END;
/
BEGIN pkg_select_styles.demo_45_generate_series; END;
/
BEGIN pkg_select_styles.demo_46_values_clause; END;
/
BEGIN pkg_select_styles.demo_47_select_into; END;
/
BEGIN pkg_select_styles.demo_48_for_update; END;
/
BEGIN pkg_select_styles.demo_49_complex_nested; END;
/
BEGIN pkg_select_styles.demo_50_comprehensive; END;
/

-- 显示所有演示结果汇总
BEGIN pkg_select_styles.proc_show_results; END;
/

-- 查看结果日志
SELECT * FROM result_log ORDER BY log_id;
