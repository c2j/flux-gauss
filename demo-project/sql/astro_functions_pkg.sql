-- ============================================================
-- 内置函数百科全书：天文观测数据处理系统
-- 涵盖 GaussDB/OpenGauss 绝大多数内置函数
-- ============================================================

CREATE OR REPLACE PACKAGE astro_functions_pkg AS

    -- 观测数据类型
    TYPE t_spectrum_array IS VARRAY(4096) OF FLOAT8;  -- 光谱数据
    TYPE t_coord_rec IS RECORD (
        ra NUMERIC(15,12),      -- 赤经（弧度）
        dec NUMERIC(15,12),     -- 赤纬（弧度）
        epoch NUMERIC(10,2)     -- 历元
    );

    -- 结果集类型
    TYPE t_analysis_result IS RECORD (
        object_id VARCHAR(50),
        magnitude NUMERIC(10,6),
        confidence FLOAT8,
        tags TEXT[]
    );

    -- 主入口：综合处理流程
    PROCEDURE process_observation_batch(
        p_obs_date IN DATE,
        p_telescope_id IN VARCHAR(20),
        p_processing_level IN INT DEFAULT 2,
        o_report OUT CLOB,
        o_stats OUT CLOB
    );

    -- 数学/几何专用
    FUNCTION calculate_great_circle(
        p_from IN t_coord_rec,
        p_to IN t_coord_rec
    ) RETURN NUMERIC;

    -- 字符串/编码处理
    FUNCTION encode_catalog_name(
        p_raw_name IN TEXT,
        p_scheme IN INT DEFAULT 1
    ) RETURN VARCHAR(200);

    -- 日期时间计算
    FUNCTION compute_julian_day(
        p_gregorian IN TIMESTAMP WITH TIME ZONE,
        p_format IN INT DEFAULT 1
    ) RETURN NUMERIC;

    -- JSON/数组操作
    FUNCTION analyze_spectrum_features(
        p_spectrum IN t_spectrum_array,
        p_threshold IN FLOAT8 DEFAULT 0.05
    ) RETURN JSONB;

END astro_functions_pkg;
/

CREATE OR REPLACE PACKAGE BODY astro_functions_pkg AS

    -- ============================================================
    -- 核心流程：天文观测批处理（内置函数大杂烩）
    -- ============================================================
    PROCEDURE process_observation_batch(
        p_obs_date IN DATE,
        p_telescope_id IN VARCHAR(20),
        p_processing_level IN INT DEFAULT 2,
        o_report OUT CLOB,
        o_stats OUT CLOB
    ) IS

        -- 游标定义（含各种函数调用）
        CURSOR c_observations IS
            SELECT
                o.obs_id,
                o.object_name,
                o.ra_hours,
                o.dec_degrees,
                o.obs_time,
                o.raw_data,
                o.exposure_seconds,
                o.filter_band,
                -- 数学函数群
                SIN(RADIANS(o.dec_degrees)) as sin_dec,
                COS(RADIANS(o.dec_degrees)) * COS(RADIANS(o.ra_hours * 15)) as x_coord,
                COS(RADIANS(o.dec_degrees)) * SIN(RADIANS(o.ra_hours * 15)) as y_coord,
                SIN(RADIANS(o.ra_hours * 15)) as z_coord,
                -- 字符串函数群
                UPPER(TRIM(BOTH ' ' FROM o.object_name)) as std_name,
                REGEXP_REPLACE(o.object_name, '[^A-Za-z0-9]', '_', 'g') as safe_name,
                SPLIT_PART(o.object_name, ' ', 1) as catalog_prefix,
                -- 日期函数群
                EXTRACT(EPOCH FROM o.obs_time) as unix_time,
                DATE_TRUNC('hour', o.obs_time) as hour_slot,
                AGE(CURRENT_TIMESTAMP, o.obs_time) as time_ago,
                -- 条件函数群
                NULLIF(o.magnitude, 99.999) as valid_mag,
                COALESCE(o.magnitude,
                    CASE WHEN o.filter_band = 'V' THEN 20.0
                         WHEN o.filter_band = 'B' THEN 21.5
                         ELSE 19.0
                    END
                ) as estimated_mag,
                -- 窗口函数（分析函数）
                ROW_NUMBER() OVER (PARTITION BY o.filter_band ORDER BY o.magnitude) as band_rank,
                PERCENT_RANK() OVER (ORDER BY o.magnitude) as mag_percentile,
                LAG(o.magnitude, 1) OVER (ORDER BY o.obs_time) as prev_mag,
                LEAD(o.magnitude, 1, o.magnitude) OVER (ORDER BY o.obs_time) as next_mag,
                FIRST_VALUE(o.object_name) OVER (
                    PARTITION BY o.filter_band
                    ORDER BY o.magnitude
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                ) as brightest_in_band,
                -- 聚合窗口
                AVG(o.magnitude) OVER w_5point as local_avg,
                STDDEV(o.magnitude) OVER w_5point as local_std,
                COUNT(*) OVER (PARTITION BY o.telescope_id) as batch_size
            FROM observations o
            WHERE o.obs_date = p_obs_date
              AND o.telescope_id = p_telescope_id
              AND o.quality_flag > 0
            WINDOW w_5point AS (ORDER BY o.obs_time ROWS BETWEEN 2 PRECEDING AND 2 FOLLOWING);

        -- 局部变量（类型覆盖 Gauss 全谱系）
        v_rec c_observations%ROWTYPE;
        v_spectrum t_spectrum_array := t_spectrum_array();
        v_coord_from t_coord_rec;
        v_coord_to t_coord_rec;
        v_jd NUMERIC;
        v_galactic_lon NUMERIC;
        v_galactic_lat NUMERIC;
        v_distance_parsec NUMERIC;
        v_json_report JSONB := '{}'::JSONB;
        v_xml_fragment XMLTYPE;
        v_hash_md5 VARCHAR(32);
        v_hash_sha256 VARCHAR(64);
        v_uuid UUID;
        v_inet_addr INET;
        v_mac_addr MACADDR;
        v_bit_mask BIT(64);
        v_bytea_raw BYTEA;
        v_geom_point GEOMETRY;
        v_geom_circle GEOMETRY;
        v_path PATH;
        v_circle CIRCLE;
        v_box BOX;
        v_lseg LSEG;
        v_interval_range INT8RANGE;
        v_num_range NUMRANGE;
        v_ts_range TSRANGE;

        -- 统计变量
        v_count_total INT := 0;
        v_count_valid INT := 0;
        v_count_variable INT := 0;
        v_sum_mag NUMERIC := 0;
        v_max_mag NUMERIC := -999;
        v_min_mag NUMERIC := 999;
        v_array_mags NUMERIC[] := '{}';
        v_array_names TEXT[] := '{}';
        v_array_times TIMESTAMP WITH TIME ZONE[] := '{}';

        -- 循环控制
        v_batch_cursor SYS_REFCURSOR;
        v_page_offset INT := 0;
        v_page_size INT := 100;
        v_has_more BOOLEAN := TRUE;

        -- 动态 SQL
        v_dynamic_sql TEXT;
        v_where_clause TEXT := '';
        v_order_clause TEXT := '';
        v_limit_clause TEXT := '';

    BEGIN
        -- ============================================================
        -- 阶段 0：环境初始化（系统/信息函数）
        -- ============================================================

        -- 会话信息
        v_uuid := gen_random_uuid();  -- 本次处理批次ID
        v_inet_addr := inet_client_addr();  -- 客户端IP
        v_mac_addr := '08:00:2b:01:02:03'::MACADDR;  -- 模拟设备MAC

        -- 版本与配置检查
        IF current_setting('server_version_num')::INT < 90600 THEN
            RAISE EXCEPTION 'Requires GaussDB 9.6+';
        END IF;

        -- 设置会话参数（GUC 函数）
        PERFORM set_config('work_mem', '256MB', TRUE);
        PERFORM set_config('enable_seqscan', 'off', FALSE);  -- 仅本次有效

        -- 时间基准
        v_jd := compute_julian_day(CURRENT_TIMESTAMP, 2);  -- 高精度儒略日

        -- ============================================================
        -- 阶段 1：动态构建查询（字符串/条件函数）
        -- ============================================================

        v_where_clause := 'obs_date = ' || quote_literal(p_obs_date) ||
                         ' AND telescope_id = ' || quote_literal(p_telescope_id);

        -- 根据处理级别动态调整
        IF p_processing_level >= 2 THEN
            v_where_clause := v_where_clause ||
                ' AND quality_flag >= ' ||
                GREATEST(p_processing_level - 1, 1)::TEXT;
        END IF;

        -- 使用 DECODE/OIF 风格条件
        v_order_clause := DECODE(p_telescope_id,
            'LST', 'ra_hours ASC',
            'SST', 'dec_degrees ASC',
            'obs_time DESC'
        );

        v_limit_clause := 'LIMIT ' || v_page_size || ' OFFSET ' || v_page_offset;

        v_dynamic_sql := 'SELECT obs_id, object_name, ra_hours, dec_degrees, obs_time, ' ||
                        'raw_data, exposure_seconds, filter_band, magnitude, quality_flag ' ||
                        'FROM observations WHERE ' || v_where_clause ||
                        ' ORDER BY ' || v_order_clause || ' ' || v_limit_clause;

        -- ============================================================
        -- 阶段 2：分页处理（循环 + 游标 + 批量）
        -- ============================================================

        <<outer_pagination>>
        WHILE v_has_more LOOP

            OPEN v_batch_cursor FOR EXECUTE v_dynamic_sql;

            v_count_total := 0;  -- 本页计数

            <<inner_batch>>
            LOOP
                FETCH v_batch_cursor INTO
                    v_rec.obs_id, v_rec.object_name, v_rec.ra_hours,
                    v_rec.dec_degrees, v_rec.obs_time, v_rec.raw_data,
                    v_rec.exposure_seconds, v_rec.filter_band,
                    v_rec.magnitude, v_rec.quality_flag;

                EXIT inner_batch WHEN v_batch_cursor%NOTFOUND;

                v_count_total := v_count_total + 1;

                -- ============================================================
                -- 阶段 2a：坐标转换（数学/三角函数群）
                -- ============================================================

                -- 赤道坐标转银道坐标（使用内置球面三角）
                v_galactic_lon := DEGREES(ATAN2(
                    SIN(RADIANS(v_rec.ra_hours * 15 - 282.25)) * COS(RADIANS(v_rec.dec_degrees)),
                    COS(RADIANS(v_rec.ra_hours * 15 - 282.25)) * SIN(RADIANS(62.87)) *
                        COS(RADIANS(v_rec.dec_degrees)) -
                        SIN(RADIANS(v_rec.dec_degrees)) * COS(RADIANS(62.87))
                )) + 33.0;

                v_galactic_lat := DEGREES(ASIN(
                    SIN(RADIANS(v_rec.dec_degrees)) * SIN(RADIANS(62.87)) +
                    COS(RADIANS(v_rec.dec_degrees)) * COS(RADIANS(62.87)) *
                        COS(RADIANS(v_rec.ra_hours * 15 - 282.25))
                ));

                -- 归一化角度
                v_galactic_lon := MOD(v_galactic_lon + 360.0, 360.0);

                -- ============================================================
                -- 阶段 2b：距离估算（数值/对数函数）
                -- ============================================================

                IF v_rec.magnitude IS NOT NULL AND v_rec.magnitude < 90 THEN
                    -- 距离模数公式：m - M = 5log10(d) - 5
                    -- 假设绝对星等 M = -1（类似天狼星）
                    v_distance_parsec := POWER(10.0, (v_rec.magnitude - (-1.0) - 5.0) / 5.0);

                    -- 使用 LOG/LN 验证
                    IF ABS(LN(v_distance_parsec) / LN(10.0) - (v_rec.magnitude + 1.0 - 5.0) / 5.0) > 1e-10 THEN
                        RAISE WARNING 'Logarithm consistency check failed';
                    END IF;
                END IF;

                -- ============================================================
                -- 阶段 2c：数据质量判断（条件/逻辑函数群）
                -- ============================================================

                -- 综合质量评分
                v_rec.quality_flag := CASE
                    WHEN v_rec.exposure_seconds < 1 THEN 1
                    WHEN v_rec.exposure_seconds BETWEEN 1 AND 10 THEN 2
                    WHEN v_rec.exposure_seconds BETWEEN 10 AND 60 THEN 3
                    WHEN v_rec.exposure_seconds > 60 THEN 4
                    ELSE 0
                END;

                -- 使用 NULLIF 处理无效数据
                v_rec.magnitude := NULLIF(v_rec.magnitude, 99.999);

                -- 使用 COALESCE 提供默认值
                v_rec.magnitude := COALESCE(v_rec.magnitude,
                    20.0 + RANDOM() * 2.0  -- 随机填充未知星等
                );

                -- 使用 GREATEST/LEAST 约束范围
                v_rec.magnitude := GREATEST(LEAST(v_rec.magnitude, 30.0), -5.0);

                -- 使用 SIGN/ABS 判断变化趋势
                IF SIGN(v_rec.magnitude - COALESCE(v_max_mag, v_rec.magnitude)) > 0 THEN
                    v_max_mag := v_rec.magnitude;  -- 更暗（数值更大）
                END IF;

                -- ============================================================
                -- 阶段 2d：字符串处理与编码（字符串函数群）
                -- ============================================================

                -- 标准化名称
                v_rec.object_name := REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        TRIM(BOTH ' ' FROM UPPER(v_rec.object_name)),
                        '\s+', ' ', 'g'
                    ),
                    '[^A-Z0-9\s\-]', '', 'g'
                );

                -- 提取星表前缀
                v_rec.catalog_prefix := SPLIT_PART(v_rec.object_name, ' ', 1);

                -- 填充对齐
                v_rec.object_name := LPAD(v_rec.object_name, 30, ' ');
                v_rec.object_name := RPAD(v_rec.object_name, 30, '.');

                -- 子串提取
                IF LENGTH(v_rec.object_name) > 20 THEN
                    v_rec.object_name := SUBSTRING(v_rec.object_name FROM 1 FOR 20) || '...';
                END IF;

                -- 位置查找
                IF STRPOS(v_rec.object_name, 'NGC') > 0 THEN
                    -- NGC 天体特殊处理
                    v_rec.quality_flag := v_rec.quality_flag + 1;
                END IF;

                -- 使用 POSITION/REPLACE
                v_rec.object_name := REPLACE(v_rec.object_name, '  ', ' ');
                v_rec.object_name := OVERLAY(v_rec.object_name PLACING '##' FROM 1 FOR 2);

                -- 编码转换
                v_bytea_raw := convert_to(v_rec.object_name, 'UTF8');
                v_hash_md5 := MD5(v_bytea_raw);
                v_hash_sha256 := encode(digest(v_bytea_raw, 'sha256'), 'hex');

                -- ============================================================
                -- 阶段 2e：日期时间处理（日期函数群）
                -- ============================================================

                -- 儒略日计算验证
                v_jd := compute_julian_day(v_rec.obs_time, 1);

                -- 时间差计算
                IF AGE(CURRENT_TIMESTAMP, v_rec.obs_time) > INTERVAL '30 days' THEN
                    -- 老数据降权
                    v_rec.quality_flag := v_rec.quality_flag - 1;
                END IF;

                -- 时间截断与舍入
                v_rec.obs_time := DATE_TRUNC('minute', v_rec.obs_time);

                -- 使用 EXTRACT 获取分量
                IF EXTRACT(YEAR FROM v_rec.obs_time) < 2000 THEN
                    -- 历史数据标记
                    v_rec.object_name := '[HIST]' || v_rec.object_name;
                END IF;

                -- 使用 TO_CHAR 格式化
                v_rec.obs_time := TO_TIMESTAMP(
                    TO_CHAR(v_rec.obs_time, 'YYYY-MM-DD HH24:MI:SS'),
                    'YYYY-MM-DD HH24:MI:SS'
                );

                -- ============================================================
                -- 阶段 2f：数组与集合操作（数组函数群）
                -- ============================================================

                -- 收集统计数组
                v_array_mags := array_append(v_array_mags, v_rec.magnitude);
                v_array_names := array_append(v_array_names, TRIM(v_rec.object_name));
                v_array_times := array_append(v_array_times, v_rec.obs_time);

                -- 光谱数据解析（假设 raw_data 包含光谱）
                IF v_rec.raw_data IS NOT NULL THEN
                    -- 使用 STRING_TO_ARRAY 解析
                    v_spectrum := t_spectrum_array(
                        STRING_TO_ARRAY(v_rec.raw_data, ',')::FLOAT8[]
                    );

                    -- 数组统计
                    IF v_spectrum.COUNT > 0 THEN
                        v_rec.quality_flag := v_rec.quality_flag +
                            CASE
                                WHEN array_length(ARRAY(SELECT UNNEST(v_spectrum) s WHERE s > 0.5), 1) > 100 THEN 2
                                WHEN array_length(ARRAY(SELECT UNNEST(v_spectrum) s WHERE s > 0.2), 1) > 50 THEN 1
                                ELSE 0
                            END;
                    END IF;
                END IF;

                -- ============================================================
                -- 阶段 2g：JSON/XML 构建（半结构化函数群）
                -- ============================================================

                -- 构建 JSON 对象
                v_json_report := jsonb_set(
                    v_json_report,
                    ARRAY[v_rec.obs_id::TEXT],
                    jsonb_build_object(
                        'name', TRIM(v_rec.object_name),
                        'ra', ROUND(v_rec.ra_hours::NUMERIC, 6),
                        'dec', ROUND(v_rec.dec_degrees::NUMERIC, 6),
                        'gal_lon', ROUND(v_galactic_lon, 4),
                        'gal_lat', ROUND(v_galactic_lat, 4),
                        'mag', v_rec.magnitude,
                        'dist_pc', ROUND(v_distance_parsec::NUMERIC, 2),
                        'quality', v_rec.quality_flag,
                        'hash', v_hash_md5,
                        'spectrum_len', v_spectrum.COUNT,
                        'tags', jsonb_build_array(
                            CASE WHEN v_rec.filter_band = 'V' THEN 'visual' END,
                            CASE WHEN v_distance_parsec < 10 THEN 'nearby' END,
                            CASE WHEN v_rec.quality_flag >= 4 THEN 'high_quality' END
                        )
                    ),
                    TRUE
                );

                -- 构建 XML 片段
                v_xml_fragment := XMLTYPE('
                    <observation id="' || v_rec.obs_id || '">
                        <coordinates>
                            <equatorial ra="' || v_rec.ra_hours || '" dec="' || v_rec.dec_degrees || '"/>
                            <galactic lon="' || v_galactic_lon || '" lat="' || v_galactic_lat || '"/>
                        </coordinates>
                        <photometry magnitude="' || v_rec.magnitude || '" band="' || v_rec.filter_band || '"/>
                    </observation>
                ');

                -- ============================================================
                -- 阶段 2h：几何与空间（PostGIS 风格函数群）
                -- ============================================================

                -- 构建几何点
                v_geom_point := ST_SetSRID(ST_MakePoint(v_rec.ra_hours * 15, v_rec.dec_degrees), 4326);

                -- 构建搜索圆（误差圈）
                v_geom_circle := ST_Buffer(v_geom_point, 0.001);  -- 约 3.6 角秒

                -- 几何属性提取
                v_circle := v_geom_circle::CIRCLE;
                v_box := ST_Envelope(v_geom_circle)::BOX;

                -- 距离计算（使用球面几何）
                IF v_coord_from.ra IS NOT NULL THEN
                    v_coord_to.ra := v_rec.ra_hours * 15 * PI() / 180.0;
                    v_coord_to.dec := v_rec.dec_degrees * PI() / 180.0;
                    v_coord_to.epoch := 2000.0;

                    -- 大圆距离
                    v_distance_parsec := calculate_great_circle(v_coord_from, v_coord_to);
                END IF;

                -- 保存当前为下一次的前一个
                v_coord_from.ra := v_rec.ra_hours * 15 * PI() / 180.0;
                v_coord_from.dec := v_rec.dec_degrees * PI() / 180.0;

                -- ============================================================
                -- 阶段 2i：范围与位运算（高级类型函数群）
                -- ============================================================

                -- 构建数值范围
                v_num_range := NUMRANGE(
                    v_rec.magnitude - 0.5,
                    v_rec.magnitude + 0.5,
                    '[]'
                );

                -- 构建时间范围
                v_ts_range := TSRANGE(
                    v_rec.obs_time - INTERVAL '1 hour',
                    v_rec.obs_time + INTERVAL '1 hour'
                );

                -- 位掩码操作（质量标志编码）
                v_bit_mask := (v_rec.quality_flag::INT8)::BIT(64);
                v_bit_mask := v_bit_mask | B'1000';  -- 设置第4位（处理标记）

                -- 范围包含检查
                IF v_rec.magnitude <@ v_num_range THEN
                    -- 自包含恒真，仅演示语法
                    NULL;
                END IF;

                -- ============================================================
                -- 阶段 2j：聚合统计更新（流程控制交织）
                -- ============================================================

                v_sum_mag := v_sum_mag + v_rec.magnitude;
                v_count_valid := v_count_valid + 1;

                IF v_rec.magnitude > v_max_mag THEN
                    v_max_mag := v_rec.magnitude;
                END IF;
                IF v_rec.magnitude < v_min_mag THEN
                    v_min_mag := v_rec.magnitude;
                END IF;

                -- 变星检测（与上一比较）
                IF v_count_valid > 1 THEN
                    IF ABS(v_rec.magnitude - v_array_mags[array_length(v_array_mags, 1) - 1]) > 0.5 THEN
                        v_count_variable := v_count_variable + 1;
                    END IF;
                END IF;

            END LOOP inner_batch;

            CLOSE v_batch_cursor;

            -- 检查是否还有更多
            v_page_offset := v_page_offset + v_page_size;
            v_limit_clause := 'LIMIT ' || v_page_size || ' OFFSET ' || v_page_offset;
            v_dynamic_sql := REPLACE(v_dynamic_sql,
                'LIMIT ' || (v_page_offset - v_page_size) || ' OFFSET ' || (v_page_offset - v_page_size),
                v_limit_clause
            );

            -- 使用 FOUND 判断
            IF v_count_total < v_page_size THEN
                v_has_more := FALSE;
            END IF;

        END LOOP outer_pagination;

        -- ============================================================
        -- 阶段 3：最终统计计算（聚合函数 + 窗口函数）
        -- ============================================================

        -- 使用数组聚合函数
        v_json_report := jsonb_set(v_json_report, ARRAY['stats'], jsonb_build_object(
            'count_total', v_count_valid,
            'count_variable', v_count_variable,
            'mean_mag', ROUND((v_sum_mag / NULLIF(v_count_valid, 0))::NUMERIC, 4),
            'min_mag', v_min_mag,
            'max_mag', v_max_mag,
            'mag_range', v_max_mag - v_min_mag,
            'array_stats', jsonb_build_object(
                'median', (SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY UNNEST) FROM UNNEST(v_array_mags)),
                'stddev', (SELECT STDDEV(UNNEST) FROM UNNEST(v_array_mags)),
                'mode', MODE() WITHIN GROUP (ORDER BY UNNEST) FROM UNNEST(v_array_mags)
            ),
            'name_list', array_to_string(v_array_names, '; '),
            'time_span',
                EXTRACT(EPOCH FROM (MAX(UNNEST) - MIN(UNNEST))) / 3600.0
                FROM UNNEST(v_array_times)
        ));

        -- 使用字符串聚合
        v_json_report := jsonb_set(v_json_report, ARRAY['catalog_summary'],
            (SELECT jsonb_object_agg(prefix, cnt) FROM (
                SELECT SPLIT_PART(TRIM(name), ' ', 1) as prefix, COUNT(*) as cnt
                FROM UNNEST(v_array_names) as name
                GROUP BY SPLIT_PART(TRIM(name), ' ', 1)
            ) t)::JSONB,
            TRUE
        );

        -- ============================================================
        -- 阶段 4：输出格式化（类型转换 + 编码函数）
        -- ============================================================

        o_report := v_json_report::CLOB;

        -- 构建统计摘要 XML
        o_stats := XMLTYPE('
            <batchReport telescope="' || p_telescope_id || '" date="' || p_obs_date || '">
                <processing>
                    <level>' || p_processing_level || '</level>
                    <timestamp>' || CURRENT_TIMESTAMP || '</timestamp>
                    <duration>' || clock_timestamp() - statement_timestamp() || '</duration>
                </processing>
                <summary>
                    <objects count="' || v_count_valid || '"/>
                    <variableCandidates count="' || v_count_variable || '"/>
                    <magnitude min="' || v_min_mag || '" max="' || v_max_mag || '"/>
                </summary>
            </batchReport>
        ')::CLOB;

        -- 使用 pg_sleep 模拟耗时操作（仅演示）
        IF v_count_valid > 1000 THEN
            PERFORM pg_sleep(0.1);
        END IF;

        -- 重置会话参数
        PERFORM set_config('work_mem', '64MB', FALSE);

    EXCEPTION
        WHEN OTHERS THEN
            -- 错误信息格式化
            o_report := jsonb_build_object(
                'error', TRUE,
                'sqlstate', SQLSTATE,
                'message', SQLERRM,
                'detail', pg_exception_detail(),
                'hint', pg_exception_hint(),
                'context', pg_exception_context()
            )::CLOB;
            RAISE;
    END;

    -- ============================================================
    -- 辅助函数实现
    -- ============================================================

    FUNCTION calculate_great_circle(
        p_from IN t_coord_rec,
        p_to IN t_coord_rec
    ) RETURN NUMERIC IS
        v_dlon NUMERIC;
        v_dlat NUMERIC;
        v_a NUMERIC;
        v_c NUMERIC;
        v_r NUMERIC := 6371000.0;  -- 地球半径（米），用于角度距离类比
    BEGIN
        v_dlon := p_to.ra - p_from.ra;
        v_dlat := p_to.dec - p_from.dec;

        v_a := SIN(v_dlat/2.0)^2 + COS(p_from.dec) * COS(p_to.dec) * SIN(v_dlon/2.0)^2;
        v_c := 2.0 * ATAN2(SQRT(v_a), SQRT(1.0 - v_a));

        RETURN v_r * v_c;  -- 弧长（米）
    END;

    FUNCTION encode_catalog_name(
        p_raw_name IN TEXT,
        p_scheme IN INT DEFAULT 1
    ) RETURN VARCHAR(200) IS
        v_encoded TEXT;
        v_crc INT;
    BEGIN
        v_encoded := TRIM(BOTH ' ' FROM UPPER(p_raw_name));

        CASE p_scheme
            WHEN 1 THEN
                -- 简单替换
                v_encoded := TRANSLATE(v_encoded, ' -', '__');
            WHEN 2 THEN
                -- CRC32 校验后缀
                v_crc := CRC32(v_encoded::BYTEA);
                v_encoded := v_encoded || '_' || TO_HEX(v_crc);
            WHEN 3 THEN
                -- Base64 编码
                v_encoded := encode(v_encoded::BYTEA, 'base64');
            ELSE
                -- MD5 哈希
                v_encoded := MD5(v_encoded::BYTEA);
        END CASE;

        RETURN LEFT(v_encoded, 200);
    END;

    FUNCTION compute_julian_day(
        p_gregorian IN TIMESTAMP WITH TIME ZONE,
        p_format IN INT DEFAULT 1
    ) RETURN NUMERIC IS
        v_year INT;
        v_month INT;
        v_day INT;
        v_hour NUMERIC;
        v_a INT;
        v_b INT;
        v_jd NUMERIC;
    BEGIN
        v_year := EXTRACT(YEAR FROM p_gregorian);
        v_month := EXTRACT(MONTH FROM p_gregorian);
        v_day := EXTRACT(DAY FROM p_gregorian);
        v_hour := EXTRACT(HOUR FROM p_gregorian) +
                  EXTRACT(MINUTE FROM p_gregorian)/60.0 +
                  EXTRACT(SECOND FROM p_gregorian)/3600.0;

        IF v_month <= 2 THEN
            v_year := v_year - 1;
            v_month := v_month + 12;
        END IF;

        v_a := TRUNC(v_year / 100.0);
        v_b := 2 - v_a + TRUNC(v_a / 4.0);

        -- 简化儒略日公式
        v_jd := TRUNC(365.25 * (v_year + 4716)) +
                TRUNC(30.6001 * (v_month + 1)) +
                v_day + v_hour/24.0 + v_b - 1524.5;

        IF p_format = 2 THEN
            -- 高精度：加入时区偏移微秒修正
            v_jd := v_jd + EXTRACT(MICROSECOND FROM p_gregorian) / 86400000000.0;
        END IF;

        RETURN v_jd;
    END;

    FUNCTION analyze_spectrum_features(
        p_spectrum IN t_spectrum_array,
        p_threshold IN FLOAT8 DEFAULT 0.05
    ) RETURN JSONB IS
        v_result JSONB := '{}'::JSONB;
        v_peaks FLOAT8[] := '{}';
        v_valleys FLOAT8[] := '{}';
        v_mean FLOAT8;
        v_std FLOAT8;
        v_n INT := p_spectrum.COUNT;
        v_i INT;
    BEGIN
        IF v_n = 0 THEN
            RETURN '{"error":"empty_spectrum"}'::JSONB;
        END IF;

        -- 基础统计
        v_mean := (SELECT AVG(UNNEST) FROM UNNEST(p_spectrum));
        v_std := (SELECT STDDEV(UNNEST) FROM UNNEST(p_spectrum));

        -- 峰谷检测
        FOR v_i IN 2..(v_n - 1) LOOP
            IF p_spectrum(v_i) > p_spectrum(v_i-1) AND
               p_spectrum(v_i) > p_spectrum(v_i+1) AND
               p_spectrum(v_i) > v_mean + 2 * v_std THEN
                v_peaks := array_append(v_peaks, v_i::FLOAT8);
            END IF;
            IF p_spectrum(v_i) < p_spectrum(v_i-1) AND
               p_spectrum(v_i) < p_spectrum(v_i+1) AND
               p_spectrum(v_i) < v_mean - 2 * v_std THEN
                v_valleys := array_append(v_valleys, v_i::FLOAT8);
            END IF;
        END LOOP;

        -- 构建结果
        v_result := jsonb_build_object(
            'mean', ROUND(v_mean::NUMERIC, 6),
            'stddev', ROUND(v_std::NUMERIC, 6),
            'snr', ROUND((v_mean/v_std)::NUMERIC, 2),
            'peaks', v_peaks,
            'valleys', v_valleys,
            'peak_count', array_length(v_peaks, 1),
            'valley_count', array_length(v_valleys, 1),
            'entropy', -SUM(
                p_spectrum(i) * LN(NULLIF(p_spectrum(i), 0))
                FROM generate_series(1, v_n) AS i
            ) / LN(v_n::FLOAT8)
        );

        RETURN v_result;
    END;

END astro_functions_pkg;
/

-- ============================================================
-- 配套测试表与数据
-- ============================================================

CREATE TABLE IF NOT EXISTS observations (
    obs_id BIGSERIAL PRIMARY KEY,
    object_name VARCHAR(100),
    ra_hours NUMERIC(10,6),
    dec_degrees NUMERIC(10,6),
    obs_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    obs_date DATE GENERATED ALWAYS AS (DATE(obs_time)) STORED,
    raw_data TEXT,  -- 逗号分隔的光谱数据
    exposure_seconds NUMERIC(10,3),
    filter_band VARCHAR(10) DEFAULT 'V',
    magnitude NUMERIC(6,3),
    quality_flag INT DEFAULT 1,
    telescope_id VARCHAR(20),
    parent_obs_id BIGINT REFERENCES observations(obs_id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 生成测试数据
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

-- 测试调用
DO $$
DECLARE
    v_report CLOB;
    v_stats CLOB;
BEGIN
    astro_functions_pkg.process_observation_batch(
        CURRENT_DATE - 30,
        'LST',
        3,
        v_report,
        v_stats
    );
    RAISE NOTICE 'Report: %', LEFT(v_report, 500);
    RAISE NOTICE 'Stats: %', LEFT(v_stats, 500);
END;
$$;
