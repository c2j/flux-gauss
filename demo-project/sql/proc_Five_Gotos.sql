-- 以下是 5 个完整的 GaussDB 存储过程示例，分别对应 5 类 `GOTO` 生存模式，以及平迁到 **Java + iBatis（MyBatis）** 的完整代码。

-- ---

-- ## 模式 A：错误清理出口（统一资源释放）

-- ### 存过代码
-- ```sql
CREATE OR REPLACE PROCEDURE sp_allocate_resource(
    p_task_id   IN  BIGINT,
    p_result    OUT VARCHAR2
) AS
    v_lock_id   BIGINT;
    v_quota     INT;
BEGIN
    -- 获取分布式锁记录
    SELECT nextval('lock_seq') INTO v_lock_id;
    INSERT INTO resource_locks(lock_id, task_id, created_at)
    VALUES(v_lock_id, p_task_id, current_timestamp);

    -- 检查配额
    SELECT remaining_quota INTO v_quota FROM quota WHERE task_type = 'A';
    IF v_quota IS NULL OR v_quota <= 0 THEN
        p_result := 'QUOTA_EMPTY';
        GOTO cleanup;          -- 配额不足，统一出口
    END IF;

    -- 执行业务
    UPDATE quota SET remaining_quota = remaining_quota - 1 WHERE task_type = 'A';
    INSERT INTO task_log(task_id, action) VALUES(p_task_id, 'ALLOCATED');
    p_result := 'SUCCESS';

    <<cleanup>>
    -- 无论成功、失败还是异常，锁必须释放
    DELETE FROM resource_locks WHERE lock_id = v_lock_id;
    COMMIT;

EXCEPTION
    WHEN OTHERS THEN
        DELETE FROM resource_locks WHERE lock_id = v_lock_id;
        p_result := 'ERROR:' || SQLERRM;
        RAISE;
END;
-- ```

-- ### Java + iBatis 迁移
-- ```java
-- @Service
-- public class ResourceService {

--     @Autowired private ResourceLockMapper lockMapper;
--     @Autowired private QuotaMapper quotaMapper;
--     @Autowired private TaskLogMapper taskLogMapper;

--     @Transactional
--     public String allocateResource(Long taskId) {
--         Long lockId = lockMapper.nextLockId();
--         lockMapper.insertLock(lockId, taskId);

--         try {
--             Integer quota = quotaMapper.getRemainingQuota("A");
--             if (quota == null || quota <= 0) {
--                 return "QUOTA_EMPTY";   // 提前走向 finally（对应 GOTO cleanup）
--             }

--             quotaMapper.decrementQuota("A");
--             taskLogMapper.insertLog(taskId, "ALLOCATED");
--             return "SUCCESS";

--         } finally {
--             // 对应 <<cleanup>>
--             lockMapper.deleteByLockId(lockId);
--         }
--     }
-- }
-- ```

-- ```xml
-- <!-- ResourceLockMapper.xml -->
-- <insert id="insertLock">
--     INSERT INTO resource_locks(lock_id, task_id, created_at)
--     VALUES (#{lockId}, #{taskId}, current_timestamp)
-- </insert>

-- <delete id="deleteByLockId">
--     DELETE FROM resource_locks WHERE lock_id = #{lockId}
-- </delete>

-- <select id="nextLockId" resultType="long">
--     SELECT nextval('lock_seq')
-- </select>
-- ```

-- ---

-- ## 模式 B：循环模拟（向后跳转）

-- ### 存过代码
-- ```sql
CREATE OR REPLACE PROCEDURE sp_purge_logs(
    p_retention_days IN  INT,
    p_deleted_count  OUT INT
) AS
    v_deleted   INT := 0;
    v_batch     INT := 1000;
    v_rowcount  INT;
BEGIN
    <<purge_loop>>
    DELETE FROM system_logs
    WHERE create_time < current_timestamp - (p_retention_days || ' days')::interval
    LIMIT v_batch;

    v_rowcount := SQL%ROWCOUNT;
    v_deleted  := v_deleted + v_rowcount;

    IF v_rowcount = v_batch THEN
        COMMIT;                -- 分段提交，避免大事务
        GOTO purge_loop;       -- 继续循环
    END IF;

    p_deleted_count := v_deleted;
    COMMIT;
END;
-- ```

-- ### Java + iBatis 迁移
-- ```java
-- @Service
-- public class LogService {

--     @Autowired private LogMapper logMapper;
--     @Autowired private PlatformTransactionManager txManager;

--     public int purgeLogs(int retentionDays) {
--         int deleted = 0;
--         int batchSize = 1000;
--         int rowCount;
--         LocalDateTime cutoff = LocalDateTime.now().minusDays(retentionDays);

--         // 对应 <<purge_loop>>
--         do {
--             // 还原分段提交语义：每批独立事务
--             TransactionStatus status = txManager.getTransaction(new DefaultTransactionDefinition());
--             try {
--                 rowCount = logMapper.deleteOldLogs(cutoff, batchSize);
--                 deleted += rowCount;
--                 txManager.commit(status);
--             } catch (RuntimeException e) {
--                 txManager.rollback(status);
--                 throw e;
--             }
--         } while (rowCount == batchSize);

--         return deleted;
--     }
-- }
-- ```

-- ```xml
-- <!-- LogMapper.xml -->
-- <delete id="deleteOldLogs">
--     DELETE FROM system_logs
--     WHERE create_time < #{cutoffDate}
--     LIMIT #{batchSize}
-- </delete>
-- ```

-- ---

-- ## 模式 C：逻辑跳过（向前跳转）

-- ### 存过代码
-- ```sql
CREATE OR REPLACE PROCEDURE sp_generate_report(
    p_report_type IN  VARCHAR2,
    p_date        IN  DATE,
    p_content     OUT CLOB
) AS
    v_header VARCHAR2(1000);
    v_detail CLOB := '';
BEGIN
    v_header := 'Report for ' || TO_CHAR(p_date, 'YYYY-MM-DD');

    -- 摘要报告直接跳过明细生成
    IF p_report_type = 'SUMMARY' THEN
        GOTO assemble_report;
    END IF;

    -- 以下是被跳过的复杂明细逻辑
    FOR rec IN (
        SELECT dept_id, SUM(amount) AS amt
        FROM transactions
        WHERE tx_date = p_date
        GROUP BY dept_id
    ) LOOP
        v_detail := v_detail || rec.dept_id || ':' || rec.amt || CHR(10);
    END LOOP;

    <<assemble_report>>
    IF p_report_type = 'SUMMARY' THEN
        p_content := v_header || ' [Summary Mode]';
    ELSE
        p_content := v_header || CHR(10) || v_detail;
    END IF;
END;
-- ```

-- ### Java + iBatis 迁移
-- ```java
-- @Service
-- public class ReportService {

--     @Autowired private TransactionMapper transactionMapper;

--     public String generateReport(String reportType, LocalDate date) {
--         String header = "Report for " + date;

--         // 对应 IF SUMMARY THEN GOTO assemble_report
--         if ("SUMMARY".equals(reportType)) {
--             return header + " [Summary Mode]";  // 直接跳到组装出口
--         }

--         // 对应被跳过的明细逻辑
--         StringBuilder detail = new StringBuilder();
--         List<DeptAmount> rows = transactionMapper.selectDeptAmount(date);
--         for (DeptAmount rec : rows) {
--             detail.append(rec.getDeptId()).append(":").append(rec.getAmt()).append("\n");
--         }

--         // 对应 <<assemble_report>>
--         return header + "\n" + detail;
--     }
-- }
-- ```

-- ```xml
-- <!-- TransactionMapper.xml -->
-- <select id="selectDeptAmount" resultType="com.example.DeptAmount">
--     SELECT dept_id, SUM(amount) AS amt
--     FROM transactions
--     WHERE tx_date = #{date}
--     GROUP BY dept_id
-- </select>
-- ```

-- ---

-- ## 模式 D：深层嵌套跳出（跨层 GOTO）

-- ### 存过代码
-- ```sql
CREATE OR REPLACE PROCEDURE sp_validate_orders(
    p_batch_date    IN  DATE,
    p_invalid_count OUT INT
) AS
    v_invalid INT := 0;
BEGIN
    FOR order_rec IN (
        SELECT order_id, customer_id
        FROM orders
        WHERE create_time = p_batch_date
    ) LOOP
        <<check_next>>

        -- 检查客户信用
        FOR credit_rec IN (
            SELECT credit_level
            FROM customer_credits
            WHERE customer_id = order_rec.customer_id
        ) LOOP
            IF credit_rec.credit_level < 60 THEN
                v_invalid := v_invalid + 1;
                GOTO next_order;  -- 直接跳到外层循环下一条
            END IF;

            -- 检查子订单项（三层嵌套）
            FOR item_rec IN (
                SELECT item_status
                FROM order_items
                WHERE order_id = order_rec.order_id
            ) LOOP
                IF item_rec.item_status = 'BLOCKED' THEN
                    v_invalid := v_invalid + 1;
                    GOTO next_order;  -- 从三层嵌套直接跳出
                END IF;
            END LOOP;
        END LOOP;

        -- 通过全部检查
        UPDATE orders SET process_flag = 'VALIDATED' WHERE order_id = order_rec.order_id;

        <<next_order>>
        NULL;  -- PL/SQL 标签后必须跟语句
    END LOOP;

    p_invalid_count := v_invalid;
END;
-- ```

-- ### Java + iBatis 迁移
-- ```java
-- @Service
-- public class OrderValidateService {

--     @Autowired private OrderMapper orderMapper;
--     @Autowired private CustomerCreditMapper creditMapper;
--     @Autowired private OrderItemMapper itemMapper;

--     @Transactional
--     public int validateOrders(LocalDate batchDate) {
--         int invalidCount = 0;
--         List<Order> orders = orderMapper.selectByDate(batchDate);

--         for (Order orderRec : orders) {
--             // 提取方法：用 return true 替代 GOTO next_order
--             if (isInvalidOrder(orderRec)) {
--                 invalidCount++;
--                 continue;  // 外层继续下一条
--             }
--             orderMapper.updateValidateFlag(orderRec.getOrderId(), "Y");
--         }
--         return invalidCount;
--     }

--     // 内层逻辑完全隔离：return = GOTO next_order
--     private boolean isInvalidOrder(Order orderRec) {
--         List<Credit> credits = creditMapper.selectByCustomerId(orderRec.getCustomerId());
--         for (Credit creditRec : credits) {
--             if (creditRec.getCreditLevel() < 60) {
--                 return true;
--             }

--             List<OrderItem> items = itemMapper.selectByOrderId(orderRec.getOrderId());
--             for (OrderItem itemRec : items) {
--                 if ("BLOCKED".equals(itemRec.getItemStatus())) {
--                     return true;  // 对应从三层嵌套 GOTO 跳出
--                 }
--             }
--         }
--         return false;
--     }
-- }
-- ```

-- ```xml
-- <!-- OrderMapper.xml -->
-- <select id="selectByDate" resultType="com.example.Order">
--     SELECT order_id, customer_id FROM orders WHERE create_date = #{date}
-- </select>

-- <update id="updateValidateFlag">
--     UPDATE orders SET validate_flag = #{flag} WHERE order_id = #{orderId}
-- </update>
-- ```

-- ---

-- ## 模式 E：网状状态机（多状态互相跳转）

-- ### 存过代码
-- ```sql
CREATE OR REPLACE PROCEDURE sp_order_state_machine(
    p_order_id     IN  BIGINT,
    p_event        IN  VARCHAR2,
    p_final_status OUT VARCHAR2
) AS
    v_current     VARCHAR2(20) := 'INIT';
    v_retry_count INT := 0;
BEGIN
    <<state_init>>
    IF p_event = 'SUBMIT' THEN
        UPDATE orders SET status = 'PENDING' WHERE order_id = p_order_id;
        v_current := 'PENDING';
        GOTO state_pending;
    END IF;
    GOTO state_done;  -- 非法事件，结束

    <<state_pending>>
    IF p_event = 'PAY' THEN
        UPDATE orders SET status = 'PAID' WHERE order_id = p_order_id;
        v_current := 'PAID';
        GOTO state_paid;
    ELSIF p_event = 'CANCEL' THEN
        UPDATE orders SET status = 'CANCELLED' WHERE order_id = p_order_id;
        v_current := 'CANCELLED';
        GOTO state_done;
    ELSIF p_event = 'TIMEOUT' AND v_retry_count < 3 THEN
        v_retry_count := v_retry_count + 1;
        GOTO state_pending;  -- 自循环等待
    END IF;
    GOTO state_done;

    <<state_paid>>
    IF p_event = 'SHIP' THEN
        UPDATE orders SET status = 'SHIPPED' WHERE order_id = p_order_id;
        v_current := 'SHIPPED';
        GOTO state_shipped;
    ELSIF p_event = 'REFUND' THEN
        UPDATE orders SET status = 'REFUNDING' WHERE order_id = p_order_id;
        v_current := 'REFUNDING';
        GOTO state_refunding;
    END IF;
    GOTO state_done;

    <<state_shipped>>
    IF p_event = 'DELIVER' THEN
        UPDATE orders SET status = 'COMPLETED' WHERE order_id = p_order_id;
        v_current := 'COMPLETED';
    END IF;
    GOTO state_done;

    <<state_refunding>>
    IF p_event = 'APPROVE' THEN
        UPDATE orders SET status = 'REFUNDED' WHERE order_id = p_order_id;
        v_current := 'REFUNDED';
        GOTO state_done;
    ELSIF p_event = 'REJECT' THEN
        -- 关键：状态回退到 PAID
        UPDATE orders SET status = 'PAID' WHERE order_id = p_order_id;
        v_current := 'PAID';
        GOTO state_paid;
    END IF;
    GOTO state_done;

    <<state_done>>
    p_final_status := v_current;
    INSERT INTO order_state_log(order_id, from_state, to_state, event)
    VALUES(p_order_id, 'UNKNOWN', v_current, p_event);
END;
-- ```

-- ### Java + iBatis 迁移
-- ```java
-- public enum OrderState {
--     INIT, PENDING, PAID, SHIPPED, REFUNDING,
--     COMPLETED, CANCELLED, REFUNDED
-- }

-- @Service
-- public class OrderStateMachineService {

--     @Autowired private OrderMapper orderMapper;
--     @Autowired private OrderStateLogMapper stateLogMapper;

--     @Transactional
--     public String runStateMachine(Long orderId, String event) {
--         OrderState current = OrderState.INIT;
--         int retryCount = 0;
--         boolean running = true;

--         // 还原 GOTO 网状跳转：显式状态机
--         while (running) {
--             switch (current) {
--                 case INIT:
--                     if ("SUBMIT".equals(event)) {
--                         orderMapper.updateStatus(orderId, "PENDING");
--                         current = OrderState.PENDING;
--                     } else {
--                         running = false;  // GOTO state_done
--                     }
--                     break;

--                 case PENDING:
--                     if ("PAY".equals(event)) {
--                         orderMapper.updateStatus(orderId, "PAID");
--                         current = OrderState.PAID;
--                     } else if ("CANCEL".equals(event)) {
--                         orderMapper.updateStatus(orderId, "CANCELLED");
--                         current = OrderState.CANCELLED;
--                         running = false;
--                     } else if ("TIMEOUT".equals(event) && retryCount < 3) {
--                         retryCount++;
--                         // current 保持 PENDING，相当于 GOTO state_pending（自循环）
--                     } else {
--                         running = false;
--                     }
--                     break;

--                 case PAID:
--                     if ("SHIP".equals(event)) {
--                         orderMapper.updateStatus(orderId, "SHIPPED");
--                         current = OrderState.SHIPPED;
--                     } else if ("REFUND".equals(event)) {
--                         orderMapper.updateStatus(orderId, "REFUNDING");
--                         current = OrderState.REFUNDING;
--                     } else {
--                         running = false;
--                     }
--                     break;

--                 case SHIPPED:
--                     if ("DELIVER".equals(event)) {
--                         orderMapper.updateStatus(orderId, "COMPLETED");
--                         current = OrderState.COMPLETED;
--                     }
--                     running = false;  // GOTO state_done
--                     break;

--                 case REFUNDING:
--                     if ("APPROVE".equals(event)) {
--                         orderMapper.updateStatus(orderId, "REFUNDED");
--                         current = OrderState.REFUNDED;
--                         running = false;
--                     } else if ("REJECT".equals(event)) {
--                         // 状态回退：对应 GOTO state_paid
--                         orderMapper.updateStatus(orderId, "PAID");
--                         current = OrderState.PAID;
--                     } else {
--                         running = false;
--                     }
--                     break;

--                 default:
--                     running = false;
--             }
--         }

--         // state_done：统一出口
--         stateLogMapper.insertLog(orderId, "UNKNOWN", current.name(), event);
--         return current.name();
--     }
-- }
-- ```

-- ```xml
-- <!-- OrderMapper.xml -->
-- <update id="updateStatus">
--     UPDATE orders SET status = #{status} WHERE order_id = #{orderId}
-- </update>

-- <insert id="insertLog">
--     INSERT INTO order_state_log(order_id, from_state, to_state, event, created_at)
--     VALUES(#{orderId}, #{fromState}, #{toState}, #{event}, current_timestamp)
-- </insert>
-- ```

-- ---

-- ## 五类模式对照速查

-- | 模式 | 存过特征 | Java 还原策略 | WarpDriver 生成规则 |
-- |---|---|---|---|
-- | **A. 错误清理出口** | `<<cleanup>>` 在末尾，多处 `GOTO cleanup` | `try-finally`，提前 `return` | 识别末尾标签 + 前向多来源跳转 → 生成 `finally` 块 |
-- | **B. 循环模拟** | 向后跳转到代码前方标签 | `while` / `for` / `do-while` | 识别后向跳转 + 条件判断 → 生成循环结构 |
-- | **C. 逻辑跳过** | 向前跳转，跳过中间段落 | 反转条件，`if-else` 包裹被跳段 | 识别前向跳转 + 单来源 → 反转条件重构 |
-- | **D. 深层嵌套跳出** | 从内层循环跳到外层循环边界 | **提取私有方法** + `return` / `continue` | 识别跨块边界跳转 → 提取方法隔离作用域 |
-- | **E. 网状状态机** | 多标签互相跳转，有回退/自循环 | 枚举状态 + `while-switch` 显式状态机 | 识别多入口多出口图结构 → 生成状态枚举与循环 |

-- 如果你的 WarpDriver 要自动处理这些，建议在内部分两步：**先 CFG（控制流图）分析识别 GOTO 模式分类，再按上表规则生成对应 Java AST**。
