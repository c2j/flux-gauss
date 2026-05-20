-- ============================================================
-- 包规范（Specification）
-- ============================================================
CREATE OR REPLACE PACKAGE PKG_WARPDRIVER_STRESS_TEST AS

  -- 包级常量与变量（会话级状态）
  g_version           CONSTANT VARCHAR2(20) := '9.9.9-STRESS';
  g_max_retry         CONSTANT INT := 5;
  g_batch_size        CONSTANT INT := 1000;
  g_session_counter   INT := 0;
  g_last_error_code   VARCHAR2(100);

  -- 复合类型（RECORD）
  TYPE r_order_item IS RECORD (
    item_id       BIGINT,
    product_code  VARCHAR2(50),
    quantity      NUMERIC(18,4),
    unit_price    NUMERIC(18,4),
    metadata      JSONB
  );

  -- 嵌套表类型（集合）
  TYPE t_item_array IS TABLE OF r_order_item INDEX BY INT;

  -- 游标类型
  TYPE cur_ref IS REF CURSOR;

  -- 公开接口
  PROCEDURE sp_main_orchestrator(
    p_biz_date    IN  VARCHAR2,
    p_mode        IN  VARCHAR2,
    p_total_amt   OUT NUMERIC,
    p_log_id      OUT BIGINT
  );

  PROCEDURE sp_goto_cleanup_master(
    p_order_id    IN  BIGINT,
    p_result      OUT VARCHAR2
  );

  PROCEDURE sp_goto_loop_purge(
    p_retention_days IN INT,
    p_deleted_cnt    OUT INT
  );

  PROCEDURE sp_goto_skip_logic(
    p_report_type IN  VARCHAR2,
    p_biz_date    IN  VARCHAR2,
    p_content     OUT CLOB
  );

  PROCEDURE sp_goto_deep_escape(
    p_batch_id    IN  BIGINT,
    p_invalid_cnt OUT INT
  );

  PROCEDURE sp_goto_state_machine(
    p_order_id    IN  BIGINT,
    p_event       IN  VARCHAR2,
    p_final_state OUT VARCHAR2
  );

  PROCEDURE sp_dynamic_scheduler_dispatch(
    p_task_list   IN  TEXT,  -- 逗号分隔的task_id
    p_job_prefix  IN  VARCHAR2
  );

  FUNCTION fn_recursive_tree_cleanup(
    p_parent_id   IN  BIGINT,
    p_depth       IN  INT DEFAULT 0
  ) RETURN INT;

  FUNCTION fn_array_jsonb_processor(
    p_items       IN  TEXT,  -- JSON数组字符串
    p_discount    IN  NUMERIC
  ) RETURN JSONB;

  FUNCTION fn_multi_cursor_return(
    p_customer_id IN  BIGINT,
    p_cur_orders  OUT cur_ref,
    p_cur_payments OUT cur_ref
  ) RETURN INT;

  PROCEDURE sp_autonomous_audit(
    p_action      IN  VARCHAR2,
    p_detail      IN  VARCHAR2
  );

  PROCEDURE sp_savepoint_hell(
    p_account_id  IN  BIGINT,
    p_amount      IN  NUMERIC,
    p_final_bal   OUT NUMERIC
  );

END PKG_WARPDRIVER_STRESS_TEST;
/

-- ============================================================
-- 包体（Body）
-- ============================================================
CREATE OR REPLACE PACKAGE BODY PKG_WARPDRIVER_STRESS_TEST AS

  -- ----------------------------------------------------------
  -- 私有工具：自治事务审计（PRAGMA AUTONOMOUS_TRANSACTION）
  -- ----------------------------------------------------------
  PROCEDURE sp_autonomous_audit(
    p_action      IN  VARCHAR2,
    p_detail      IN  VARCHAR2
  ) AS
    PRAGMA AUTONOMOUS_TRANSACTION;
  BEGIN
    INSERT INTO audit_trail(action_code, detail_info, created_at, session_id)
    VALUES(p_action, p_detail, current_timestamp, pg_backend_pid());
    COMMIT;  -- 独立提交，不受主事务影响
  EXCEPTION
    WHEN OTHERS THEN
      NULL; -- 审计失败不能影响主流程
  END;

  -- ----------------------------------------------------------
  -- 私有工具：动态SQL执行器（EXECUTE IMMEDIATE + USING）
  -- ----------------------------------------------------------
  FUNCTION fn_dynamic_executor(
    p_sql         IN  VARCHAR2,
    p_param1      IN  BIGINT,
    p_param2      IN  VARCHAR2
  ) RETURN INT AS
    v_affected    INT;
  BEGIN
    EXECUTE IMMEDIATE p_sql USING p_param1, p_param2;
    GET DIAGNOSTICS v_affected = ROW_COUNT;
    RETURN v_affected;
  END;

  -- ----------------------------------------------------------
  -- 1. 主调度器：复杂控制流大杂烩
  -- ----------------------------------------------------------
  PROCEDURE sp_main_orchestrator(
    p_biz_date    IN  VARCHAR2,
    p_mode        IN  VARCHAR2,
    p_total_amt   OUT NUMERIC,
    p_log_id      OUT BIGINT
  ) AS
    v_date        DATE := TO_DATE(p_biz_date, 'YYYY-MM-DD');
    v_sum         NUMERIC(38,8) := 0;
    v_item_tab    t_item_array;
    v_idx         INT := 0;
    v_cur_orders  cur_ref;
    v_rec         RECORD;
    v_savepoint1  TEXT;
    v_has_error   BOOLEAN := FALSE;
  BEGIN
    -- 包级变量自增
    g_session_counter := g_session_counter + 1;
    p_log_id := nextval('seq_log_master');

    -- 自治事务：记录启动
    sp_autonomous_audit('ORCH_START', 'Mode=' || p_mode || '|Date=' || p_biz_date);

    -- 复杂游标：动态OPEN FOR
    IF p_mode = 'FAST' THEN
      OPEN v_cur_orders FOR
        SELECT order_id, total_amount, status
        FROM orders
        WHERE biz_date = v_date AND status IN ('PENDING','PROCESSING')
        ORDER BY priority DESC
        LIMIT g_batch_size;
    ELSE
      OPEN v_cur_orders FOR
        SELECT order_id, total_amount, status
        FROM orders
        WHERE biz_date = v_date
        ORDER BY create_time;
    END IF;

    <<fetch_loop>>
    FETCH v_cur_orders INTO v_rec;
    IF NOT FOUND THEN
      GOTO close_cursor;
    END IF;

    -- 多层嵌套业务判断
    IF v_rec.status = 'PENDING' THEN
      -- 模拟SAVEPOINT使用
      v_savepoint1 := 'sp_' || v_rec.order_id;
      EXECUTE IMMEDIATE 'SAVEPOINT ' || v_savepoint1;

      BEGIN
        -- 递归清理历史垃圾数据
        DECLARE
          v_cleaned INT;
        BEGIN
          v_cleaned := fn_recursive_tree_cleanup(v_rec.order_id, 0);
        END;

        -- 更新订单状态（可能失败）
        UPDATE orders SET status = 'PROCESSING', process_time = current_timestamp
        WHERE order_id = v_rec.order_id;

        IF SQL%ROWCOUNT = 0 THEN
          -- 回滚到保存点
          EXECUTE IMMEDIATE 'ROLLBACK TO SAVEPOINT ' || v_savepoint1;
          GOTO fetch_loop;
        END IF;

        -- 构建数组（模拟复杂内存计算）
        v_idx := v_idx + 1;
        v_item_tab(v_idx).item_id := v_rec.order_id;
        v_item_tab(v_idx).product_code := 'PROD_' || MOD(v_rec.order_id, 100);
        v_item_tab(v_idx).quantity := v_rec.total_amount / 100;
        v_item_tab(v_idx).unit_price := 100;
        v_item_tab(v_idx).metadata := jsonb_build_object(
          'order_id', v_rec.order_id,
          'mode', p_mode,
          'seq', g_session_counter
        );

        v_sum := v_sum + v_rec.total_amount;

        -- 根据金额触发不同分支
        IF v_rec.total_amount > 1000000 THEN
          -- 大额订单：派发异步子任务
          sp_dynamic_scheduler_dispatch(
            v_rec.order_id::TEXT,
            'JOB_BIG_ORDER_'
          );
        ELSIF v_rec.total_amount < 0 THEN
          -- 异常金额：直接跳到错误处理
          GOTO error_handler;
        END IF;

      EXCEPTION
        WHEN OTHERS THEN
          g_last_error_code := SQLERRM;
          -- 回滚到保存点
          EXECUTE IMMEDIATE 'ROLLBACK TO SAVEPOINT ' || v_savepoint1;
          sp_autonomous_audit('ORCH_ERROR', SQLERRM);
          GOTO fetch_loop;
      END;

    ELSIF v_rec.status = 'PROCESSING' THEN
      -- 跳过已处理的（GOTO 模拟 continue）
      GOTO fetch_loop;
    END IF;

    -- 每100条提交一次（模拟分段提交）
    IF MOD(g_session_counter, 100) = 0 THEN
      COMMIT;
      sp_autonomous_audit('ORCH_COMMIT', 'Batch committed at counter=' || g_session_counter);
    END IF;

    GOTO fetch_loop;

    <<error_handler>>
    -- 统一错误处理出口
    INSERT INTO error_log(log_id, order_id, err_msg, created_at)
    VALUES(p_log_id, v_rec.order_id, 'NEGATIVE_AMOUNT', current_timestamp);
    v_has_error := TRUE;
    GOTO fetch_loop;

    <<close_cursor>>
    CLOSE v_cur_orders;

    -- 最终汇总：使用数组数据
    IF v_idx > 0 THEN
      FOR i IN 1..v_idx LOOP
        INSERT INTO order_item_snapshot(log_id, item_data, created_at)
        VALUES(p_log_id, to_jsonb(v_item_tab(i)), current_timestamp);
      END LOOP;
    END IF;

    p_total_amt := v_sum;

    -- 如果全程无错，最终提交；否则回滚
    IF NOT v_has_error THEN
      COMMIT;
    ELSE
      ROLLBACK;
    END IF;

    sp_autonomous_audit('ORCH_END', 'Total=' || v_sum || '|Items=' || v_idx);
  EXCEPTION
    WHEN OTHERS THEN
      ROLLBACK;
      p_total_amt := -1;
      g_last_error_code := SQLERRM;
      sp_autonomous_audit('ORCH_FATAL', SQLERRM);
      RAISE;
  END;

  -- ----------------------------------------------------------
  -- 2. GOTO 模式A：错误清理出口（统一资源释放）
  -- ----------------------------------------------------------
  PROCEDURE sp_goto_cleanup_master(
    p_order_id    IN  BIGINT,
    p_result      OUT VARCHAR2
  ) AS
    v_lock_key    VARCHAR2(100);
    v_conn_id     BIGINT;
    v_temp_amt    NUMERIC(18,4);
  BEGIN
    v_lock_key := 'LOCK_ORDER_' || p_order_id;
    v_conn_id := pg_backend_pid();

    -- 获取分布式锁
    INSERT INTO distributed_locks(lock_key, holder_id, acquired_at)
    VALUES(v_lock_key, v_conn_id, current_timestamp);

    -- 检查订单是否存在
    SELECT total_amount INTO v_temp_amt FROM orders WHERE order_id = p_order_id;
    IF NOT FOUND THEN
      p_result := 'ORDER_NOT_FOUND';
      GOTO cleanup;
    END IF;

    -- 检查余额
    IF v_temp_amt IS NULL OR v_temp_amt <= 0 THEN
      p_result := 'INVALID_AMOUNT';
      GOTO cleanup;
    END IF;

    -- 检查并发冲突（乐观锁）
    UPDATE orders SET version = version + 1, update_time = current_timestamp
    WHERE order_id = p_order_id AND version = (
      SELECT version FROM orders WHERE order_id = p_order_id
    );
    IF SQL%ROWCOUNT = 0 THEN
      p_result := 'CONFLICT';
      GOTO cleanup;
    END IF;

    -- 执行扣减
    UPDATE accounts SET balance = balance - v_temp_amt
    WHERE account_id = (SELECT account_id FROM orders WHERE order_id = p_order_id);

    INSERT INTO transaction_log(order_id, amount, tx_type, tx_time)
    VALUES(p_order_id, v_temp_amt, 'DEBIT', current_timestamp);

    p_result := 'SUCCESS';

    <<cleanup>>
    -- 无论成功/失败，锁必须释放
    DELETE FROM distributed_locks WHERE lock_key = v_lock_key;
    COMMIT;

  EXCEPTION
    WHEN OTHERS THEN
      DELETE FROM distributed_locks WHERE lock_key = v_lock_key;
      p_result := 'ERROR:' || SQLERRM;
      RAISE;
  END;

  -- ----------------------------------------------------------
  -- 3. GOTO 模式B：循环模拟（向后跳转 + 分段提交）
  -- ----------------------------------------------------------
  PROCEDURE sp_goto_loop_purge(
    p_retention_days IN INT,
    p_deleted_cnt    OUT INT
  ) AS
    v_deleted     INT := 0;
    v_batch       INT := 500;
    v_rowcount    INT;
    v_cutoff      TIMESTAMP;
  BEGIN
    v_cutoff := current_timestamp - (p_retention_days || ' days')::interval;

    <<purge_loop>>
    -- 使用LIMIT批量删除（GaussDB语法）
    DELETE FROM operation_logs
    WHERE create_time < v_cutoff
    AND log_id IN (
      SELECT log_id FROM operation_logs
      WHERE create_time < v_cutoff
      LIMIT v_batch
    );

    GET DIAGNOSTICS v_rowcount = ROW_COUNT;
    v_deleted := v_deleted + v_rowcount;

    -- 分段提交，避免长事务
    IF v_rowcount > 0 THEN
      COMMIT;
      sp_autonomous_audit('PURGE_BATCH', 'Deleted ' || v_rowcount || ' rows');
    END IF;

    IF v_rowcount = v_batch THEN
      GOTO purge_loop;  -- 还有数据，继续循环
    END IF;

    p_deleted_cnt := v_deleted;
    COMMIT;
  END;

  -- ----------------------------------------------------------
  -- 4. GOTO 模式C：逻辑跳过（向前跳转）
  -- ----------------------------------------------------------
  PROCEDURE sp_goto_skip_logic(
    p_report_type IN  VARCHAR2,
    p_biz_date    IN  VARCHAR2,
    p_content     OUT CLOB
  ) AS
    v_header      VARCHAR2(500);
    v_detail      CLOB := '';
    v_date        DATE := TO_DATE(p_biz_date, 'YYYY-MM-DD');
  BEGIN
    v_header := '=== 清算报告 (' || p_biz_date || ') ===' || CHR(10);

    -- 摘要模式直接跳过明细
    IF UPPER(p_report_type) = 'SUMMARY' THEN
      GOTO assemble_output;
    END IF;

    -- 明细模式：复杂聚合（可能被跳过）
    FOR rec IN (
      SELECT region_code,
             COUNT(*) AS cnt,
             SUM(settle_amount) AS amt,
             AVG(fee_rate) AS avg_fee
      FROM settlement
      WHERE settle_date = v_date
      GROUP BY region_code
      HAVING COUNT(*) > 10
      ORDER BY amt DESC
    ) LOOP
      v_detail := v_detail || rec.region_code || '|' || rec.cnt || '|'
                  || rec.amt || '|' || rec.avg_fee || CHR(10);
    END LOOP;

    <<assemble_output>>
    IF UPPER(p_report_type) = 'SUMMARY' THEN
      SELECT '总计:' || COUNT(*) || '笔,金额:' || COALESCE(SUM(settle_amount),0)
      INTO v_detail
      FROM settlement WHERE settle_date = v_date;
    END IF;

    p_content := v_header || v_detail || CHR(10) || 'Generated at:' || current_timestamp;
  END;

  -- ----------------------------------------------------------
  -- 5. GOTO 模式D：深层嵌套跳出（跨层跳转）
  -- ----------------------------------------------------------
  PROCEDURE sp_goto_deep_escape(
    p_batch_id    IN  BIGINT,
    p_invalid_cnt OUT INT
  ) AS
    v_invalid     INT := 0;
  BEGIN
    FOR main_rec IN (
      SELECT order_id, customer_id, order_type
      FROM bulk_orders
      WHERE batch_id = p_batch_id AND process_flag = 'N'
    ) LOOP
      <<next_order>>

      -- 第一层：客户黑名单检查
      FOR black_rec IN (
        SELECT 1 FROM black_list WHERE customer_id = main_rec.customer_id AND active = 'Y'
      ) LOOP
        v_invalid := v_invalid + 1;
        UPDATE bulk_orders SET process_flag = 'BLACKLIST', process_time = current_timestamp
        WHERE order_id = main_rec.order_id;
        GOTO next_order;  -- 跳出到外层循环下一条
      END LOOP;

      -- 第二层：风控规则检查
      FOR risk_rec IN (
        SELECT rule_id, threshold FROM risk_rules WHERE rule_type = main_rec.order_type
      ) LOOP
        DECLARE
          v_score INT;
        BEGIN
          SELECT risk_score INTO v_score FROM customer_risk
          WHERE customer_id = main_rec.customer_id AND rule_id = risk_rec.rule_id;

          IF v_score > risk_rec.threshold THEN
            v_invalid := v_invalid + 1;
            UPDATE bulk_orders SET process_flag = 'RISK_REJECT', reject_reason = 'RULE_' || risk_rec.rule_id
            WHERE order_id = main_rec.order_id;
            GOTO next_order;  -- 从第二层直接跳出
          END IF;
        EXCEPTION
          WHEN NO_DATA THEN
            NULL; -- 无评分记录，视为通过
        END;
      END LOOP;

      -- 第三层：库存检查（最内层）
      FOR stock_rec IN (
        SELECT product_id, required_qty FROM order_items WHERE order_id = main_rec.order_id
      ) LOOP
        DECLARE
          v_stock INT;
        BEGIN
          SELECT available_qty INTO v_stock FROM inventory WHERE product_id = stock_rec.product_id;
          IF v_stock < stock_rec.required_qty THEN
            v_invalid := v_invalid + 1;
            UPDATE bulk_orders SET process_flag = 'NO_STOCK', reject_reason = 'PROD_' || stock_rec.product_id
            WHERE order_id = main_rec.order_id;
            GOTO next_order;  -- 从第三层直接跳到外层
          END IF;
        END;
      END LOOP;

      -- 全部通过
      UPDATE bulk_orders SET process_flag = 'PASSED', process_time = current_timestamp
      WHERE order_id = main_rec.order_id;

    END LOOP;

    p_invalid_cnt := v_invalid;
    COMMIT;
  END;

  -- ----------------------------------------------------------
  -- 6. GOTO 模式E：网状状态机（多状态互相跳转）
  -- ----------------------------------------------------------
  PROCEDURE sp_goto_state_machine(
    p_order_id    IN  BIGINT,
    p_event       IN  VARCHAR2,
    p_final_state OUT VARCHAR2
  ) AS
    v_state       VARCHAR2(20) := 'INIT';
    v_retry       INT := 0;
    v_max_retry   INT := 3;
  BEGIN
    <<state_init>>
    IF p_event = 'SUBMIT' THEN
      UPDATE orders SET status = 'PENDING', submit_time = current_timestamp
      WHERE order_id = p_order_id;
      v_state := 'PENDING';
      GOTO state_pending;
    ELSIF p_event = 'RESUME' THEN
      -- 从存档恢复，直接到检查状态
      GOTO state_check;
    END IF;
    GOTO state_done;

    <<state_pending>>
    IF p_event = 'PAY' THEN
      UPDATE orders SET status = 'PAID', pay_time = current_timestamp
      WHERE order_id = p_order_id;
      v_state := 'PAID';
      GOTO state_paid;
    ELSIF p_event = 'CANCEL' THEN
      UPDATE orders SET status = 'CANCELLED', cancel_time = current_timestamp
      WHERE order_id = p_order_id;
      v_state := 'CANCELLED';
      GOTO state_done;
    ELSIF p_event = 'TIMEOUT' THEN
      IF v_retry < v_max_retry THEN
        v_retry := v_retry + 1;
        -- 自循环：更新重试时间，回到 pending 等待
        UPDATE orders SET retry_count = v_retry, last_retry = current_timestamp
        WHERE order_id = p_order_id;
        GOTO state_pending;
      ELSE
        UPDATE orders SET status = 'EXPIRED' WHERE order_id = p_order_id;
        v_state := 'EXPIRED';
        GOTO state_done;
      END IF;
    END IF;
    GOTO state_done;

    <<state_paid>>
    IF p_event = 'SHIP' THEN
      UPDATE orders SET status = 'SHIPPED', ship_time = current_timestamp
      WHERE order_id = p_order_id;
      v_state := 'SHIPPED';
      GOTO state_shipped;
    ELSIF p_event = 'REFUND_REQ' THEN
      UPDATE orders SET status = 'REFUNDING', refund_apply_time = current_timestamp
      WHERE order_id = p_order_id;
      v_state := 'REFUNDING';
      GOTO state_refunding;
    END IF;
    GOTO state_done;

    <<state_shipped>>
    IF p_event = 'DELIVER' THEN
      UPDATE orders SET status = 'COMPLETED', complete_time = current_timestamp
      WHERE order_id = p_order_id;
      v_state := 'COMPLETED';
    ELSIF p_event = 'RETURN' THEN
      -- 退货：回退到退款状态
      UPDATE orders SET status = 'REFUNDING', refund_apply_time = current_timestamp
      WHERE order_id = p_order_id;
      v_state := 'REFUNDING';
      GOTO state_refunding;
    END IF;
    GOTO state_done;

    <<state_refunding>>
    IF p_event = 'REFUND_APPROVE' THEN
      UPDATE orders SET status = 'REFUNDED', refund_time = current_timestamp
      WHERE order_id = p_order_id;
      v_state := 'REFUNDED';
      GOTO state_done;
    ELSIF p_event = 'REFUND_REJECT' THEN
      -- 关键：状态回退到 PAID
      UPDATE orders SET status = 'PAID', refund_reject_time = current_timestamp
      WHERE order_id = p_order_id;
      v_state := 'PAID';
      GOTO state_paid;  -- 回退跳转！
    ELSIF p_event = 'PARTIAL_REFUND' THEN
      -- 部分退款后回到 PAID 继续发货流程
      UPDATE orders SET status = 'PAID', partial_refund_amt = 100
      WHERE order_id = p_order_id;
      v_state := 'PAID';
      GOTO state_paid;
    END IF;
    GOTO state_done;

    <<state_check>>
    -- 恢复后的检查状态
    DECLARE
      v_db_state VARCHAR2(20);
    BEGIN
      SELECT status INTO v_db_state FROM orders WHERE order_id = p_order_id;
      v_state := v_db_state;
      IF v_db_state = 'PENDING' THEN GOTO state_pending; END IF;
      IF v_db_state = 'PAID' THEN GOTO state_paid; END IF;
      IF v_db_state = 'SHIPPED' THEN GOTO state_shipped; END IF;
      IF v_db_state = 'REFUNDING' THEN GOTO state_refunding; END IF;
    END;
    GOTO state_done;

    <<state_done>>
    p_final_state := v_state;
    INSERT INTO state_transitions(order_id, event, from_state, to_state, trans_time)
    VALUES(p_order_id, p_event, 'AUTO', v_state, current_timestamp);
    COMMIT;
  END;

  -- ----------------------------------------------------------
  -- 7. DBE_SCHEDULER 动态任务派发（异步子任务）
  -- ----------------------------------------------------------
  PROCEDURE sp_dynamic_scheduler_dispatch(
    p_task_list   IN  TEXT,
    p_job_prefix  IN  VARCHAR2
  ) AS
    v_tasks       TEXT[];
    v_task_id     BIGINT;
    v_job_name    VARCHAR2(100);
    v_sql         VARCHAR2(500);
  BEGIN
    -- 将逗号分隔字符串转为数组
    v_tasks := string_to_array(p_task_list, ',');

    FOR i IN 1..array_length(v_tasks, 1) LOOP
      v_task_id := v_tasks[i]::BIGINT;
      v_job_name := p_job_prefix || v_task_id || '_' || TO_CHAR(current_timestamp, 'HH24MISS');

      -- 动态创建一次性 Job
      PERFORM DBE_SCHEDULER.create_job(
        job_name        => v_job_name,
        job_type        => 'PLSQL_BLOCK',
        job_action      => 'BEGIN PKG_WARPDRIVER_STRESS_TEST.sp_goto_cleanup_master(' || v_task_id || ', NULL); END;',
        enabled         => FALSE,
        auto_drop       => TRUE,
        comments        => 'Auto dispatch for task ' || v_task_id
      );

      -- 使用 SET_JOB_ARGUMENT_VALUE 绑定参数（STORED_PROCEDURE 模式）
      /*
      PERFORM DBE_SCHEDULER.create_job(
        job_name            => v_job_name,
        job_type            => 'STORED_PROCEDURE',
        job_action          => 'PKG_WARPDRIVER_STRESS_TEST.sp_goto_cleanup_master',
        number_of_arguments => 1,
        enabled             => FALSE,
        auto_drop           => TRUE
      );
      PERFORM DBE_SCHEDULER.set_job_argument_value(
        job_name          => v_job_name,
        argument_position => 1,
        argument_value    => v_task_id::TEXT
      );
      */

      DBE_SCHEDULER.enable(v_job_name);

      -- 记录派发日志
      INSERT INTO job_dispatch_log(job_name, task_id, dispatch_time, status)
      VALUES(v_job_name, v_task_id, current_timestamp, 'DISPATCHED');
    END LOOP;

    COMMIT;
  EXCEPTION
    WHEN OTHERS THEN
      sp_autonomous_audit('SCHEDULER_FAIL', SQLERRM);
      RAISE;
  END;

  -- ----------------------------------------------------------
  -- 8. 递归函数：树形数据清理
  -- ----------------------------------------------------------
  FUNCTION fn_recursive_tree_cleanup(
    p_parent_id   IN  BIGINT,
    p_depth       IN  INT DEFAULT 0
  ) RETURN INT AS
    v_count       INT := 0;
    v_child_id    BIGINT;
    v_cur         CURSOR FOR SELECT node_id FROM tree_nodes WHERE parent_id = p_parent_id;
  BEGIN
    IF p_depth > 10 THEN
      RETURN 0; -- 防止无限递归
    END IF;

    OPEN v_cur;
    LOOP
      FETCH v_cur INTO v_child_id;
      EXIT WHEN NOT FOUND;

      -- 递归清理子节点
      v_count := v_count + fn_recursive_tree_cleanup(v_child_id, p_depth + 1);

      -- 清理当前节点关联数据
      DELETE FROM tree_attributes WHERE node_id = v_child_id;
      DELETE FROM tree_nodes WHERE node_id = v_child_id;
      v_count := v_count + 1;
    END LOOP;
    CLOSE v_cur;

    RETURN v_count;
  END;

  -- ----------------------------------------------------------
  -- 9. 数组 + JSONB 处理函数
  -- ----------------------------------------------------------
  FUNCTION fn_array_jsonb_processor(
    p_items       IN  TEXT,  -- JSON数组字符串
    p_discount    IN  NUMERIC
  ) RETURN JSONB AS
    v_arr         JSONB := p_items::JSONB;
    v_result      JSONB := '[]'::JSONB;
    v_item        JSONB;
    v_new_price   NUMERIC(18,4);
    v_idx         INT;
  BEGIN
    IF jsonb_array_length(v_arr) = 0 THEN
      RETURN '{"error":"empty_array"}'::JSONB;
    END IF;

    FOR v_idx IN 0..jsonb_array_length(v_arr) - 1 LOOP
      v_item := v_arr -> v_idx;

      -- JSONB 字段提取与计算
      v_new_price := (v_item ->> 'price')::NUMERIC * (1 - p_discount);

      v_result := v_result || jsonb_build_object(
        'product_id', v_item ->> 'product_id',
        'original_price', (v_item ->> 'price')::NUMERIC,
        'discount_rate', p_discount,
        'final_price', ROUND(v_new_price, 2),
        'processed_at', current_timestamp
      );
    END LOOP;

    RETURN jsonb_build_object(
      'items', v_result,
      'total_count', jsonb_array_length(v_arr),
      'summary_discount', p_discount
    );
  END;

  -- ----------------------------------------------------------
  -- 10. 多游标返回函数
  -- ----------------------------------------------------------
  FUNCTION fn_multi_cursor_return(
    p_customer_id IN  BIGINT,
    p_cur_orders  OUT cur_ref,
    p_cur_payments OUT cur_ref
  ) RETURN INT AS
    v_order_cnt   INT;
    v_pay_cnt     INT;
  BEGIN
    OPEN p_cur_orders FOR
      SELECT order_id, total_amount, status, create_time
      FROM orders
      WHERE customer_id = p_customer_id
      ORDER BY create_time DESC
      LIMIT 100;

    OPEN p_cur_payments FOR
      SELECT payment_id, pay_amount, pay_channel, pay_time
      FROM payments
      WHERE customer_id = p_customer_id
      AND pay_status = 'SUCCESS'
      ORDER BY pay_time DESC
      LIMIT 100;

    SELECT COUNT(*) INTO v_order_cnt FROM orders WHERE customer_id = p_customer_id;
    SELECT COUNT(*) INTO v_pay_cnt FROM payments WHERE customer_id = p_customer_id AND pay_status = 'SUCCESS';

    RETURN v_order_cnt + v_pay_cnt;
  END;

  -- ----------------------------------------------------------
  -- 11. SAVEPOINT 地狱（多层保存点）
  -- ----------------------------------------------------------
  PROCEDURE sp_savepoint_hell(
    p_account_id  IN  BIGINT,
    p_amount      IN  NUMERIC,
    p_final_bal   OUT NUMERIC
  ) AS
    v_balance     NUMERIC(18,4);
    v_sp1         TEXT;
    v_sp2         TEXT;
    v_sp3         TEXT;
    v_ins TEXT;
  BEGIN
    SELECT balance INTO v_balance FROM accounts WHERE account_id = p_account_id;

    -- 第一层保存点：预扣
    v_sp1 := 'sp_pre_' || p_account_id;
    EXECUTE IMMEDIATE 'SAVEPOINT ' || v_sp1;

    UPDATE accounts SET balance = balance - p_amount, pre_amount = p_amount
    WHERE account_id = p_account_id;

    -- 第二层保存点：记账
    v_sp2 := 'sp_acct_' || p_account_id;
    EXECUTE IMMEDIATE 'SAVEPOINT ' || v_sp2;

    INSERT INTO account_journal(account_id, dr_amount, cr_amount, remark)
    VALUES(p_account_id, p_amount, 0, 'PRE_DEBIT');

    -- 模拟某个条件失败，回滚到第一层
    IF p_amount > v_balance THEN
      EXECUTE IMMEDIATE 'ROLLBACK TO SAVEPOINT ' || v_sp1;
      -- 改为冻结操作
      UPDATE accounts SET frozen_flag = 'Y' WHERE account_id = p_account_id;
      INSERT INTO risk_events(account_id, event_type, event_time)
      VALUES(p_account_id, 'OVERDRAW', current_timestamp);
      p_final_bal := v_balance;
      COMMIT;
      RETURN;
    END IF;

    -- 第三层：通知（可独立失败）
    BEGIN
      EXECUTE IMMEDIATE 'SAVEPOINT sp_notify';
      INSERT INTO notifications(account_id, notify_type, content)
      VALUES(p_account_id, 'SMS', 'Debit ' || p_amount);
    EXCEPTION
      WHEN OTHERS THEN
        EXECUTE IMMEDIATE 'INSERT INTO notifications1(' || p_account_id ||', notify_type, content)';
        v_ins = "INSERT INTO";
        v_sp3  := v_ins || ' notifications(' || p_account_id ||', notify_type, content)';
        EXECUTE IMMEDIATE v_sp3;
        -- 通知失败不影响主交易
        NULL;
    END;

    -- 全部成功，释放所有保存点（COMMIT 即可）
    COMMIT;
    SELECT balance INTO p_final_bal FROM accounts WHERE account_id = p_account_id;

  EXCEPTION
    WHEN OTHERS THEN
      ROLLBACK;
      RAISE;
  END;

END PKG_WARPDRIVER_STRESS_TEST;
/
