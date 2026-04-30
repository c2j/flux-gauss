-- 基础表
CREATE TABLE IF NOT EXISTS trade_record (
    trade_id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL,
    amount NUMERIC(18,4) NOT NULL,
    fee NUMERIC(18,4) DEFAULT 0,
    status VARCHAR(20) DEFAULT 'PENDING',
    trade_date DATE NOT NULL,
    processed_at TIMESTAMP,
    batch_seq INT,
    CONSTRAINT chk_amount CHECK (amount <> 0)  -- 允许负数，测试边界
);

CREATE TABLE IF NOT EXISTS account (
    account_id BIGSERIAL PRIMARY KEY,
    account_name VARCHAR(100) NOT NULL,
    account_type VARCHAR(20) DEFAULT 'STANDARD',
    balance NUMERIC(18,4) DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id BIGSERIAL PRIMARY KEY,
    log_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    severity VARCHAR(10) DEFAULT 'INFO',
    message TEXT,
    session_id BIGINT
);

-- 插入测试数据
INSERT INTO account (account_name, account_type)
SELECT 'Account_' || i, CASE WHEN i % 10 = 0 THEN 'VIP' ELSE 'STANDARD' END
FROM generate_series(1, 100) AS i;

INSERT INTO trade_record (account_id, amount, fee, status, trade_date)
SELECT
    (random() * 99 + 1)::BIGINT,
    (random() * 2000000 - 500000)::NUMERIC(18,4),  -- 含负数
    (random() * 1000)::NUMERIC(18,4),
    CASE (random() * 4)::INT
        WHEN 0 THEN 'PENDING'
        WHEN 1 THEN 'SETTLED'
        WHEN 2 THEN 'DISPUTED'
        ELSE 'CANCELLED'
    END,
    CURRENT_DATE - (random() * 30)::INT
FROM generate_series(1, 10000);
