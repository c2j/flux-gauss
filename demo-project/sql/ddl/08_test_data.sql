-- ============================================================
-- Test data (INSERT/UPDATE/SELECT from DDL sections)
-- Auto-consolidated from demo-project/sql/*.sql
-- Each table includes ALL column variants from ALL source files
-- ============================================================

-- Source: gauss_complete_examples.sql
INSERT INTO departments (department_id, department_name, location, manager_id) VALUES
(10, '销售部', '上海', 1001),
(20, '技术部', '北京', 1002),
(30, '财务部', '深圳', 1003),
(40, '人事部', '广州', 1004),
(50, '运营部', '杭州', 1005);


-- Source: gauss_complete_examples.sql
INSERT INTO projects (project_id, project_name, start_date, end_date, budget, status) VALUES
(1, '企业ERP升级', '2024-01-01', '2024-12-31', 5000000, 'ACTIVE'),
(2, '移动端APP开发', '2024-03-01', '2024-09-30', 2000000, 'ACTIVE'),
(3, '数据中台建设', '2024-06-01', '2025-06-30', 8000000, 'ACTIVE'),
(4, '旧系统迁移', '2023-01-01', '2023-12-31', 3000000, 'COMPLETED');


-- Source: gauss_complete_examples.sql
INSERT INTO employees (employee_id, employee_name, department_id, salary, email, hire_date, status, current_project_id) VALUES
(1001, '张三', 10, 85000, 'zhangsan@company.com', '2020-03-15', 'ACTIVE', 1),
(1002, '李四', 20, 92000, 'lisi@company.com', '2019-06-20', 'ACTIVE', 2),
(1003, '王五', 30, 68000, 'wangwu@company.com', '2021-01-10', 'ACTIVE', NULL),
(1004, '赵六', 20, 78000, 'zhaoliu@company.com', '2022-05-08', 'ACTIVE', 2),
(1005, '孙七', 10, 95000, 'sunqi@company.com', '2018-11-01', 'ACTIVE', 1),
(1006, '周八', 40, 55000, 'zhouba@company.com', '2023-03-20', 'ACTIVE', NULL),
(1007, '吴九', 20, 88000, 'wujiu@company.com', '2020-09-15', 'ACTIVE', 3),
(1008, '郑十', 10, 72000, 'zhengshi@company.com', '2022-08-01', 'ACTIVE', 1),
(1009, '钱十一', 30, 62000, 'qian11@company.com', '2023-07-10', 'ACTIVE', NULL),
(1010, '冯十二', 50, 48000, 'feng12@company.com', '2024-01-05', 'ACTIVE', 3),
(1011, '陈十三', 20, 105000, 'chen13@company.com', '2017-04-20', 'ACTIVE', 2),
(1012, '褚十四', 10, 58000, 'chu14@company.com', '2023-11-15', 'ACTIVE', 1),
(1013, '卫十五', 40, 45000, 'wei15@company.com', '2024-02-28', 'ACTIVE', NULL),
(1014, '蒋十六', 20, 98000, 'jiang16@company.com', '2019-12-01', 'ACTIVE', 3),
(1015, '沈十七', 30, 70000, 'shen17@company.com', '2021-08-15', 'ACTIVE', NULL);


-- Source: gauss_complete_examples.sql
INSERT INTO customers (customer_id, customer_name, email, phone) VALUES
(2001, '华为技术有限公司', 'contact@huawei.com', '0755-28780808'),
(2002, '阿里巴巴集团', 'contact@alibaba.com', '0571-85022088'),
(2003, '腾讯科技', 'contact@tencent.com', '0755-86013388'),
(2004, '字节跳动', 'contact@bytedance.com', '010-58341700'),
(2005, '美团点评', 'contact@meituan.com', '010-57376677');


-- Source: gauss_complete_examples.sql
INSERT INTO orders (order_id, customer_id, order_date, order_status, total_amount, priority) VALUES
(3001, 2001, '2024-01-15', 'COMPLETED', 150000, 8),
(3002, 2002, '2024-02-20', 'PROCESSING', 280000, 9),
(3003, 2001, '2024-03-10', 'PENDING', 95000, 5),
(3004, 2003, '2024-03-25', 'PENDING', 320000, 7),
(3005, 2004, '2024-04-05', 'PROCESSING', 180000, 6),
(3006, 2002, '2024-04-18', 'PENDING', 420000, 9),
(3007, 2005, '2024-05-01', 'COMPLETED', 75000, 4),
(3008, 2001, '2024-05-12', 'PENDING', 210000, 8),
(3009, 2003, '2024-05-20', 'PROCESSING', 150000, 6),
(3010, 2004, '2024-06-01', 'PENDING', 380000, 7);


-- Source: gauss_complete_examples.sql
INSERT INTO order_items (item_id, order_id, product_name, quantity, unit_price) VALUES
(4001, 3001, '服务器集群A', 10, 12000),
(4002, 3001, '存储设备B', 5, 6000),
(4003, 3002, '云服务套餐C', 20, 10000),
(4004, 3002, '数据库授权D', 15, 8000),
(4005, 3002, '安全模块E', 8, 5000),
(4006, 3003, '咨询服務F', 1, 95000),
(4007, 3004, 'AI平台G', 1, 320000),
(4008, 3005, '中间件H', 12, 10000),
(4009, 3005, '监控工具I', 6, 10000),
(4010, 3006, '大数据套件J', 1, 420000),
(4011, 3007, '运维服务K', 1, 75000),
(4012, 3008, '容器平台L', 14, 10000),
(4013, 3008, 'DevOps工具M', 7, 10000),
(4014, 3009, '微服务框架N', 10, 10000),
(4015, 3009, 'API网关O', 5, 10000),
(4016, 3010, '区块链平台P', 1, 380000);


-- Source: gauss_complete_examples.sql
INSERT INTO query_results (result_id, query_id, priority, status, amount, query_params) VALUES
(5001, 12345, 9, 'PENDING', 50000, '{"type":"A","region":"east"}'),
(5002, 12345, 7, 'PROCESSING', 32000, '{"type":"A","region":"east"}'),
(5003, 12345, 8, 'PENDING', 78000, '{"type":"A","region":"east"}'),
(5004, 12345, 5, 'COMPLETED', 15000, '{"type":"A","region":"east"}'),
(5005, 12345, 9, 'PENDING', 95000, '{"type":"A","region":"east"}'),
(5006, 12345, 6, 'PROCESSING', 42000, '{"type":"A","region":"east"}'),
(5007, 12345, 8, 'PENDING', 67000, '{"type":"A","region":"east"}'),
(5008, 12345, 4, 'COMPLETED', 23000, '{"type":"A","region":"east"}'),
(5009, 12345, 7, 'PENDING', 54000, '{"type":"A","region":"east"}'),
(5010, 12345, 9, 'PROCESSING', 88000, '{"type":"A","region":"east"}');


-- Source: gauss_complete_examples.sql
INSERT INTO products (product_id, product_name, category_id, price, stock, status) VALUES
(6001, '云服务器ECS', 1, 5000, 100, 'ACTIVE'),
(6002, '对象存储OSS', 1, 2000, 500, 'ACTIVE'),
(6003, '关系数据库RDS', 2, 8000, 50, 'ACTIVE'),
(6004, '缓存服务Redis', 2, 3000, 80, 'ACTIVE'),
(6005, '容器服务K8s', 3, 12000, 30, 'ACTIVE'),
(6006, '微服务引擎MSE', 3, 15000, 20, 'ACTIVE'),
(6007, 'AI推理平台', 4, 25000, 10, 'ACTIVE'),
(6008, '大数据分析EMR', 4, 18000, 15, 'ACTIVE'),
(6009, '安全防护WAF', 5, 6000, 40, 'ACTIVE'),
(6010, '身份认证IAM', 5, 4000, 60, 'ACTIVE');


-- Source: gauss_insert_all_styles.sql
INSERT INTO departments (dept_id, dept_name, location, budget, manager_id) VALUES
(10, '销售部', '上海', 5000000, 1001),
(20, '技术部', '北京', 8000000, 1002),
(30, '财务部', '深圳', 3000000, 1003);


-- Source: gauss_select_all_styles.sql
INSERT INTO departments (dept_id, dept_name, location, budget, is_active) VALUES
(10, '销售部', '上海', 5000000, 1),
(20, '技术部', '北京', 8000000, 1),
(30, '财务部', '深圳', 3000000, 1),
(40, '人事部', '广州', 2000000, 0),
(50, '运营部', '杭州', 4000000, 1);


-- Source: gauss_select_all_styles.sql
INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, hire_date, status, manager_id, email, phone, address) VALUES
(1001, '张三', 10,  8000, 0.10, '2018-03-15', 'ACTIVE',   NULL, 'zhangsan@hx.com', '13800138001', '上海市浦东新区'),
(1002, '李四', 20, 12000, 0.08, '2017-06-20', 'ACTIVE',   NULL, 'lisi@hx.com',     '13800138002', '北京市海淀区'),
(1003, '王五', 10,  9000, 0.12, '2021-01-10', 'ACTIVE', 1001, 'wangwu@hx.com',   '13800138003', '上海市黄浦区'),
(1004, '赵六', 30,  7000, 0.06, '2022-05-08', 'ACTIVE', 1003, 'zhaoliu@hx.com',  '13800138004', '深圳市南山区'),
(1005, '孙七', 20, 15000, 0.15, '2016-11-01', 'ACTIVE', 1002, 'sunqi@hx.com',    '13800138005', '北京市朝阳区'),
(1006, '周八', 10,  6500, 0.05, '2023-03-20', 'INACTIVE', 1001, 'zhouba@hx.com',   '13800138006', '上海市徐汇区'),
(1007, '吴九', 20, 11000, 0.11, '2019-09-15', 'ACTIVE', 1002, 'wujiu@hx.com',    '13800138007', '北京市昌平区'),
(1008, '郑十', 30,  8500, 0.09, '2022-08-01', 'ACTIVE', 1003, 'zhengshi@hx.com', '13800138008', '深圳市福田区'),
(1009, '钱十一', 10,  5500, 0.04, '2023-12-01', 'ACTIVE', 1001, 'qian11@hx.com',   '13800138009', '上海市静安区'),
(1010, '冯十二', 20,  9500, 0.07, '2020-02-15', 'INACTIVE', 1002, 'feng12@hx.com',   '13800138010', '北京市大兴区'),
(1011, '陈十三', 40,  6000, 0.05, '2021-07-20', 'ACTIVE', NULL, 'chen13@hx.com',   '13800138011', '广州市天河区'),
(1012, '褚十四', 40,  5800, 0.04, '2022-01-10', 'ACTIVE', NULL, 'chu14@hx.com',    '13800138012', '广州市越秀区');


-- Source: gauss_select_all_styles.sql
INSERT INTO emp_performance (perf_id, emp_id, perf_year, perf_quarter, perf_score, perf_grade, eval_date) VALUES
(1, 1001, 2024, 1, 92.5, 'A', '2024-01-15'),
(2, 1001, 2024, 2, 88.0, 'B', '2024-04-15'),
(3, 1002, 2024, 1, 85.0, 'B', '2024-01-20'),
(4, 1002, 2024, 2, 90.0, 'A', '2024-04-20'),
(5, 1003, 2024, 1, 65.0, 'D', '2024-01-10'),
(6, 1003, 2024, 2, 70.0, 'C', '2024-04-10'),
(7, 1004, 2024, 1, 78.0, 'C', '2024-01-18'),
(8, 1004, 2024, 2, 82.0, 'B', '2024-04-18'),
(9, 1005, 2024, 1, 95.0, 'A', '2024-01-25'),
(10, 1005, 2024, 2, 93.0, 'A', '2024-04-25'),
(11, 1006, 2024, 1, 55.0, 'D', '2024-01-12'),
(12, 1006, 2024, 2, 58.0, 'D', '2024-04-12'),
(13, 1007, 2024, 1, 88.0, 'B', '2024-01-22'),
(14, 1007, 2024, 2, 91.0, 'A', '2024-04-22'),
(15, 1008, 2024, 1, 72.0, 'C', '2024-01-16'),
(16, 1008, 2024, 2, 75.0, 'C', '2024-04-16'),
(17, 1009, 2024, 1, 45.0, 'D', '2024-01-08'),
(18, 1009, 2024, 2, 50.0, 'D', '2024-04-08'),
(19, 1010, 2024, 1, 60.0, 'D', '2024-01-14'),
(20, 1010, 2024, 2, 62.0, 'D', '2024-04-14'),
(21, 1011, 2024, 1, 70.0, 'C', '2024-01-19'),
(22, 1011, 2024, 2, 73.0, 'C', '2024-04-19'),
(23, 1012, 2024, 1, 68.0, 'C', '2024-01-11'),
(24, 1012, 2024, 2, 71.0, 'C', '2024-04-11');


-- Source: gauss_select_all_styles.sql
INSERT INTO emp_projects (project_id, emp_id, role, hours_per_week, start_date, end_date) VALUES
(1, 1001, 'MANAGER', 20, '2024-01-01', NULL),
(1, 1003, 'MEMBER',  15, '2024-01-01', NULL),
(1, 1006, 'MEMBER',  10, '2024-01-01', '2024-03-31'),
(2, 1002, 'MANAGER', 25, '2024-02-01', NULL),
(2, 1005, 'LEAD',    20, '2024-02-01', NULL),
(2, 1007, 'MEMBER',  15, '2024-02-01', NULL),
(3, 1004, 'MANAGER', 15, '2023-06-01', '2024-01-31'),
(3, 1008, 'MEMBER',  10, '2023-06-01', '2024-01-31'),
(4, 1001, 'MANAGER', 10, '2023-01-01', '2023-12-31'),
(4, 1009, 'MEMBER',   8, '2023-01-01', '2023-12-31');


-- Source: gauss_select_all_styles.sql
INSERT INTO sales_data (sale_id, emp_id, sale_date, region, product_a_qty, product_b_qty, product_c_qty, product_a_amt, product_b_amt, product_c_amt) VALUES
(1, 1001, '2024-01-15', 'EAST', 100, 50, 30, 50000, 25000, 15000),
(2, 1001, '2024-02-15', 'EAST', 120, 60, 40, 60000, 30000, 20000),
(3, 1003, '2024-01-20', 'EAST',  80, 40, 20, 40000, 20000, 10000),
(4, 1002, '2024-01-25', 'NORTH', 90, 45, 25, 45000, 22500, 12500),
(5, 1005, '2024-02-10', 'NORTH', 110, 55, 35, 55000, 27500, 17500),
(6, 1007, '2024-01-30', 'NORTH', 70, 35, 15, 35000, 17500, 7500),
(7, 1004, '2024-01-18', 'SOUTH', 60, 30, 10, 30000, 15000, 5000),
(8, 1008, '2024-02-05', 'SOUTH', 85, 42, 22, 42500, 21000, 11000);


-- Source: gauss_select_all_styles.sql
INSERT INTO time_dim (date_id, full_date, year_num, quarter_num, month_num, day_num, weekday_name, is_holiday) VALUES
(20240101, '2024-01-01', 2024, 1, 1, 1, 'Monday',    1),
(20240115, '2024-01-15', 2024, 1, 1, 15, 'Monday',   0),
(20240201, '2024-02-01', 2024, 1, 2, 1, 'Thursday', 0),
(20240215, '2024-02-15', 2024, 1, 2, 15, 'Thursday',0),
(20240301, '2024-03-01', 2024, 1, 3, 1, 'Friday',   0),
(20240315, '2024-03-15', 2024, 1, 3, 15, 'Friday',  0),
(20240401, '2024-04-01', 2024, 2, 4, 1, 'Monday',    0),
(20240415, '2024-04-15', 2024, 2, 4, 15, 'Monday',  0);


-- Source: gauss_function_calls.sql
INSERT INTO departments (dept_id, dept_name, location, budget) VALUES
(10, '销售部', '上海', 5000000),
(20, '技术部', '北京', 8000000),
(30, '财务部', '深圳', 3000000);


-- Source: gauss_function_calls.sql
INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, hire_date, status, manager_id) VALUES
(1001, '张三', 10,  8000, 0.10, '2020-03-15', 'ACTIVE', NULL),
(1002, '李四', 20, 12000, 0.08, '2019-06-20', 'ACTIVE', NULL),
(1003, '王五', 10,  9000, 0.12, '2021-01-10', 'ACTIVE', 1001),
(1004, '赵六', 30,  7000, 0.06, '2022-05-08', 'ACTIVE', 1003),
(1005, '孙七', 20, 15000, 0.15, '2018-11-01', 'ACTIVE', 1002),
(1006, '周八', 10,  6500, 0.05, '2023-03-20', 'INACTIVE', 1001),
(1007, '吴九', 20, 11000, 0.11, '2020-09-15', 'ACTIVE', 1002),
(1008, '郑十', 30,  8500, 0.09, '2022-08-01', 'ACTIVE', 1003);


-- Source: gauss_update_all_styles.sql
INSERT INTO departments (dept_id, dept_name, location, budget, manager_id) VALUES
(10, '销售部', '上海', 5000000, 1001),
(20, '技术部', '北京', 8000000, 1002),
(30, '财务部', '深圳', 3000000, 1003);


-- Source: gauss_update_all_styles.sql
INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, allowance, total_salary, status, manager_id, hire_date, last_update) VALUES
(1001, '张三', 10,  8000, 0.10,  500,  NULL, 'ACTIVE', NULL, '2020-03-15', '2024-01-01'),
(1002, '李四', 20, 12000, 0.08, 1000,  NULL, 'ACTIVE', NULL, '2019-06-20', '2024-01-01'),
(1003, '王五', 10,  9000, 0.12,  800,  NULL, 'ACTIVE', 1001, '2021-01-10', '2024-01-01'),
(1004, '赵六', 30,  7000, 0.06,  600,  NULL, 'ACTIVE', 1003, '2022-05-08', '2024-01-01'),
(1005, '孙七', 20, 15000, 0.15, 1200,  NULL, 'ACTIVE', 1002, '2018-11-01', '2024-01-01'),
(1006, '周八', 10,  6500, 0.05,  400,  NULL, 'INACTIVE', 1001, '2023-03-20', '2024-01-01'),
(1007, '吴九', 20, 11000, 0.11,  900,  NULL, 'ACTIVE', 1002, '2020-09-15', '2024-01-01'),
(1008, '郑十', 30,  8500, 0.09,  700,  NULL, 'ACTIVE', 1003, '2022-08-01', '2024-01-01');


-- Source: gauss_update_all_styles.sql
INSERT INTO emp_performance (emp_id, perf_score, perf_grade, eval_year) VALUES
(1001, 92.5, 'A', 2024),
(1002, 85.0, 'B', 2024),
(1003, 78.0, 'C', 2024),
(1004, 65.0, 'D', 2024),
(1005, 95.0, 'A', 2024),
(1006, 72.0, 'C', 2024),
(1007, 88.0, 'B', 2024),
(1008, 91.0, 'A', 2024);


-- Source: gauss_delete_all_styles.sql
INSERT INTO departments (dept_id, dept_name, location, budget, is_active) VALUES
(10, '销售部', '上海', 5000000, 1),
(20, '技术部', '北京', 8000000, 1),
(30, '财务部', '深圳', 3000000, 1),
(40, '人事部', '广州', 2000000, 0);  -- 已停用部门

-- 插入员工数据
INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, hire_date, status, manager_id, is_deleted) VALUES
(1001, '张三', 10,  8000, 0.10, '2018-03-15', 'ACTIVE',   NULL, 0),
(1002, '李四', 20, 12000, 0.08, '2017-06-20', 'ACTIVE',   NULL, 0),
(1003, '王五', 10,  9000, 0.12, '2021-01-10', 'ACTIVE', 1001, 0),
(1004, '赵六', 30,  7000, 0.06, '2022-05-08', 'ACTIVE', 1003, 0),
(1005, '孙七', 20, 15000, 0.15, '2016-11-01', 'ACTIVE', 1002, 0),
(1006, '周八', 10,  6500, 0.05, '2023-03-20', 'INACTIVE', 1001, 0),
(1007, '吴九', 20, 11000, 0.11, '2019-09-15', 'ACTIVE', 1002, 0),
(1008, '郑十', 30,  8500, 0.09, '2022-08-01', 'ACTIVE', 1003, 0),
(1009, '钱十一', 10,  5500, 0.04, '2023-12-01', 'ACTIVE', 1001, 0),
(1010, '冯十二', 20,  9500, 0.07, '2020-02-15', 'INACTIVE', 1002, 0),
(1011, '陈十三', 40,  6000, 0.05, '2021-07-20', 'ACTIVE', NULL, 0),  -- 已停用部门
(1012, '褚十四', 40,  5800, 0.04, '2022-01-10', 'ACTIVE', NULL, 0);  -- 已停用部门

-- 插入绩效数据
INSERT INTO emp_performance (perf_id, emp_id, perf_year, perf_score, perf_grade, eval_date) VALUES
(1, 1001, 2024, 92.5, 'A', '2024-01-15'),
(2, 1002, 2024, 85.0, 'B', '2024-01-20'),
(3, 1003, 2024, 65.0, 'D', '2024-01-10'),
(4, 1004, 2024, 78.0, 'C', '2024-01-18'),
(5, 1005, 2024, 95.0, 'A', '2024-01-25'),
(6, 1006, 2024, 55.0, 'D', '2024-01-12'),
(7, 1007, 2024, 88.0, 'B', '2024-01-22'),
(8, 1008, 2024, 72.0, 'C', '2024-01-16'),
(9, 1009, 2024, 45.0, 'D', '2024-01-08'),
(10, 1010, 2024, 60.0, 'D', '2024-01-14'),
(11, 1011, 2024, 70.0, 'C', '2024-01-19'),
(12, 1012, 2024, 68.0, 'C', '2024-01-11');


-- Source: gauss_delete_all_styles.sql
INSERT INTO emp_projects (project_id, emp_id, role, start_date, end_date) VALUES
(1, 1001, 'MANAGER', '2024-01-01', NULL),
(1, 1003, 'MEMBER', '2024-01-01', NULL),
(1, 1006, 'MEMBER', '2024-01-01', NULL),
(2, 1002, 'MANAGER', '2024-02-01', NULL),
(2, 1005, 'LEAD', '2024-02-01', NULL),
(2, 1007, 'MEMBER', '2024-02-01', NULL),
(3, 1004, 'MANAGER', '2023-06-01', '2024-01-31'),
(3, 1008, 'MEMBER', '2023-06-01', '2024-01-31'),
(4, 1001, 'MANAGER', '2023-01-01', '2023-12-31'),
(4, 1009, 'MEMBER', '2023-01-01', '2023-12-31');


-- Source: gauss_delete_all_styles.sql
INSERT INTO emp_contacts (contact_id, emp_id, contact_type, contact_value) VALUES
(1, 1001, 'PHONE', '13800138001'),
(2, 1001, 'EMAIL', 'zhangsan@company.com'),
(3, 1002, 'PHONE', '13800138002'),
(4, 1003, 'EMAIL', 'wangwu@company.com'),
(5, 1004, 'PHONE', '13800138004'),
(6, 1005, 'EMAIL', 'sunqi@company.com'),
(7, 1006, 'PHONE', '13800138006'),
(8, 1007, 'EMAIL', 'wujiu@company.com');


-- Source: gauss_update_select.sql
INSERT INTO dept_raise_standard (dept_id, dept_name, base_raise_pct, bonus_raise_pct, allowance_add, effective_date, is_active) VALUES
(10, '销售部',  0.15, 0.05, 2000.00, '2024-06-01', 1),
(20, '技术部',  0.12, 0.03, 1500.00, '2024-06-01', 1),
(30, '财务部',  0.08, 0.02, 1000.00, '2024-06-01', 1),
(40, '人事部',  0.10, 0.02, 1200.00, '2024-06-01', 1);


-- Source: gauss_update_select.sql
INSERT INTO perf_coefficient (perf_rating, salary_coeff, bonus_coeff) VALUES
('A', 1.20, 1.30),
('B', 1.10, 1.15),
('C', 1.00, 1.00),
('D', 0.90, 0.80);


-- Source: gauss_update_select.sql
INSERT INTO emp_salary (emp_id, emp_name, dept_id, base_salary, bonus_pct, allowance, total_salary, last_update, update_reason) VALUES
(1001, '张三', 10,  8000.00, 0.10,  500.00,  NULL, '2024-01-01', 'Initial'),
(1002, '李四', 20, 12000.00, 0.08, 1000.00,  NULL, '2024-01-01', 'Initial'),
(1003, '王五', 10,  9000.00, 0.12,  800.00,  NULL, '2024-01-01', 'Initial'),
(1004, '赵六', 30,  7000.00, 0.06,  600.00,  NULL, '2024-01-01', 'Initial'),
(1005, '孙七', 20, 15000.00, 0.15, 1200.00,  NULL, '2024-01-01', 'Initial'),
(1006, '周八', 40,  6500.00, 0.05,  400.00,  NULL, '2024-01-01', 'Initial'),
(1007, '吴九', 10, 11000.00, 0.11,  900.00,  NULL, '2024-01-01', 'Initial'),
(1008, '郑十', 20, 13500.00, 0.09, 1100.00,  NULL, '2024-01-01', 'Initial');


-- Source: gauss_update_select.sql
INSERT INTO emp_performance (emp_id, perf_rating, perf_score, perf_year, perf_quarter) VALUES
(1001, 'A', 92.5, 2024, 1),
(1002, 'B', 85.0, 2024, 1),
(1003, 'C', 78.0, 2024, 1),
(1004, 'D', 65.0, 2024, 1),
(1005, 'A', 95.0, 2024, 1),
(1006, 'C', 72.0, 2024, 1),
(1007, 'B', 88.0, 2024, 1),
(1008, 'A', 91.0, 2024, 1);


-- Source: gauss_update_select.sql
UPDATE emp_salary
SET (base_salary, bonus_pct, allowance) = (
    SELECT
        s.base_salary * (1 + r.base_raise_pct),
        s.bonus_pct + r.bonus_raise_pct,
        s.allowance + r.allowance_add
    FROM emp_salary s
    JOIN dept_raise_standard r ON s.dept_id = r.dept_id
    WHERE s.emp_id = emp_salary.emp_id
      AND r.is_active = 1
      AND r.effective_date <= CURRENT_DATE
)
WHERE EXISTS (
    SELECT 1 FROM dept_raise_standard r
    WHERE r.dept_id = emp_salary.dept_id AND r.is_active = 1
);


-- Source: gauss_update_select.sql
SELECT emp_id, emp_name, dept_id, base_salary, bonus_pct, allowance,
       ROUND(base_salary * (1 + bonus_pct) + allowance, 2) AS calc_total
FROM emp_salary ORDER BY emp_id;


-- Source: gauss_update_select.sql
UPDATE emp_salary SET base_salary =
    CASE emp_id
        WHEN 1001 THEN 8000  WHEN 1002 THEN 12000 WHEN 1003 THEN 9000  WHEN 1004 THEN 7000
        WHEN 1005 THEN 15000 WHEN 1006 THEN 6500  WHEN 1007 THEN 11000 WHEN 1008 THEN 13500
    END,
    bonus_pct =
    CASE emp_id
        WHEN 1001 THEN 0.10 WHEN 1002 THEN 0.08 WHEN 1003 THEN 0.12 WHEN 1004 THEN 0.06
        WHEN 1005 THEN 0.15 WHEN 1006 THEN 0.05 WHEN 1007 THEN 0.11 WHEN 1008 THEN 0.09
    END,
    allowance =
    CASE emp_id
        WHEN 1001 THEN 500  WHEN 1002 THEN 1000 WHEN 1003 THEN 800  WHEN 1004 THEN 600
        WHEN 1005 THEN 1200 WHEN 1006 THEN 400  WHEN 1007 THEN 900  WHEN 1008 THEN 1100
    END,
    total_salary = NULL,
    last_update = '2024-01-01',
    update_reason = 'Initial';


-- Source: gauss_update_select.sql
UPDATE emp_salary
SET (base_salary, bonus_pct, allowance, total_salary, last_update, update_reason) = (
    SELECT
        -- 新基本工资 = 原工资 * 部门涨幅 * 绩效系数
        ROUND(s.base_salary * (1 + r.base_raise_pct) * c.salary_coeff, 2),
        -- 新奖金比例 = 原比例 + 部门涨幅 * 绩效系数
        ROUND(s.bonus_pct + r.bonus_raise_pct * c.bonus_coeff, 4),
        -- 新津贴 = 原津贴 + 部门固定增加
        s.allowance + r.allowance_add,
        -- 新总薪资 = 新基本工资 * (1 + 新奖金比例) + 新津贴
        ROUND(s.base_salary * (1 + r.base_raise_pct) * c.salary_coeff *
              (1 + s.bonus_pct + r.bonus_raise_pct * c.bonus_coeff), 2) +
        s.allowance + r.allowance_add,
        -- 更新时间
        CURRENT_TIMESTAMP,
        -- 更新原因
        'Annual raise: dept=' || r.dept_name || ', perf=' || p.perf_rating
    FROM emp_salary s
    JOIN dept_raise_standard r ON s.dept_id = r.dept_id
    JOIN emp_performance p ON s.emp_id = p.emp_id
    JOIN perf_coefficient c ON p.perf_rating = c.perf_rating
    WHERE s.emp_id = emp_salary.emp_id
      AND r.is_active = 1
      AND p.perf_year = 2024
      AND p.perf_quarter = 1
)
WHERE EXISTS (
    SELECT 1 FROM emp_performance p
    WHERE p.emp_id = emp_salary.emp_id AND p.perf_year = 2024 AND p.perf_quarter = 1
);


-- Source: gauss_update_select.sql
INSERT INTO salary_update_log (
    log_id, emp_id, old_base, new_base, old_bonus_pct, new_bonus_pct,
    old_allowance, new_allowance, old_total, new_total
)
SELECT
    seq_sal_log.NEXTVAL, e.emp_id, e.base_salary, NULL, e.bonus_pct, NULL,
    e.allowance, NULL, e.total_salary, NULL
FROM emp_salary e
WHERE e.dept_id = 20 AND e.base_salary < 15000;


-- Source: gauss_update_select.sql
UPDATE emp_salary
SET (base_salary, bonus_pct, allowance, total_salary) = (
    SELECT
        s.base_salary * 1.25,           -- 技术部专项上调25%
        LEAST(s.bonus_pct + 0.05, 0.30), -- 奖金上限30%
        s.allowance + 3000,              -- 技术津贴
        ROUND(s.base_salary * 1.25 * (1 + LEAST(s.bonus_pct + 0.05, 0.30)) + s.allowance + 3000, 2)
    FROM emp_salary s
    WHERE s.emp_id = emp_salary.emp_id
)
WHERE dept_id = 20
  AND base_salary < 15000
  AND EXISTS (SELECT 1 FROM emp_performance p WHERE p.emp_id = emp_salary.emp_id AND p.perf_rating IN ('A', 'B'));


-- Source: gauss_update_select.sql
UPDATE salary_update_log l
SET (new_base, new_bonus_pct, new_allowance, new_total) = (
    SELECT e.base_salary, e.bonus_pct, e.allowance, e.total_salary
    FROM emp_salary e WHERE e.emp_id = l.emp_id
)
WHERE l.new_base IS NULL;


-- Source: gauss_update_select.sql
SELECT * FROM salary_update_log ORDER BY log_id;


-- Source: gauss_update_select.sql
UPDATE emp_salary
SET (base_salary, allowance, total_salary, update_reason) = (
    SELECT
        -- 新工资 = 部门平均工资 * 0.95（保底95%均值）
        GREATEST(s.base_salary * 1.10, a.avg_salary * 0.95),
        -- 津贴 = 原津贴 + (均值 - 当前工资) * 0.5（差额补贴）
        s.allowance + GREATEST(0, (a.avg_salary - s.base_salary) * 0.5),
        -- 总薪资重新计算
        GREATEST(s.base_salary * 1.10, a.avg_salary * 0.95) * (1 + s.bonus_pct) +
        s.allowance + GREATEST(0, (a.avg_salary - s.base_salary) * 0.5),
        -- 更新原因
        'Low salary adjustment: dept_avg=' || TO_CHAR(ROUND(a.avg_salary, 2)) ||
        ', rank=' || TO_CHAR(r.sal_rank) || '/' || TO_CHAR(a.emp_count)
    FROM emp_salary s
    JOIN dept_avg_salary a ON s.dept_id = a.dept_id
    JOIN (
        SELECT emp_id, dept_id,
               RANK() OVER (PARTITION BY dept_id ORDER BY base_salary) AS sal_rank
        FROM emp_salary
    ) r ON s.emp_id = r.emp_id AND s.dept_id = r.dept_id
    WHERE s.emp_id = emp_salary.emp_id
)
WHERE base_salary < (
    SELECT avg_salary FROM dept_avg_salary a WHERE a.dept_id = emp_salary.dept_id
);


-- Source: gauss_update_select.sql
SELECT e.*, a.avg_salary AS dept_avg
FROM emp_salary e
JOIN dept_avg_salary a ON e.dept_id = a.dept_id
ORDER BY e.dept_id, e.base_salary;


-- Source: gauss_package_constants.sql
INSERT INTO employees (employee_id, employee_name, department_id, salary, status) VALUES
(1, '张三', 10, 85000, 'ACTIVE'),
(2, '李四', 20, 92000, 'ACTIVE'),
(3, '王五', 10, 68000, 'INACTIVE'),
(4, '赵六', 30, 78000, 'ACTIVE'),
(5, '孙七', 20, 95000, 'ACTIVE');


-- Source: pkg_merge_fix1.sql
INSERT INTO dim_product (sk_product, bk_product_code, product_name, category_code, brand_code, unit_cost) VALUES
(1, 'P001', '企业级服务器A型', 'SERVER', 'BRAND_A', 25000),
(2, 'P002', '云存储解决方案', 'STORAGE', 'BRAND_B', 8000),
(3, 'P003', 'AI推理加速卡', 'AI_CHIP', 'BRAND_C', 45000),
(4, 'P004', '数据库中间件', 'SOFTWARE', 'BRAND_A', 15000),
(5, 'P005', '网络安全网关', 'SECURITY', 'BRAND_D', 12000);


-- Source: pkg_merge_fix1.sql
INSERT INTO dim_customer (sk_customer, bk_customer_code, customer_name, customer_type, region_code, credit_level) VALUES
(1, 'C001', '华夏科技集团', 'ENTERPRISE', 'EAST', 'A'),
(2, 'C002', '创新软件公司', 'SMB', 'NORTH', 'B'),
(3, 'C003', '个人用户张三', 'INDIVIDUAL', 'SOUTH', 'C'),
(4, 'C004', '云端数据服务', 'ENTERPRISE', 'EAST', 'A'),
(5, 'C005', '小微工作室', 'SMB', 'WEST', 'D');


-- Source: pkg_merge_fix1.sql
INSERT INTO dim_region (sk_region, bk_region_code, region_name, parent_region, region_level) VALUES
(1, 'EAST', '华东地区', 'CHINA', 1),
(2, 'NORTH', '华北地区', 'CHINA', 1),
(3, 'SOUTH', '华南地区', 'CHINA', 1),
(4, 'WEST', '西部地区', 'CHINA', 1);


-- Source: pkg_merge_fix1.sql
INSERT INTO dim_sales_rep (sk_sales_rep, bk_sales_rep_code, rep_name, team_code, hire_date) VALUES
(1, 'R001', '李明', 'TEAM_A', '2019-03-15'),
(2, 'R002', '王芳', 'TEAM_B', '2020-06-20'),
(3, 'R003', '张伟', 'TEAM_A', '2018-11-01'),
(4, 'R004', '刘洋', 'TEAM_C', '2021-01-10'),
(5, 'R005', '陈静', 'TEAM_B', '2022-08-15');


-- Source: pkg_merge_fix1.sql
INSERT INTO dim_date (sk_date, full_date, year_number, quarter_number, month_number, day_number, weekday_name) VALUES
(20240115, '2024-01-15', 2024, 1, 1, 15, 'Monday'),
(20240220, '2024-02-20', 2024, 1, 2, 20, 'Tuesday'),
(20240310, '2024-03-10', 2024, 1, 3, 10, 'Sunday'),
(20240325, '2024-03-25', 2024, 1, 3, 25, 'Monday'),
(20240405, '2024-04-05', 2024, 2, 4, 5, 'Friday'),
(20240501, '2024-05-01', 2024, 2, 5, 1, 'Wednesday'),
(20240520, '2024-05-20', 2024, 2, 5, 20, 'Monday'),
(20240601, '2024-06-01', 2024, 2, 6, 1, 'Saturday');


-- Source: pkg_merge_fix1.sql
INSERT INTO src_sales_data (src_batch_id, src_sequence, transaction_id, product_code, customer_code,
    sales_amount, sales_quantity, sales_date, region_code, channel_type, sales_rep_code,
    discount_rate, payment_method, delivery_status, data_source, record_hash, src_create_time) VALUES
('BATCH_202401', 1, 'TXN_001', 'P001', 'C001', 250000, 10, '2024-01-15', 'EAST', 'ONLINE', 'R001', 0.10, 'BANK_TRANSFER', 'DELIVERED', 'CRM', 'hash001', SYSTIMESTAMP),
('BATCH_202401', 2, 'TXN_002', 'P002', 'C002', 80000, 10, '2024-01-15', 'NORTH', 'OFFLINE', 'R002', 0.05, 'CASH', 'SHIPPED', 'POS', 'hash002', SYSTIMESTAMP),
('BATCH_202401', 3, 'TXN_003', 'P003', 'C001', 450000, 10, '2024-02-20', 'EAST', 'ONLINE', 'R001', 0.15, 'BANK_TRANSFER', 'PENDING', 'WEB', 'hash003', SYSTIMESTAMP),
('BATCH_202401', 4, 'TXN_004', 'P004', 'C004', 150000, 10, '2024-03-10', 'EAST', 'AGENCY', 'R003', 0.08, 'CREDIT_CARD', 'DELIVERED', 'ERP', 'hash004', SYSTIMESTAMP),
('BATCH_202401', 5, 'TXN_005', 'P005', 'C003', 12000, 1, '2024-03-25', 'SOUTH', 'ONLINE', 'R004', 0.00, 'ALIPAY', 'SHIPPED', 'WEB', 'hash005', SYSTIMESTAMP);


-- Source: PACK_LOG.sql
      INSERT INTO DB_LOG
        (ID,
         PROC_NAME,
         INFO,
         LOG_LEVEL,
         TIME_STAMP,
         CALL_STACK,
         ERR_STACK,
         STEP_NO,
         SQL_TXT,
         SQL_PARAM,
         LOG_DATE
         )
      VALUES
        (LPAD(LOG_SEQ.NEXTVAL, 20, '0'),
         IN_PROCNAME,
         IN_INFO,
         IN_LEVEL,
         to_char(now(), 'YYYYMMDDHH24MISS'),
         V_CALLSTACK,
         V_ERRSTACK,
         IN_STEPNO,
         IN_SQLTXT,
         IN_SQLPARAM,
         to_char(now(), 'YYYYMMDD'));


-- Source: PACK_LOG.sql
      INSERT INTO DB_LOG
        (ID,
         PROC_NAME,
         INFO,
         LOG_LEVEL,
         TIME_STAMP,
         CALL_STACK,
         ERR_STACK,
         STEP_NO,
         SQL_TXT,
         SQL_PARAM,
         LOG_DATE
         )
      VALUES
        (LPAD(LOG_SEQ.NEXTVAL, 20, '0'),
         IN_PROCNAME,
         IN_INFO,
         IN_LEVEL,
         to_char(now(), 'YYYYMMDDHH24MISS'),
         V_CALLSTACK,
         V_ERRSTACK,
         IN_STEPNO,
         IN_SQLTXT,
         IN_SQLPARAM,
         to_char(now(), 'YYYYMMDD'));


-- Source: PKG_2008802001_MGT.sql
    SELECT /*+use_cplan*/COUNT(1)
      INTO totalnum
      FROM dat_clr_cash_dtl t, dat_trustee_acnt_detail d
     WHERE t.trade_code IN ('2008801001', '2008802001')
       AND t.account_id = in_accnt_id
       AND t.match_status = in_match_status
       AND t.account_date BETWEEN nvl(in_accnt_date1, '19000101') AND
           nvl(in_accnt_date2, '99991231')
       AND (t.respond_date BETWEEN nvl(in_respond_date1, '19000101') AND
           nvl(in_respond_date2, '99991231') OR t.respond_date IS NULL)
       AND t.interface_seq = d.interface_seq(+)
       AND (t.operation_status =
           decode(t.trade_code, '2008801001', '0', t.operation_status) OR
           decode(t.trade_code, '2008801001', '0', t.operation_status) IS NULL);


-- Source: PKG_2008802001_MGT.sql
      SELECT /*+use_cplan*/s.account_id,
             s.accname,
             s.account_date,
             s.in_amount,
             s.describe,
             s.recipacc,
             s.recipnam,
             s.account_seqno,
             s.accnt_seqno,
             s.interface_seq,
             s.match_status,
             s.statusname,
             s.respond_date
        FROM (SELECT t.account_id,
                     (SELECT s.accname
                        FROM v_par_asset_acnt_info s
                       WHERE s.asset_acnt_id = t.account_id
                         AND rownum = 1) accname,
                     t.account_date,
                     t.in_amount,
                     t.describe,
                     d.recipacc,
                     d.recipnam,
                     t.account_seqno,
                     t.trade_code,
                     t.accnt_seqno,
                     t.match_status,
                     t.interface_seq,
                     decode(t.match_status, '0', 'δƥ��', '1', '��ƥ��') statusname,
                     t.respond_date,
                     row_number() over(ORDER BY t.account_date DESC, t.account_seqno, t.account_id, t.interface_seq,accno, d.serialno,
                     d.busidate, d.timestmp, d.updtranf, d.revtranf, d.trxcode, d.drcrf, d.amount, d.detailf, d.currtype, d.subcode, d.euoflag) rownm
                FROM dat_clr_cash_dtl t, dat_trustee_acnt_detail d
               WHERE t.trade_code IN ('2008801001', '2008802001')
                 AND t.account_id = in_accnt_id
                 AND t.match_status = in_match_status
                 AND t.account_date BETWEEN nvl(in_accnt_date1, '19000101') AND
                     nvl(in_accnt_date2, '99991231')
                 AND (t.respond_date BETWEEN
                     nvl(in_respond_date1, '19000101') AND
                     nvl(in_respond_date2, '99991231') OR
                     t.respond_date IS NULL)
                 AND t.interface_seq = d.interface_seq(+)
                 AND (t.operation_status =
                     decode(t.trade_code,
                             '2008801001',
                             '0',
                             t.operation_status) OR
                     decode(t.trade_code,
                             '2008801001',
                             '0',
                             t.operation_status) IS NULL)) s
         limit to_number(in_qrynum) offset to_number(in_qrybeginpos)-1;


-- Source: PKG_2008802001_MGT.sql
      SELECT t.trade_code, t.in_amount
        INTO v_trade_code, v_amout
        FROM dat_clr_cash_dtl t
       WHERE t.account_date = in_accnt_date
         AND t.account_seqno = in_seq_no
         AND t.account_id = in_accnt_id
         AND t.interface_seq = in_interface_seq;


-- Source: PKG_2008802001_MGT.sql
    UPDATE dat_clr_cash_dtl t
       SET t.match_status = '1', --��ƥ��
           t.trade_code   = '2008802001', --ҵ�����͸�Ϊ����֧���˻�
           t.respond_date = v_respond_date
     WHERE t.account_date = in_accnt_date
       AND t.account_seqno = in_seq_no
       AND t.account_id = in_accnt_id
       AND t.interface_seq = in_interface_seq;


-- Source: PKG_2008802001_MGT.sql
      SELECT t.accnt_flag
        INTO v_flag
        FROM prm_sth_payback_accnt_date t
       WHERE t.accnt_id = in_accnt_id;


-- Source: PKG_2008802001_MGT.sql
      SELECT t.trade_code
        INTO v_trade_code
        FROM dat_clr_cash_dtl t
       WHERE t.account_date = in_accnt_date
         AND t.account_seqno = in_seq_no
         AND t.account_id = in_accnt_id
         AND t.interface_seq = in_interface_seq;


-- Source: PKG_2008802001_MGT.sql
    UPDATE dat_clr_cash_dtl t
       SET t.match_status = '1', --��ƥ��
           t.trade_code   = '2008802001', --ҵ�����͸�Ϊ����֧���˻�
           t.respond_date = in_respond_date
     WHERE t.account_date = in_accnt_date
       AND t.account_seqno = in_seq_no
       AND t.account_id = in_accnt_id
       AND t.interface_seq = in_interface_seq;


-- Source: PKG_2008802001_MGT.sql
      SELECT decode(t.accnt_flag, '1', in_accnt_date, in_respond_date)
        INTO v_date
        FROM prm_sth_payback_accnt_date t
       WHERE t.accnt_id = in_accnt_id;


-- Source: PKG_2008802001_MGT.sql
      SELECT t.trade_code, t.in_amount
        INTO v_trade_code, v_amout
        FROM dat_clr_cash_dtl t
       WHERE t.account_date = in_accnt_date
         AND t.account_seqno = in_seq_no
         AND t.account_id = in_accnt_id
         AND t.interface_seq = in_interface_seq;


-- Source: PKG_2008802001_MGT.sql
    UPDATE dat_clr_cash_dtl t
       SET t.match_status     = '0', --��ƥ��
           t.respond_date     = '',
           t.trade_code       = '2008801001',
           t.operation_status = '0'
     WHERE t.account_date = in_accnt_date
       AND t.account_seqno = in_seq_no
       AND t.account_id = in_accnt_id
       AND t.interface_seq = in_interface_seq;


-- Source: PKG_2008802001_MGT.sql
      SELECT t.accnt_flag
        INTO v_flag
        FROM prm_sth_payback_accnt_date t
       WHERE t.accnt_id = in_accnt_id;


-- Source: PKG_2008802001_MGT.sql
      SELECT MAX(substr(t.send_tm, 0, 8))
        INTO out_date
        FROM dat_zl_batchpayment t, par_sys_plan s
       WHERE t.data_date = v_date
         AND t.beneaccount = v_recipacc
         AND t.planid = s.plan_id
         AND s.acnt_id = in_accnt_id
         AND t.successflag = '1'; --ʧ��*/
      /*SELECT MAX(substr(t.send_tm, 0, 8))
        INTO out_date
        FROM dat_zl_batchpayment  t,
             dat_batchpay_errback b,
             dat_clr_cash_dtl     s
       WHERE b.account_seqno = s.account_seqno
         AND t.referenceno = b.referenceno
         AND s.account_id = in_accnt_id
         AND s.account_date = in_accnt_date
         AND s.account_seqno = in_seq_no
         AND s.interface_seq = in_interface_seq
         AND t.planid = b.planid
         AND t.data_date = b.data_date;*/
     SELECT MAX(substr(t.send_tm, 0, 8))
       INTO out_date
       FROM dat_zl_batchpayment     t,
            par_sys_plan            b,
            dat_clr_cash_dtl        s,
            dat_trustee_acnt_detail c
      WHERE s.account_id = in_accnt_id
        AND s.account_date = in_accnt_date
        AND s.account_seqno = in_seq_no
        AND s.interface_seq = in_interface_seq
        AND t.planid = b.plan_id
        AND b.acnt_id = s.account_id
        AND s.interface_seq = c.interface_seq
        AND t.beneaccount = c.recipacc
        AND t.apaysum = s.in_amount
        AND s.account_date BETWEEN t.apaydate AND substr(t.send_tm, 1, 8);


-- Source: PKG_2008802001_MGT.sql
        -- SELECT MAX(to_char(t.pay_tm, 'yyyymmdd'))
        --   INTO out_date
        --   FROM tmp_batchpay_submit t \*, tmp_batch_payment_03092_03093 s*\
        --  WHERE t.status = '26' --�˿�
        --    AND t.send_account = v_accno
        --    AND t.inst_date = in_accnt_date
        --    AND t.rece_account = v_recipacc;*/
        /*SELECT MAX(to_char(t.pay_tm, 'yyyymmdd'))
          INTO out_date
          FROM tmp_batchpay_submit  t,
               dat_batchpay_errback b,
               dat_clr_cash_dtl     s
         WHERE b.account_seqno = s.account_seqno
           AND t.referenceno = b.referenceno
           AND s.account_id = in_accnt_id
           AND s.account_date = in_accnt_date
           AND s.account_seqno = in_seq_no
           AND s.interface_seq = in_interface_seq
           AND t.planid = b.planid
           AND t.inst_date = in_accnt_date
           AND t.status = '26';*/
       SELECT MAX(to_char(t.pay_tm, 'yyyymmdd'))
         INTO out_date
         FROM tmp_batchpay_submit     t,
              dat_clr_cash_dtl        s,
              dat_trustee_acnt_detail b,
              par_sys_plan            p
        WHERE s.account_id = in_accnt_id
          AND s.account_date = in_accnt_date
          AND s.account_seqno = in_seq_no
          AND s.interface_seq = in_interface_seq
          AND s.interface_seq = b.interface_seq
          AND t.rece_account = b.recipacc
          AND t.status = '26'
          AND s.account_date BETWEEN t.inst_date AND to_char(t.pay_tm, 'yyyymmdd')
          AND t.pay_amount / 100 = s.in_amount
          AND s.account_id = p.acnt_id
          AND p.plan_id = t.planid
          AND s.out_amount = 0;


-- Source: PKG_AAS_DATACLEAR.sql
      SELECT t.kind_id
        INTO v_switch
        FROM swh_all_kind t
       WHERE t.operation_kind = 'AAS_DATACLEAR_SWITCH';


-- Source: PKG_AAS_DATACLEAR.sql
      select count(1)
        into v_today_finish_flag
        from db_log t
       where t.proc_name = 'PROC_AAS_DATACLEAR'
         and t.log_date = to_char(sysdate, 'YYYYMMDD');


-- Source: PKG_AAS_DATACLEAR.sql
   SELECT COUNT(1)
     INTO v_cnt
     FROM user_scheduler_jobs t
     --BIGFUND TO GAUSS OTG-10051�޸�Ϊ�Խ���ͼ
    WHERE t.job_name LIKE 'JOB_PKG_AAS_DATACLEAR%'
    AND state = 'r';


-- Source: PKG_AAS_DATACLEAR.sql
      SELECT t.table_name
        INTO v_table
        FROM dat_dataclear_config t
       WHERE t.clear_type = '1'
         AND t.task_id = p_i_taskid;


-- Source: PKG_AAS_DATACLEAR.sql
      SELECT t.table_name, t.save_date, t.tab_column
        INTO v_table, v_date, v_column
        FROM dat_dataclear_config t
       WHERE t.clear_type = '2'
         AND t.task_id = p_i_taskid;


-- Source: PKG_AAS_DATACLEAR.sql
    select t.table_name, t.save_date, t.tab_column, t.table_his
    into v_table, v_date, v_date_column,v_tab_his
    from dat_dataclear_config t
    where t.clear_type = '3'
    and t.task_id = p_i_taskid;


-- Source: PKG_AAS_DATACLEAR.sql
    select listagg(t.COLUMN_NAME, ', ') within group(order by column_id)
      into v_column
      from MY_TAB_COLUMNS t
     where lower(t.TABLE_NAME) = lower(v_table);


-- Source: PKG_AAS_DATACLEAR.sql
      select t.special_sql
        into v_sql
        from dat_dataclear_config t
       where t.clear_type = '4'
         and t.task_id = p_i_taskid;


-- Source: PKG_AAS_DATACLEAR.sql
      select t.table_name, floor(t.save_date/30)
        into v_table, v_month
        from dat_dataclear_config t
       where t.clear_type = '5'
         and t.task_id = p_i_taskid;


-- Source: PKG_CURSOR.sql
        INSERT INTO audit_log(log_time, operation, sql_text, params)
        VALUES (SYSTIMESTAMP, 'DYNAMIC_FOR_LOOP', v_sql,
                'table=' || p_table_name || ',type=' || p_process_type);


-- Source: PKG_CURSOR.sql
                        -- SELECT *, :1, :2 FROM ' || p_table_name || ' WHERE id = :3';


-- Source: PKG_CURSOR.sql
                    INSERT INTO scan_log(scan_time, table_name, record_id, record_status)
                    VALUES (SYSTIMESTAMP, p_table_name, v_pk_id, v_status);


-- Source: PKG_CURSOR.sql
        INSERT INTO performance_log(log_time, operation, rows_affected, elapsed_ms)
        VALUES (SYSTIMESTAMP, 'dynamic_for_' || p_process_type, v_row_count,
                EXTRACT(EPOCH FROM (SYSTIMESTAMP - v_start_time)) * 1000);


-- Source: PKG_CURSOR.sql
            INSERT INTO error_log(error_time, procedure_name, sql_text, error_msg)
            VALUES (SYSTIMESTAMP, 'proc_dynamic_for_processing', v_sql, SQLERRM);


-- Source: PKG_CURSOR.sql
        INSERT INTO audit_log(log_time, operation, sql_text, bind_params)
        VALUES (SYSTIMESTAMP, 'CURSOR_DYNAMIC_USING', v_sql,
                'status=' || p_status_list || ',min=' || p_min_amount || ',date=' || p_start_date);


-- Source: PKG_CURSOR.sql
            -- SELECT employee_id, employee_name, salary, department_id, hire_date
            -- FROM employees
            -- WHERE department_id = :1
            -- ORDER BY salary DESC
            -- USING p_dept_id;


-- Source: PKG_DEPOSIT_ACNT_INFO_INQUIRY.sql
   select /*+ use_cplan*/ count(1)
   into total_num
   from (
SELECT t.client_acnt_id, t.sys_acnt_id, t.fund_code, t.accno, t.accname,
 t.accnamefund, t.belong_bank_code, t.coin_code, t.zone_code, t.brno,
 t.acnt_type, t.bank_name, t.bank_code, t.bank_cexc, t.bank_bic,
 t.sys_flag, t.cnt_flag, t.dept_code, t.dept_type,
 t.auth_area, t.asset_type, t.accname_eng, '8' AS sub_src_type,
 t.vald_flag, t.inure_begin_date, t.inure_end_date, t.parent_acnt_id, t.sysupdatetm,e.asset_acnt_id
FROM v_par_client_acnt_info_noflag t, v_acnt_check_base_rule e
WHERE e.client_acnt_id = t.client_acnt_id and t.if_inter_bank = '2'
) temp
   left join par_fund_info fi
    on temp.fund_code = fi.fund_code
   left join (select t.area_name,t.area_code
       from par_sys_area t
       where to_char(now(), 'yyyymmdd') between t.inure_begin_date and
            t.inure_end_date) sysarea
   on fi.area_code = sysarea.area_code
   WHERE temp.sub_src_type = '8'
    AND EXISTS (SELECT /*+ no_expand */ 1
                FROM MV_ACCOUNT_PRIV v
               WHERE v.account_code = temp.asset_acnt_id
                 AND v.user_id = p_i_user_id
                 AND v.role = p_i_role_id)
    and (p_i_qry_acnt is null or temp.accno = p_i_qry_acnt)
    and (p_i_qry_bank_pset is null or temp.accno = p_i_qry_bank_pset)
    and (p_i_qry_sys_flag is null or temp.sys_flag= p_i_qry_sys_flag)
    and (p_i_qry_vald_flag is null or temp.vald_flag = p_i_qry_vald_flag)
    and (p_i_qry_asset_type is null or temp.asset_type = p_i_qry_asset_type)
    and (p_i_qry_bank_name is null or temp.bank_name like '%' || p_i_qry_bank_name || '%')
    and (p_i_qry_area_code is null or sysarea.area_code = p_i_qry_area_code);


-- Source: PKG_DEPOSIT_ACNT_INFO_INQUIRY.sql
   select /*+ use_cplan*/ fund_name,
   area_name,
   accno,
   accname,
   balance,
   bank_name,
   asset_type,
   coin_code,
   coin_name,
   security_name,
   sys_flag,
   cnt_flag,
   vald_flag,
   operator_name,
   check_user_name,
   sys_acnt_id
   from (select fi.fund_name,
      (select t.area_name
         from par_sys_area t
        where fi.area_code = t.area_code
        and to_char(now(), 'yyyymmdd') between t.inure_begin_date and
              t.inure_end_date) as area_name,
      temp.ACCNO,
      temp.ACCNAME,
      CASE temp.SYS_FLAG
        when '1' then
         to_char((select t.balance
                   from DAT_CLR_ACNT_BALANCE t
                  where t.asset_acnt_id = temp.asset_acnt_id
                    AND t.data_date = to_char(sysdate - 1, 'yyyymmdd')))
        when '2' then
         '��ϵͳ���˻�'
        else
         ''
      END as balance, -- ���
      temp.bank_name, -- ����������
      (select t.kind_name
         from dic_all_kind t
        where t.operation_kind = 'asset_type'
          and t.kind_id = temp.asset_type) as asset_type, -- �ʲ�����
      temp.coin_code,
      (select t.coin_name
         from par_sys_coin t
        where t.coin_code = temp.coin_code) as coin_name,
      (SELECT b.market_name || '--' || a.main_stock_code || '--' ||
              a.stock_short_name
         FROM par_sys_securities a, par_sys_market b, par_sys_acnt_info t
        WHERE a.main_market_code = b.market_code
          AND a.security_id = t.security_id
          AND t.acnt_id = temp.sys_acnt_id
          AND to_char(now(), 'yyyymmdd') BETWEEN a.inure_begin_date AND
              a.inure_end_date) as security_name, -- ��ӦͶ��Ʒ����
      temp.sys_flag,
      temp.cnt_flag,
      temp.vald_flag,
      (select message_value
         from usermessage um,v_par_client_acnt_info_noflag i
        where i.operator = um.user_id
        and temp.sys_acnt_id = i.sys_acnt_id
        and um.message_id = '001') operator_name,
      (select message_value
         from usermessage um,v_par_client_acnt_info_noflag i
        where i.check_user = um.user_id
        and temp.sys_acnt_id = i.sys_acnt_id
        and um.message_id = '001') check_user_name,
      temp.sys_acnt_id,
      row_number() over(ORDER BY temp.sys_acnt_id) rn
    from (
SELECT t.client_acnt_id, t.sys_acnt_id, t.fund_code, t.accno, t.accname,
     t.accnamefund, t.belong_bank_code, t.coin_code, t.zone_code, t.brno,
     t.acnt_type, t.bank_name, t.bank_code, t.bank_cexc, t.bank_bic,
     t.sys_flag, t.cnt_flag, t.dept_code, t.dept_type,
     t.auth_area, t.asset_type, t.accname_eng, '8' AS sub_src_type,
     t.vald_flag, t.inure_begin_date, t.inure_end_date, t.parent_acnt_id, t.sysupdatetm,e.asset_acnt_id
   FROM v_par_client_acnt_info_noflag t, v_acnt_check_base_rule e
   WHERE e.client_acnt_id = t.client_acnt_id and t.if_inter_bank = '2'
) temp
    left join par_fund_info fi
    on temp.fund_code = fi.fund_code
    left join (select t.area_name,t.area_code
         from par_sys_area t
         where to_char(now(), 'yyyymmdd') between t.inure_begin_date and
              t.inure_end_date) sysarea
    on fi.area_code = sysarea.area_code
    WHERE temp.sub_src_type = '8'
    AND EXISTS (SELECT /*+ no_expand */ 1
                FROM MV_ACCOUNT_PRIV v
               WHERE v.account_code = temp.asset_acnt_id
                 AND v.user_id = p_i_user_id
                 AND v.role = p_i_role_id)
    and (p_i_qry_acnt is null or temp.accno = p_i_qry_acnt)
    and (p_i_qry_bank_pset is null or temp.accno = p_i_qry_bank_pset)
    and (p_i_qry_sys_flag is null or temp.sys_flag= p_i_qry_sys_flag)
    and (p_i_qry_vald_flag is null or temp.vald_flag = p_i_qry_vald_flag)
    and (p_i_qry_asset_type is null or temp.asset_type = p_i_qry_asset_type)
    and (p_i_qry_bank_name is null or temp.bank_name like '%' || p_i_qry_bank_name || '%')
    and (p_i_qry_area_code is null or sysarea.area_code = p_i_qry_area_code))
    where rn BETWEEN to_number(p_i_qrybeginpos) AND to_number(p_i_qrybeginpos) + to_number(p_i_qrynum) - 1
    ORDER BY rn;


-- Source: PKG_DEPOSIT_ACNT_INFO_INQUIRY.sql
   select count(1)
   into total_num
   from (
SELECT t.client_acnt_id, t.sys_acnt_id, t.fund_code, t.accno, t.accname,
     t.accnamefund, t.belong_bank_code, t.coin_code, t.zone_code, t.brno,
     t.acnt_type, t.bank_name, t.bank_code, t.bank_cexc, t.bank_bic,
     t.sys_flag, t.cnt_flag, t.dept_code, t.dept_type,
     t.auth_area, t.asset_type, t.accname_eng, '8' AS sub_src_type,
     t.vald_flag, t.inure_begin_date, t.inure_end_date, t.parent_acnt_id, t.sysupdatetm,e.asset_acnt_id
   FROM v_par_client_acnt_info_noflag t, v_acnt_check_base_rule e
   WHERE e.client_acnt_id = t.client_acnt_id and t.if_inter_bank = '2'
) temp
   left join par_fund_info fi
    on temp.fund_code = fi.fund_code
   left join (select t.area_name,t.area_code
       from par_sys_area t
       where to_char(now(), 'yyyymmdd') between t.inure_begin_date and
            t.inure_end_date) sysarea
   on fi.area_code = sysarea.area_code
   WHERE temp.sub_src_type = '8'
    AND EXISTS (SELECT /*+ no_expand */ 1
                FROM MV_ACCOUNT_PRIV v
               WHERE v.account_code = temp.asset_acnt_id
                 AND v.user_id = p_i_user_id
                 AND v.role = p_i_role_id)
    and (p_i_qry_acnt is null or temp.accno = p_i_qry_acnt)
    and (p_i_qry_bank_pset is null or temp.accno = p_i_qry_bank_pset)
    and (p_i_qry_sys_flag is null or temp.sys_flag= p_i_qry_sys_flag)
    and (p_i_qry_vald_flag is null or temp.vald_flag = p_i_qry_vald_flag)
    and (p_i_qry_asset_type is null or temp.asset_type = p_i_qry_asset_type)
    and (p_i_qry_bank_name is null or temp.bank_name like '%' || p_i_qry_bank_name || '%')
    and (p_i_qry_area_code is null or sysarea.area_code = p_i_qry_area_code);


-- Source: PKG_DEPOSIT_ACNT_INFO_INQUIRY.sql
     select fi.fund_name,
        (select t.area_name
           from par_sys_area t
          where fi.area_code = t.area_code
          and to_char(now(), 'yyyymmdd') between t.inure_begin_date and
                t.inure_end_date) as area_name,
        temp.ACCNO,
        temp.ACCNAME,
        CASE temp.SYS_FLAG
          when '1' then
           to_char((select t.balance
                   from DAT_CLR_ACNT_BALANCE t
                  where t.asset_acnt_id = temp.asset_acnt_id
                    AND t.data_date = to_char(sysdate - 1, 'yyyymmdd')))
          when '2' then
           '��ϵͳ���˻�'
          else
           ''
        END as balance, -- ���
        temp.bank_name, -- ����������
        (select t.kind_name
           from dic_all_kind t
          where t.operation_kind = 'asset_type'
            and t.kind_id = temp.asset_type) as asset_type, -- �ʲ�����
        (select t.coin_name
           from par_sys_coin t
          where t.coin_code = temp.coin_code) as coin_name,
        (SELECT b.market_name || '--' || a.main_stock_code || '--' ||
              a.stock_short_name
         FROM par_sys_securities a, par_sys_market b, par_sys_acnt_info t
        WHERE a.main_market_code = b.market_code
          AND a.security_id = t.security_id
          AND t.acnt_id = temp.sys_acnt_id
          AND to_char(now(), 'yyyymmdd') BETWEEN a.inure_begin_date AND
              a.inure_end_date) as security_name, -- ��ӦͶ��Ʒ����
        decode(temp.sys_flag,'1','ϵͳ��','2','ϵͳ��'),
        decode(temp.cnt_flag,'1','����','2','����'),
        decode(temp.vald_flag,'0','��Ч','1','��Ч'),
        (select message_value
         from usermessage um,v_par_client_acnt_info_noflag i
        where i.operator = um.user_id
        and temp.sys_acnt_id = i.sys_acnt_id
        and um.message_id = '001') operator_name,
      (select message_value
         from usermessage um,v_par_client_acnt_info_noflag i
        where i.check_user = um.user_id
        and temp.sys_acnt_id = i.sys_acnt_id
        and um.message_id = '001') check_user_name,
        temp.sys_acnt_id
      from (
  SELECT t.client_acnt_id, t.sys_acnt_id, t.fund_code, t.accno, t.accname,
     t.accnamefund, t.belong_bank_code, t.coin_code, t.zone_code, t.brno,
     t.acnt_type, t.bank_name, t.bank_code, t.bank_cexc, t.bank_bic,
     t.sys_flag, t.cnt_flag, t.dept_code, t.dept_type,
     t.auth_area, t.asset_type, t.accname_eng, '8' AS sub_src_type,
     t.vald_flag, t.inure_begin_date, t.inure_end_date, t.parent_acnt_id, t.sysupdatetm,e.asset_acnt_id
   FROM v_par_client_acnt_info_noflag t, v_acnt_check_base_rule e
   WHERE e.client_acnt_id = t.client_acnt_id and t.if_inter_bank = '2'
  ) temp
      left join par_fund_info fi
      on temp.fund_code = fi.fund_code
      left join (select t.area_name,t.area_code
           from par_sys_area t
           where to_char(now(), 'yyyymmdd') between t.inure_begin_date and
                t.inure_end_date) sysarea
      on fi.area_code = sysarea.area_code
      WHERE temp.sub_src_type = '8'
      AND EXISTS (SELECT /*+ no_expand */ 1
                FROM MV_ACCOUNT_PRIV v
               WHERE v.account_code = temp.asset_acnt_id
                 AND v.user_id = p_i_user_id
                 AND v.role = p_i_role_id)
      and (p_i_qry_acnt is null or temp.accno = p_i_qry_acnt)
      and (p_i_qry_bank_pset is null or temp.accno = p_i_qry_bank_pset)
      and (p_i_qry_sys_flag is null or temp.sys_flag= p_i_qry_sys_flag)
      and (p_i_qry_vald_flag is null or temp.vald_flag = p_i_qry_vald_flag)
      and (p_i_qry_asset_type is null or temp.asset_type = p_i_qry_asset_type)
      and (p_i_qry_bank_name is null or temp.bank_name like '%' || p_i_qry_bank_name || '%')
      and (p_i_qry_area_code is null or sysarea.area_code = p_i_qry_area_code);


-- Source: PKG_FOR.sql
        -- INSERT INTO batch_log(batch_id, batch_type, start_time, status)
        -- VALUES (seq_batch_log.NEXTVAL, 'BONUS_CALC', SYSDATE, 'RUNNING')
        -- RETURNING batch_id INTO v_log_id;


-- Source: PKG_FOR.sql
                    INSERT INTO bonus_limit_log(log_time, emp_id, limit_reason)
                    VALUES (SYSDATE, v_emp.employee_id,
                            'Bonus capped at annual 20% limit');


-- Source: PKG_FOR.sql
                    -- INSERT INTO employee_bonus (
                    --     bonus_id, emp_id, bonus_amount,
                    --     bonus_month, bonus_year, calc_reason, create_time
                    -- ) VALUES (
                    --     seq_employee_bonus.NEXTVAL,
                    --     v_emp.employee_id,
                    --     v_bonus_amt,
                    --     EXTRACT(MONTH FROM SYSDATE),
                    --     EXTRACT(YEAR FROM SYSDATE),
                    --     'Q' || TO_CHAR(SYSDATE, 'Q') || ' performance bonus',
                    --     SYSDATE
                    -- )
                    -- RETURNING bonus_id INTO v_insert_id;


-- Source: PKG_FOR.sql
                    UPDATE employee_bonus
                    SET bonus_amount = v_bonus_amt,
                        update_time = SYSDATE
                    WHERE emp_id = v_emp.employee_id
                      AND bonus_year = EXTRACT(YEAR FROM SYSDATE)
                      AND bonus_month = EXTRACT(MONTH FROM SYSDATE);


-- Source: PKG_FOR.sql
                    INSERT INTO error_log(error_time, procedure_name,
                                         error_code, error_message, context)
                    VALUES (SYSDATE, 'proc_sync_employee_bonus',
                           SQLCODE, SQLERRM,
                           'EmpID=' || v_emp.employee_id);


-- Source: PKG_FOR.sql
        UPDATE batch_log
        SET end_time = SYSDATE,
            status = 'SUCCESS',
            record_count = v_processed,
            total_amount = v_total_bonus,
            message = 'Processed ' || v_processed || ' employees, total bonus: ' || v_total_bonus
        WHERE batch_id = v_log_id;


-- Source: PKG_FOR.sql
            UPDATE batch_log
            SET status = 'FAILED',
                end_time = SYSDATE,
                message = SQLERRM
            WHERE batch_id = v_log_id;


-- Source: PKG_FOR.sql
        INSERT INTO audit_log(log_time, operation, sql_text, user_name)
        VALUES (SYSTIMESTAMP, 'DYNAMIC_QUERY', v_sql, USER);


-- Source: PKG_FOR.sql
                        INSERT INTO archive_table (id, name, amount, status, archived_time)
                        VALUES (v_rec_id, v_rec_name, v_rec_value, v_rec_status, SYSTIMESTAMP);


-- Source: PKG_FOR.sql
                        INSERT INTO exception_log(exception_time, record_id,
                                                 exception_type, detail)
                        VALUES (SYSTIMESTAMP, v_rec_id, 'UNKNOWN_STATUS', v_rec_status);


-- Source: PKG_FOR.sql
                    INSERT INTO error_log(error_time, context, sqlcode, sqlerrm)
                    VALUES (SYSTIMESTAMP, 'Record ID=' || v_rec_id, SQLCODE, SQLERRM);


-- Source: PKG_FOR.sql
        INSERT INTO performance_log(log_time, procedure_name,
                                   rows_processed, elapsed_ms)
        VALUES (SYSTIMESTAMP, 'proc_process_dynamic_query', v_row_count,
                EXTRACT(EPOCH FROM (SYSTIMESTAMP - v_start_time)) * 1000);


-- Source: PKG_RPT_BATCH_DOWNLOAD.sql
     SELECT t.kind_id
       INTO v_async_export_flag
       FROM dic_all_kind t
      WHERE t.operation_kind = 'ASYNC_EXPORT_FLAG';


-- Source: PKG_RPT_BATCH_DOWNLOAD.sql
   select count(1)
     into v_flag_count
     from swh_all_kind t
    where t.operation_kind = p_i_report_id
      and t.kind_id = '1'
      and t.kind_name = 'BATCH_EXPORT_FLAG'
      and t.remark2 = p_i_proc_name;


-- Source: PKG_RPT_BATCH_DOWNLOAD.sql
   SELECT COUNT(1)
     INTO v_count
     FROM dat_rpt_batch_info t
    WHERE t.report_date = to_char(now(), 'yyyymmdd')
      AND DBE_LOB.COMPARE(t.sql_script, p_i_sql) = 0;


-- Source: PKG_RPT_BATCH_DOWNLOAD.sql
   SELECT seq_rpt_batch.nextval INTO v_seq FROM sys_dummy;


-- Source: PKG_RPT_BATCH_DOWNLOAD.sql
   INSERT INTO dat_rpt_batch_info
     (seq_id,
      report_id,
      report_name,
      user_id,
      status,
      report_date,
      TIMESTAMP,
      content,
      proc_name,
      sql_script,
      col_name,
      batch_type,
      begin_date,
      report_format)
   VALUES
     (v_seq,
      p_i_report_id,
      p_i_file_name || '_' || v_seq || '.csv',
      p_i_user_id,
      '1',
      to_char(now(), 'yyyymmdd'),
      CURRENT_TIMESTAMP,
      empty_blob(),
      p_i_proc_name,
      p_i_sql,
      p_i_col_name,
      '2',
      to_char(now(), 'yyyymmdd'),
      '4');


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    INSERT INTO audit_trail(action_code, detail_info, created_at, session_id)
    VALUES(p_action, p_detail, current_timestamp, pg_backend_pid());


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
        SELECT order_id, total_amount, status
        FROM orders
        WHERE biz_date = v_date AND status IN ('PENDING','PROCESSING')
        ORDER BY priority DESC
        LIMIT g_batch_size;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
        SELECT order_id, total_amount, status
        FROM orders
        WHERE biz_date = v_date
        ORDER BY create_time;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
        UPDATE orders SET status = 'PROCESSING', process_time = current_timestamp
        WHERE order_id = v_rec.order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    INSERT INTO error_log(log_id, order_id, err_msg, created_at)
    VALUES(p_log_id, v_rec.order_id, 'NEGATIVE_AMOUNT', current_timestamp);


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
        INSERT INTO order_item_snapshot(log_id, item_data, created_at)
        VALUES(p_log_id, to_jsonb(v_item_tab(i)), current_timestamp);


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    INSERT INTO distributed_locks(lock_key, holder_id, acquired_at)
    VALUES(v_lock_key, v_conn_id, current_timestamp);


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    SELECT total_amount INTO v_temp_amt FROM orders WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    UPDATE orders SET version = version + 1, update_time = current_timestamp
    WHERE order_id = p_order_id AND version = (
      SELECT version FROM orders WHERE order_id = p_order_id
    );


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    UPDATE accounts SET balance = balance - v_temp_amt
    WHERE account_id = (SELECT account_id FROM orders WHERE order_id = p_order_id);


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    INSERT INTO transaction_log(order_id, amount, tx_type, tx_time)
    VALUES(p_order_id, v_temp_amt, 'DEBIT', current_timestamp);


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    --   SELECT log_id FROM operation_logs
    --   WHERE create_time < v_cutoff
    --   LIMIT v_batch
    -- );


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    --   SELECT region_code,
    --          COUNT(*) AS cnt,
    --          SUM(settle_amount) AS amt,
    --          AVG(fee_rate) AS avg_fee
    --   FROM settlement
    --   WHERE settle_date = v_date
    --   GROUP BY region_code
    --   HAVING COUNT(*) > 10
    --   ORDER BY amt DESC
    -- ) LOOP
    --   v_detail := v_detail || rec.region_code || '|' || rec.cnt || '|'
    --               || rec.amt || '|' || rec.avg_fee || CHR(10);


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      -- SELECT '总计:' || COUNT(*) || '笔,金额:' || COALESCE(SUM(settle_amount),0)
      -- INTO v_detail
      -- FROM settlement WHERE settle_date = v_date;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    --   SELECT order_id, customer_id, order_type
    --   FROM bulk_orders
    --   WHERE batch_id = p_batch_id AND process_flag = 'N'
    -- ) LOOP
    --   <<next_order>>

    --   -- 第一层：客户黑名单检查
    --   FOR black_rec IN (
    --     SELECT 1 FROM black_list WHERE customer_id = main_rec.customer_id AND active = 'Y'
    --   ) LOOP
    --     v_invalid := v_invalid + 1;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
        UPDATE bulk_orders SET process_flag = 'BLACKLIST', process_time = current_timestamp
        WHERE order_id = main_rec.order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      --   SELECT rule_id, threshold FROM risk_rules WHERE rule_type = main_rec.order_type
      -- ) LOOP
      --   DECLARE
      --     v_score INT;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
          -- SELECT risk_score INTO v_score FROM customer_risk
          -- WHERE customer_id = main_rec.customer_id AND rule_id = risk_rec.rule_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
            UPDATE bulk_orders SET process_flag = 'RISK_REJECT', reject_reason = 'RULE_' || risk_rec.rule_id
            WHERE order_id = main_rec.order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      --   SELECT product_id, required_qty FROM order_items WHERE order_id = main_rec.order_id
      -- ) LOOP
      --   DECLARE
      --     v_stock INT;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
          -- SELECT available_qty INTO v_stock FROM inventory WHERE product_id = stock_rec.product_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
            UPDATE bulk_orders SET process_flag = 'NO_STOCK', reject_reason = 'PROD_' || stock_rec.product_id
            WHERE order_id = main_rec.order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE bulk_orders SET process_flag = 'PASSED', process_time = current_timestamp
      WHERE order_id = main_rec.order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE orders SET status = 'PENDING', submit_time = current_timestamp
      WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE orders SET status = 'PAID', pay_time = current_timestamp
      WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE orders SET status = 'CANCELLED', cancel_time = current_timestamp
      WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
        UPDATE orders SET retry_count = v_retry, last_retry = current_timestamp
        WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
        UPDATE orders SET status = 'EXPIRED' WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE orders SET status = 'SHIPPED', ship_time = current_timestamp
      WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE orders SET status = 'REFUNDING', refund_apply_time = current_timestamp
      WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE orders SET status = 'COMPLETED', complete_time = current_timestamp
      WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE orders SET status = 'REFUNDING', refund_apply_time = current_timestamp
      WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE orders SET status = 'REFUNDED', refund_time = current_timestamp
      WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE orders SET status = 'PAID', refund_reject_time = current_timestamp
      WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE orders SET status = 'PAID', partial_refund_amt = 100
      WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      SELECT status INTO v_db_state FROM orders WHERE order_id = p_order_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    INSERT INTO state_transitions(order_id, event, from_state, to_state, trans_time)
    VALUES(p_order_id, p_event, 'AUTO', v_state, current_timestamp);


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      INSERT INTO job_dispatch_log(job_name, task_id, dispatch_time, status)
      VALUES(v_job_name, v_task_id, current_timestamp, 'DISPATCHED');


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      SELECT order_id, total_amount, status, create_time
      FROM orders
      WHERE customer_id = p_customer_id
      ORDER BY create_time DESC
      LIMIT 100;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      SELECT payment_id, pay_amount, pay_channel, pay_time
      FROM payments
      WHERE customer_id = p_customer_id
      AND pay_status = 'SUCCESS'
      ORDER BY pay_time DESC
      LIMIT 100;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    SELECT COUNT(*) INTO v_order_cnt FROM orders WHERE customer_id = p_customer_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    SELECT COUNT(*) INTO v_pay_cnt FROM payments WHERE customer_id = p_customer_id AND pay_status = 'SUCCESS';


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    SELECT balance INTO v_balance FROM accounts WHERE account_id = p_account_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    UPDATE accounts SET balance = balance - p_amount, pre_amount = p_amount
    WHERE account_id = p_account_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    INSERT INTO account_journal(account_id, dr_amount, cr_amount, remark)
    VALUES(p_account_id, p_amount, 0, 'PRE_DEBIT');


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      UPDATE accounts SET frozen_flag = 'Y' WHERE account_id = p_account_id;


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      INSERT INTO risk_events(account_id, event_type, event_time)
      VALUES(p_account_id, 'OVERDRAW', current_timestamp);


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
      INSERT INTO notifications(account_id, notify_type, content)
      VALUES(p_account_id, 'SMS', 'Debit ' || p_amount);


-- Source: PKG_WARPDRIVER_STRESS_TEST.sql
    SELECT balance INTO p_final_bal FROM accounts WHERE account_id = p_account_id;


-- Source: astro_functions_pkg.sql
        --         SELECT SPLIT_PART(TRIM(name), ' ', 1) as prefix, COUNT(*) as cnt
        --         FROM UNNEST(v_array_names) as name
        --         GROUP BY SPLIT_PART(TRIM(name), ' ', 1)
        --     ) t)::JSONB,
        --     TRUE
        -- );


-- Source: astro_functions_pkg.sql
INSERT INTO observations (object_name, ra_hours, dec_degrees, obs_time, raw_data, exposure_seconds, filter_band, magnitude, quality_flag, telescope_id)
SELECT
    CASE (random() * 4)::INT
        WHEN 0 THEN 'NGC ' || (random() * 8000)::INT
        WHEN 1 THEN 'M ' || (random() * 110)::INT
        WHEN 2 THEN 'HD ' || (random() * 200000)::INT
        ELSE 'STAR_' || md5(random()::TEXT)
    END,
    random() * 24,           -- RA 0-24h
    random() * 180 - 90,     -- Dec -90 to +90
    CURRENT_TIMESTAMP - (random() * INTERVAL '90 days'),
    array_to_string(ARRAY(SELECT random()::TEXT FROM generate_series(1, 100)), ','),
    random() * 120,
    CASE (random() * 3)::INT WHEN 0 THEN 'U' WHEN 1 THEN 'B' WHEN 2 THEN 'V' ELSE 'R' END,
    CASE WHEN random() > 0.1 THEN random() * 15 - 5 ELSE 99.999 END,
    (random() * 5)::INT + 1,
    CASE (random() * 2)::INT WHEN 0 THEN 'LST' ELSE 'SST' END
FROM generate_series(1, 5000);


-- Source: complex_clearing_pkg.sql
        INSERT INTO audit_log (log_time, severity, message, session_id)
        VALUES (CURRENT_TIMESTAMP, p_severity, p_msg, pg_backend_pid());


-- Source: complex_clearing_pkg.sql
        SELECT parent_trade_id INTO v_parent_id
        FROM trade_record WHERE trade_id = p_trade_id;


-- Source: complex_clearing_pkg.sql
            SELECT balance INTO v_balance FROM account WHERE account_id = p_account_id;


-- Source: complex_clearing_pkg.sql
            SELECT COALESCE(SUM(amount - fee), 0) INTO v_trade_sum
            FROM trade_record
            WHERE account_id = p_account_id AND status = 'SETTLED';


-- Source: complex_clearing_pkg.sql
            SELECT trade_id, amount, status
            FROM trade_record
            WHERE account_id = p_acct AND trade_id != p_except_id
            FOR UPDATE OF trade_record;  -- 锁定相关行
    BEGIN
        -- 递归深度检查（触发器级联调用自我防护）
        g_trigger_depth := g_trigger_depth + 1;


-- Source: complex_clearing_pkg.sql
            SELECT COALESCE(SUM(amount - fee), 0) INTO v_delta
            FROM trade_record
            WHERE account_id = p_new_rec.account_id
              AND status IN ('SETTLED', 'PENDING');


-- Source: complex_clearing_pkg.sql
                SELECT COUNT(*) INTO v_related_count
                FROM trade_record
                WHERE account_id = p_new_rec.account_id AND status = 'DISPUTED';


-- Source: complex_clearing_pkg.sql
            SELECT t.*, a.account_name,
                   (SELECT COUNT(*) FROM trade_record t2 WHERE t2.account_id = t.account_id AND t2.trade_date > t.trade_date - INTERVAL '30 days') as recent_count
            FROM trade_record t
            JOIN account a ON t.account_id = a.account_id
            WHERE t.amount > p_threshold AND t.status IN ('PENDING', 'DISPUTED')
              AND EXISTS (SELECT 1 FROM audit_log al WHERE al.message LIKE '%' || t.trade_id || '%' AND al.severity = 'WARN' AND al.log_time > CURRENT_TIMESTAMP - INTERVAL '7 days')
            ORDER BY t.amount DESC;


-- Source: complex_clearing_pkg.sql
INSERT INTO trade_record (account_id, amount, status, trade_date, parent_trade_id)
VALUES (1, 50000, 'PENDING', CURRENT_DATE, NULL);


-- Source: complex_clearing_pkg.sql
UPDATE trade_record SET status = 'SETTLED' WHERE trade_id = 1;


-- Source: complex_clearing_pkg.sql
INSERT INTO trade_record (account_id, amount, status, trade_date)
SELECT (random()*99+1)::BIGINT, (random()*100000)::NUMERIC, 'PENDING', CURRENT_DATE
FROM generate_series(1, 100);


-- Source: complex_clearing_pkg.sql
SELECT * FROM audit_log ORDER BY log_id DESC LIMIT 20;


-- Source: pkg_aas_lob_dataclear.sql
      SELECT t.kind_id
        INTO v_switch
        FROM swh_all_kind t
       WHERE t.operation_kind = 'AAS_DATACLEAR_SWITCH';


-- Source: pkg_aas_lob_dataclear.sql
      select count(1)
        into v_today_finish_flag
        from db_log t
       where t.proc_name = 'PROC_AAS_LOB_DATACLEAR'
         and t.log_date = to_char(now(), 'YYYYMMDD');


-- Source: pkg_aas_lob_dataclear.sql
   SELECT COUNT(1)
     INTO v_cnt
     FROM user_scheduler_jobs t
     --BIGFUND TO GAUSS OTG-10051�޸�Ϊ�Խ���ͼ
    WHERE t.job_name ILIKE 'JOB_PKG_AAS_LOB_DATACLEAR%'
    AND state = 'r';


-- Source: pkg_aas_lob_dataclear.sql
      SELECT t.table_name, t.save_date, t.tab_column, t.remark
        INTO v_table, v_date, v_column, v_lob_column
        FROM dat_dataclear_config t
       WHERE t.clear_type = '6'
         AND t.task_id = p_i_taskid;


-- Source: pkg_common.sql
    INSERT INTO t_operation_log(module, action, target_id, created_at)
    VALUES (p_module, p_action, p_target_id, pkg_common.get_sys_date());


-- Source: pkg_common.sql
    INSERT INTO t_notifications(channel, message, sent_at)
    VALUES (p_channel, p_message, pkg_common.get_sys_date());


-- Source: pkg_cursor_patterns.sql
        INSERT INTO t_audit(user_id, action) VALUES(v_rec.id, 'processed');


-- Source: pkg_cursor_patterns.sql
    UPDATE t_stats SET cnt = v_total WHERE stat_key = 'users_processed';


-- Source: pkg_cursor_patterns.sql
        UPDATE t_users SET processed = 1 WHERE id = v_id;


-- Source: pkg_cursor_patterns.sql
    INSERT INTO t_audit(user_id, action) VALUES(v_cnt, 'cursor processed');


-- Source: pkg_cursor_patterns.sql
            INSERT INTO t_alerts(acnt_id, alert_type, message) VALUES(v_id, 'HIGH_BALANCE', v_name);


-- Source: pkg_cursor_patterns.sql
            UPDATE t_accounts SET status = 'FROZEN' WHERE id = v_id;


-- Source: pkg_employee_comments.sql
    SELECT count(1) INTO v_total
    FROM t_employees
    WHERE dept_id = p_dept_id AND status = 'ACTIVE';


-- Source: pkg_employee_comments.sql
    SELECT id, name, email, hire_date, dept_id,
           row_number() OVER (ORDER BY hire_date DESC) AS rn
    FROM t_employees
    WHERE dept_id = p_dept_id AND status = 'ACTIVE'
    ORDER BY hire_date DESC
    LIMIT p_page_size OFFSET v_offset;


-- Source: pkg_employee_comments.sql
    SELECT count(1) INTO v_count
    FROM t_employees WHERE email = p_email;


-- Source: pkg_employee_comments.sql
    INSERT INTO t_employees(name, email, dept_id, hire_date, status)
    VALUES (p_name, p_email, p_dept_id, p_hire_date, 'ACTIVE');


-- Source: pkg_employee_comments.sql
    SELECT max(id) INTO v_emp_id FROM t_employees WHERE email = p_email;


-- Source: pkg_employee_comments.sql
    SELECT dept_id INTO v_old_dept_id
    FROM t_employees WHERE id = p_emp_id;


-- Source: pkg_employee_comments.sql
    UPDATE t_employees SET dept_id = p_new_dept_id WHERE id = p_emp_id;


-- Source: pkg_employee_comments.sql
    UPDATE t_employees SET status = 'INACTIVE' WHERE id = p_emp_id;


-- Source: pkg_employee_comments.sql
    INSERT INTO t_employees(name, email, dept_id, hire_date, status)
    VALUES ('batch_user', 'batch@example.com', p_dept_id, CURRENT_DATE, 'ACTIVE');


-- Source: pkg_inventory.sql
    SELECT stock_qty INTO v_available FROM t_products WHERE id = p_product_id;


-- Source: pkg_inventory.sql
    UPDATE t_products SET stock_qty = stock_qty - p_qty WHERE id = p_product_id;


-- Source: pkg_inventory.sql
    INSERT INTO t_inventory_log(product_id, delta, reason)
    VALUES (p_product_id, -p_qty, 'RESERVE');


-- Source: pkg_inventory.sql
    UPDATE t_products SET stock_qty = stock_qty + p_qty WHERE id = p_product_id;


-- Source: pkg_inventory.sql
    INSERT INTO t_inventory_log(product_id, delta, reason)
    VALUES (p_product_id, p_qty, 'RELEASE');


-- Source: pkg_inventory.sql
    INSERT INTO t_inventory_log(product_id, delta, reason)
    SELECT id, 100, 'SUPPLIER_SYNC'
    FROM t_products WHERE supplier_id = p_supplier_id AND active = true;


-- Source: pkg_mapper_param_test.sql
    SELECT order_status, total_amount
    INTO v_status, v_amount
    FROM t_mapper_order
    WHERE order_id = p_order_id;


-- Source: pkg_mapper_param_test.sql
    INSERT INTO t_mapper_order_item (order_id, line_no, product_name, qty, price)
    VALUES (p_customer_id, 1, 'test', v_qty, p_product_id);


-- Source: pkg_mapper_param_test.sql
    INSERT INTO t_mapper_order (
        customer_id, product_id, quantity, unit_price,
        discount, total_amount, order_status, remark, created_by
    ) VALUES (
        p_customer_id, p_product_id, p_quantity, p_unit_price,
        COALESCE(p_discount, 0), v_total, p_status, p_remark, p_created_by
    );


-- Source: pkg_mapper_param_test.sql
    UPDATE t_mapper_order
    SET order_status = 'PROCESSING',
        updated_at = CURRENT_TIMESTAMP
    WHERE customer_id = p_new_rec.customer_id
      AND product_id = p_new_rec.product_id;


-- Source: pkg_mapper_param_test.sql
    SELECT COALESCE(SUM(total_amount), 0)
    INTO v_diff
    FROM t_mapper_order
    WHERE customer_id = p_new_rec.customer_id
      AND order_status IN ('NEW', 'PROCESSING');


-- Source: pkg_mapper_param_test.sql
    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (
        p_new_rec.order_id,
        p_operator,
        'AUTO_REVIEW',
        'Amount changed from ' || p_old_rec.total_amount || ' to ' || p_new_rec.total_amount
    );


-- Source: pkg_mapper_param_test.sql
    SELECT customer_id, product_id, quantity, unit_price
    INTO v_detail.customer_id, v_detail.product_id,
         v_detail.item_count, v_detail.unit_price
    FROM t_mapper_order
    WHERE order_id = p_order_id;


-- Source: pkg_mapper_param_test.sql
    INSERT INTO t_mapper_order_item (
        order_id, line_no, product_name, qty, price, line_amount
    ) VALUES (
        p_order_id,
        p_line_no,
        'detail_item',
        v_detail.item_count,
        v_detail.unit_price,
        v_detail.item_count * v_detail.unit_price
    );


-- Source: pkg_mapper_param_test.sql
    UPDATE t_mapper_order
    SET total_amount = v_detail.item_count * v_detail.unit_price
    WHERE customer_id = v_detail.customer_id
      AND product_id = v_detail.product_id;


-- Source: pkg_mapper_param_test.sql
    --     SELECT order_id, customer_id, total_amount, order_status
    --     FROM t_mapper_order
    --     WHERE total_amount > p_min_amount
    --       AND order_status = 'NEW'
    --     ORDER BY order_id
    -- LOOP
    --     -- INSERT 用 v_rec 字段 + p_approver
    --     INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    --     VALUES (v_rec.order_id, p_approver, 'BATCH_APPROVE',
    --             'Auto approved, amount=' || v_rec.total_amount);


-- Source: pkg_mapper_param_test.sql
        UPDATE t_mapper_order
        SET order_status = 'APPROVED', updated_at = CURRENT_TIMESTAMP
        WHERE order_id = v_rec.order_id;


-- Source: pkg_mapper_param_test.sql
    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (0, p_approver, 'BATCH_SUMMARY', 'Approved ' || v_count || ' orders');


-- Source: pkg_mapper_param_test.sql
    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (p_order_id, p_operator, 'STATUS_CHANGE', 'Changed to ' || p_new_status);


-- Source: pkg_mapper_param_test.sql
    -- SELECT COUNT(*), COALESCE(SUM(total_amount), 0)
    -- INTO p_order_count, p_total_amount
    -- FROM t_mapper_order
    -- WHERE customer_id = p_customer_id;


-- Source: pkg_mapper_param_test.sql
    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (0, 'SYSTEM', 'SUMMARY', 'Customer ' || p_customer_id || ' has ' || p_order_count || ' orders');


-- Source: pkg_mapper_param_test.sql
    SELECT *
    INTO v_order
    FROM t_mapper_order
    WHERE order_id = p_order_id;


-- Source: pkg_mapper_param_test.sql
    UPDATE t_mapper_order
    SET order_status = 'REVIEWING'
    WHERE customer_id = v_order.customer_id
      AND product_id = v_order.product_id
      AND order_status = v_order.order_status;


-- Source: pkg_mapper_param_test.sql
    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (
        v_order.order_id,
        p_approver,
        p_action,
        'Order amount=' || v_order.total_amount || ' status=' || v_order.order_status
    );


-- Source: pkg_mapper_param_test.sql
    SELECT *
    INTO v_item
    FROM t_mapper_order_item
    WHERE order_id = p_order_id
    LIMIT 1;


-- Source: pkg_mapper_param_test.sql
    UPDATE t_mapper_order_item
    SET line_amount = v_item.qty * v_item.price
    WHERE order_id = v_item.order_id AND line_no = v_item.line_no;


-- Source: pkg_mapper_param_test.sql
    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (
        v_order.order_id,
        p_approver,
        'ITEM_REVIEW',
        'Item total=' || v_item.line_amount || ' for product=' || v_item.product_name
    );


-- Source: pkg_order.sql
    INSERT INTO t_orders(user_id, product_id, qty, status, created_at)
    VALUES (p_user_id, p_product_id, p_qty, 'CREATED', pkg_common.get_sys_date());


-- Source: pkg_order.sql
    SELECT product_id, qty INTO v_product_id, v_qty
    FROM t_orders WHERE id = p_order_id;


-- Source: pkg_order.sql
    UPDATE t_orders SET status = 'CANCELLED' WHERE id = p_order_id;


-- Source: pkg_order.sql
    SELECT o.*, p.name as product_name
    FROM t_orders o
    JOIN t_products p ON o.product_id = p.id
    WHERE o.id = p_order_id;


-- Source: pkg_order.sql
    UPDATE t_orders SET status = 'COMPLETED' WHERE id = p_order_id;


-- Source: pkg_package_vars_test.sql
        UPDATE t_orders SET status = 'PROCESSING' WHERE id = p_order_id;


-- Source: pkg_package_vars_test.sql
        UPDATE t_orders SET status = 'CANCELLED' WHERE id = p_order_id;


-- Source: pkg_package_vars_test.sql
    INSERT INTO t_log(id, msg) VALUES(1, 'status=' || v_current);


-- Source: pkg_package_vars_test.sql
        INSERT INTO t_alerts(order_id, alert_type, message)
        VALUES(0, 'AMOUNT_EXCEED', 'Amount exceeds max: ' || v_max_amount);


-- Source: pkg_package_vars_test.sql
        INSERT INTO t_log(id, msg) VALUES(2, 'Amount OK: ' || p_amount);


-- Source: pkg_package_vars_test.sql
        INSERT INTO t_log(id, msg) VALUES(3, 'Batch exceeds threshold: ' || v_threshold);


-- Source: pkg_package_vars_test.sql
        INSERT INTO t_log(id, msg) VALUES(3, 'App=' || v_app_name || ' processed ' || v_count);


-- Source: pkg_payment.sql
    INSERT INTO t_payments(order_id, amount, method, status, paid_at)
    VALUES (p_order_id, p_amount, p_method, 'PAID', pkg_common.get_sys_date());


-- Source: pkg_payment.sql
    UPDATE t_payments SET status = 'REFUNDED' WHERE order_id = p_order_id;


-- Source: pkg_payment.sql
    SELECT status INTO v_status FROM t_payments WHERE order_id = p_order_id;


-- Source: pkg_payment.sql
    INSERT INTO t_reconciliation(date, total_amount, total_count)
    SELECT p_date, SUM(amount), COUNT(*)
    FROM t_payments
    WHERE DATE(paid_at) = p_date::DATE AND status = 'PAID';


-- Source: pkg_product.sql
    SELECT id, name, price, stock_qty FROM t_products WHERE id = p_product_id;


-- Source: pkg_product.sql
    SELECT * FROM t_products
    WHERE name LIKE '%' || p_keyword || '%'
      AND (p_category IS NULL OR category = p_category);


-- Source: pkg_product.sql
    UPDATE t_products SET price = p_new_price WHERE id = p_product_id;


-- Source: pkg_product.sql
    UPDATE t_products SET price = price * p_multiplier WHERE category = p_category;


-- Source: pkg_product.sql
    UPDATE t_products SET active = false WHERE id = p_product_id;


-- Source: pkg_report.sql
    INSERT INTO t_reports(type, content, generated_at)
    VALUES ('DAILY', p_date, pkg_common.get_sys_date());


-- Source: pkg_report.sql
    INSERT INTO t_reports(type, content, generated_at)
    VALUES ('SALES', p_start_date || '~' || p_end_date, pkg_common.get_sys_date());


-- Source: pkg_test_patterns.sql
        INSERT INTO t_summary(id, amount, batch_no) VALUES(i, v_total, 1);


-- Source: pkg_test_patterns.sql
    UPDATE t_config SET value = v_total WHERE key = 'total';


-- Source: pkg_test_patterns.sql
        UPDATE t_orders SET processed = true WHERE id = v_rec.id;


-- Source: pkg_test_patterns.sql
        INSERT INTO t_audit(order_id, action, operator) VALUES(v_rec.id, 'PROCESSED', 'system');


-- Source: pkg_test_patterns.sql
    INSERT INTO t_log(id, msg) VALUES(1, 'processed ' || v_count || ' orders');


-- Source: pkg_test_patterns.sql
            UPDATE t_products SET price = price - v_discount WHERE id = v_id;


-- Source: pkg_test_patterns.sql
            UPDATE t_products SET price = price - v_discount WHERE id = v_id;


-- Source: pkg_test_patterns.sql
        INSERT INTO t_price_log(product_id, old_price, discount) VALUES(v_id, v_price, v_discount);


-- Source: pkg_test_patterns.sql
    SELECT status, total_amount INTO v_status, v_amount FROM t_orders WHERE id = p_order_id;


-- Source: pkg_test_patterns.sql
            UPDATE t_orders SET status = 'APPROVED' WHERE id = p_order_id;


-- Source: pkg_test_patterns.sql
            UPDATE t_orders SET status = 'REJECTED' WHERE id = p_order_id;


-- Source: pkg_test_patterns.sql
            UPDATE t_orders SET status = 'CANCELLED', remark = v_formatted WHERE id = p_order_id;


-- Source: pkg_test_patterns.sql
    SELECT COUNT(*) INTO v_count FROM t_tasks WHERE status = 'PENDING';


-- Source: pkg_test_patterns.sql
        -- UPDATE t_tasks SET status = 'PROCESSING', batch_no = v_batch
        -- WHERE status = 'PENDING' LIMIT p_threshold;


-- Source: pkg_test_patterns.sql
        SELECT COUNT(*) INTO v_count FROM t_tasks WHERE status = 'PENDING';


-- Source: proc_Five_Gotos.sql
    SELECT nextval('lock_seq') INTO v_lock_id;


-- Source: proc_Five_Gotos.sql
    INSERT INTO resource_locks(lock_id, task_id, created_at)
    VALUES(v_lock_id, p_task_id, current_timestamp);


-- Source: proc_Five_Gotos.sql
    SELECT remaining_quota INTO v_quota FROM quota WHERE task_type = 'A';


-- Source: proc_Five_Gotos.sql
    UPDATE quota SET remaining_quota = remaining_quota - 1 WHERE task_type = 'A';


-- Source: proc_Five_Gotos.sql
    INSERT INTO task_log(task_id, action) VALUES(p_task_id, 'ALLOCATED');


-- Source: proc_Five_Gotos.sql
    --     SELECT dept_id, SUM(amount) AS amt
    --     FROM transactions
    --     WHERE tx_date = p_date
    --     GROUP BY dept_id
    -- ) LOOP
    --     v_detail := v_detail || rec.dept_id || ':' || rec.amt || CHR(10);


-- Source: proc_Five_Gotos.sql
    --     SELECT order_id, customer_id
    --     FROM orders
    --     WHERE create_time = p_batch_date
    -- ) LOOP
    --     <<check_next>>

    --     -- 检查客户信用
    --     FOR credit_rec IN (
    --         SELECT credit_level
    --         FROM customer_credits
    --         WHERE customer_id = order_rec.customer_id
    --     ) LOOP
    --         IF credit_rec.credit_level < 60 THEN
    --             v_invalid := v_invalid + 1;


-- Source: proc_Five_Gotos.sql
            --     SELECT item_status
            --     FROM order_items
            --     WHERE order_id = order_rec.order_id
            -- ) LOOP
            --     IF item_rec.item_status = 'BLOCKED' THEN
            --         v_invalid := v_invalid + 1;


-- Source: proc_Five_Gotos.sql
        UPDATE orders SET process_flag = 'VALIDATED' WHERE order_id = order_rec.order_id;


-- Source: proc_Five_Gotos.sql
        UPDATE orders SET status = 'PENDING' WHERE order_id = p_order_id;


-- Source: proc_Five_Gotos.sql
        UPDATE orders SET status = 'PAID' WHERE order_id = p_order_id;


-- Source: proc_Five_Gotos.sql
        UPDATE orders SET status = 'CANCELLED' WHERE order_id = p_order_id;


-- Source: proc_Five_Gotos.sql
        UPDATE orders SET status = 'SHIPPED' WHERE order_id = p_order_id;


-- Source: proc_Five_Gotos.sql
        UPDATE orders SET status = 'REFUNDING' WHERE order_id = p_order_id;


-- Source: proc_Five_Gotos.sql
        UPDATE orders SET status = 'COMPLETED' WHERE order_id = p_order_id;


-- Source: proc_Five_Gotos.sql
        UPDATE orders SET status = 'REFUNDED' WHERE order_id = p_order_id;


-- Source: proc_Five_Gotos.sql
        UPDATE orders SET status = 'PAID' WHERE order_id = p_order_id;


-- Source: proc_Five_Gotos.sql
    INSERT INTO order_state_log(order_id, from_state, to_state, event)
    VALUES(p_order_id, 'UNKNOWN', v_current, p_event);


-- Source: proc_GOto.sql
            INSERT INTO tgt_table VALUES (rec.*);
END;
