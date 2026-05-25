-- ============================================================
-- Business domain tables — ALL column variants merged
-- Auto-consolidated from demo-project/sql/*.sql
-- Each table includes ALL column variants from ALL source files
-- ============================================================

-- Sources: astro_functions_pkg.sql
DROP TABLE IF EXISTS observations CASCADE;
CREATE TABLE observations (
    obs_id BIGSERIAL PRIMARY KEY,
    object_name VARCHAR(100),
    ra_hours NUMERIC(10,6),
    dec_degrees NUMERIC(10,6),
    obs_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    obs_date DATE GENERATED ALWAYS AS (DATE(obs_time)) STORED,
    raw_data TEXT,
    filter_band VARCHAR(10) DEFAULT 'V',
    magnitude NUMERIC(6,3),
    quality_flag INT DEFAULT 1,
    telescope_id VARCHAR(20),
    parent_obs_id BIGINT REFERENCES observations(obs_id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Sources: pkg_builtin_funcs_test.sql
DROP TABLE IF EXISTS t_test_funcs CASCADE;
CREATE TABLE t_test_funcs (
    id          BIGINT,
    name        VARCHAR(100),
    amount      NUMERIC(18, 2),
    status      VARCHAR(20),
    created_at  TIMESTAMP,
    remark      VARCHAR(500)
);
