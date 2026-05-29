-- ============================================================
-- 包规范
-- ============================================================
CREATE OR REPLACE PACKAGE tetris_pkg IS
    PROCEDURE init_env();
    FUNCTION run_game() RETURN VARCHAR;
    PROCEDURE cleanup_env();
END tetris_pkg;
/


-- ============================================================
-- 包体
-- ============================================================
CREATE OR REPLACE PACKAGE BODY tetris_pkg IS

    PROCEDURE init_env() IS
    BEGIN
        EXECUTE IMMEDIATE 'CREATE UNLOGGED TABLE IF NOT EXISTS Input (cmd char(1), ts timestamp)';
        EXECUTE IMMEDIATE 'TRUNCATE Input';
        EXECUTE IMMEDIATE 'INSERT INTO Input VALUES ('''', now())';
        EXECUTE IMMEDIATE '
CREATE OR REPLACE FUNCTION notify(str varchar) RETURNS void AS $$
BEGIN
    RAISE NOTICE ''%'', str;
END
$$ LANGUAGE PLPGSQL';
        EXECUTE IMMEDIATE 'CREATE EXTENSION IF NOT EXISTS dblink';
    END;

    FUNCTION run_game() RETURN VARCHAR IS
        result VARCHAR;
    BEGIN
        WITH RECURSIVE main AS (
            WITH const AS (
                SELECT
                    10 AS width,
                    20 AS height,
                    60 AS fps,
                    48/60.0 AS init_drop_delta,
                    6/60.0 AS min_drop_delta,
                    2/60.0 AS drop_delta_decrease,
                    10 AS lines_per_level,
                    1 AS level_score_multiplier
            ),
            points_per_line(lines, points) AS (
                SELECT *
                FROM (
                    VALUES
                        (0, 0),
                        (1, 100),
                        (2, 300),
                        (3, 500),
                        (4, 800)
                ) _
            ),
            tetromino(id, rotation, piece) AS (
                SELECT id, rotation, piece
                FROM const c(w), LATERAL (
                    VALUES
                        (0, 0, ARRAY[4, 5, (c.w+1) + 4, (c.w+1) + 5]),
                        (0, 1, ARRAY[4, 5, (c.w+1) + 4, (c.w+1) + 5]),
                        (0, 2, ARRAY[4, 5, (c.w+1) + 4, (c.w+1) + 5]),
                        (0, 3, ARRAY[4, 5, (c.w+1) + 4, (c.w+1) + 5]),
                        (1, 0, ARRAY[3, 4, 5, 6]),
                        (1, 1, ARRAY[-(c.w+1) + 4, 4, 1*(c.w+1) + 4, 2*(c.w+1) + 4]),
                        (1, 2, ARRAY[3, 4, 5, 6]),
                        (1, 3, ARRAY[-(c.w+1) + 4, 4, 1*(c.w+1) + 4, 2*(c.w+1) + 4]),
                        (2, 0, ARRAY[3, 4, 5, (c.w+1) + 4]),
                        (2, 1, ARRAY[-(c.w+1) + 4, 3, 4, (c.w+1) + 4]),
                        (2, 2, ARRAY[-(c.w+1) + 4, 3, 4, 5]),
                        (2, 3, ARRAY[-(c.w+1) + 4, 4, 5, (c.w+1) + 4]),
                        (3, 0, ARRAY[3, 4, 5, (c.w+1) + 3]),
                        (3, 1, ARRAY[-(c.w+1) + 3, -(c.w+1) + 4, 4, (c.w+1) + 4]),
                        (3, 2, ARRAY[-(c.w+1) + 5, 3, 4, 5]),
                        (3, 3, ARRAY[-(c.w+1) + 4, 4, (c.w+1) + 4, (c.w+1) + 5]),
                        (4, 0, ARRAY[3, 4, 5, (c.w+1) + 5]),
                        (4, 1, ARRAY[-(c.w+1) + 4, 4, (c.w+1) + 3, (c.w+1) + 4]),
                        (4, 2, ARRAY[-(c.w+1) + 3, 3, 4, 5]),
                        (4, 3, ARRAY[-(c.w+1) + 4, -(c.w+1) + 5, 4, (c.w+1) + 4]),
                        (5, 0, ARRAY[4, 5, (c.w+1) + 3, (c.w+1) + 4]),
                        (5, 1, ARRAY[-(c.w+1) + 4, 4, 5, (c.w+1) + 5]),
                        (5, 2, ARRAY[4, 5, (c.w+1) + 3, (c.w+1) + 4]),
                        (5, 3, ARRAY[-(c.w+1) + 4, 4, 5, (c.w+1) + 5]),
                        (6, 0, ARRAY[3, 4, (c.w+1) + 4, (c.w+1) + 5]),
                        (6, 1, ARRAY[-(c.w+1) + 5, 4, 5, (c.w+1) + 4]),
                        (6, 2, ARRAY[3, 4, (c.w+1) + 4, (c.w+1) + 5]),
                        (6, 3, ARRAY[-(c.w+1) + 5, 4, 5, (c.w+1) + 4])
                ) _(id, rotation, piece)
            ),
            conn(name, _) AS (
                SELECT 'conn',
                    CASE
                        WHEN ARRAY['conn'] <@ dblink_get_connections() THEN ''
                        ELSE dblink_connect('conn', 'dbname=' || current_database())
                    END
            )
            SELECT
                0 AS frame,
                string_to_array(repeat(repeat('f', const.width) || 't', const.height), NULL)::bool[] AS board,
                0 AS score,
                0 AS lines,
                const.init_drop_delta AS drop_delta,
                (
                    SELECT ARRAY[id, 0, 0, 0]
                    FROM tetromino
                    ORDER BY random()
                    LIMIT 1
                ) AS pos,
                0 AS max_drop_lines,
                (
                    SELECT id
                    FROM tetromino
                    ORDER BY random()
                    LIMIT 1
                ) AS next_piece,
                clock_timestamp() AS last_drop_time,
                clock_timestamp() AS last_input_time,
                notify('start'),
                pg_sleep(0),
                clock_timestamp() AS last_frame_time
                FROM const
            UNION ALL
            SELECT
                main.frame + 1,
                next_board.board,
                main.score + next_board.earned_points,
                main.lines + next_board.lines_cleared,
                greatest(const.min_drop_delta,
                         const.init_drop_delta
                         - const.drop_delta_decrease * ((main.lines + next_board.lines_cleared) / const.lines_per_level)),
                movement.pos[:3] || ARRAY[0],
                drop_piece.lines,
                next_piece.id,
                movement.drop_time,
                movement.input_time,
                notify(render.string),
                pg_sleep(extract(epoch FROM
                                 main.last_frame_time + make_interval(secs => 1 / const.fps::decimal) - clock_timestamp())),
                clock_timestamp()
            FROM main,
                const,
                conn,
                dblink(conn.name, 'SELECT * FROM Input --' || main.frame) input (cmd char, ts timestamp),
                LATERAL (
                    WITH next_pos(pos, drop_time, input_time) AS (
                        WITH natural_fall(natural_fall) AS (
                            SELECT main.last_drop_time + make_interval(secs => main.drop_delta) <= clock_timestamp()
                                AND input.cmd <> 'p' AS natural_fall
                        )
                        SELECT
                            CASE
                                WHEN natural_fall THEN
                                    main.pos[:2] || ARRAY[main.pos[3] + const.width + 1] || 1
                                WHEN input.ts > main.last_input_time THEN
                                    CASE
                                        WHEN input.cmd = 'u' THEN main.pos[:1] || ARRAY[(main.pos[2] + 1) % 4] || main.pos[3:]
                                        WHEN input.cmd = 'd' THEN main.pos[:2] || ARRAY[main.pos[3] + const.width + 1] || 1
                                        WHEN input.cmd = 'l' THEN main.pos[:2] || ARRAY[main.pos[3] - 1] || main.pos[4]
                                        WHEN input.cmd = 'r' THEN main.pos[:2] || ARRAY[main.pos[3] + 1] || main.pos[4]
                                        WHEN input.cmd = 's' THEN
                                            main.pos[:2] || ARRAY[main.pos[3] + main.max_drop_lines * (const.width + 1)] || 1
                                    END
                                ELSE
                                    main.pos
                            END AS pos,
                            CASE
                                WHEN natural_fall OR (input.ts > main.last_input_time AND input.cmd = 'd') THEN
                                    clock_timestamp()
                                WHEN (input.ts > main.last_input_time AND input.cmd = 's') THEN
                                    main.last_drop_time - make_interval(secs => main.drop_delta)
                                ELSE
                                    main.last_drop_time
                            END AS drop_time,
                            CASE
                                WHEN NOT natural_fall THEN
                                    input.ts
                                ELSE
                                    main.last_input_time
                            END AS input_time
                            FROM natural_fall
                    ),
                    piece_after_movement(new_piece) AS (
                        SELECT array_agg(cell)::integer[] AS new_piece
                        FROM (
                            SELECT unnest(piece) + next_pos.pos[3] AS cell
                            FROM tetromino, next_pos
                            WHERE id = next_pos.pos[1]
                                AND rotation = next_pos.pos[2]
                        ) _
                    ),
                    -- ========== 修改点 1：WITH ORDINALITY → generate_series + 数组下标 ==========
                    collision(collides) AS (
                        SELECT bool_or(main.board[b.ordinality]) AS collides
                        FROM generate_series(1, array_length(main.board, 1)) AS b(ordinality)
                        JOIN unnest((SELECT new_piece FROM piece_after_movement)) p(coord)
                            ON p.coord + 1 = b.ordinality
                    )
                    -- =======================================================================
                    SELECT drop_time, input_time,
                        CASE
                            WHEN
                                (NOT new_piece && ARRAY(SELECT (const.width + 1) * const.height + i
                                                        FROM generate_series(0, const.width + 1) _(i)))
                                    AND (NOT new_piece && ARRAY[-1]) AND NOT (new_piece && ARRAY[-(const.width + 1) - 1])
                                    AND (NOT collision.collides) THEN
                                next_pos.pos
                            WHEN next_pos.pos[4] = 1
                                AND (
                                    new_piece && ARRAY(SELECT (const.width + 1) * const.height + i
                                                       FROM generate_series(0, const.width + 1) _(i))
                                    OR collision.collides
                                ) THEN
                                    ARRAY[main.next_piece, 0, 0, 2]
                            ELSE
                                main.pos
                        END AS pos
                    FROM next_pos, piece_after_movement, collision
                ) movement,
                LATERAL (
                    WITH new_board(board) AS (
                        SELECT
                            CASE
                                WHEN movement.pos[4] = 2 THEN (
                                    WITH RECURSIVE last_piece(piece) AS (
                                        SELECT array_agg(cell)
                                        FROM (
                                            SELECT unnest(piece) + main.pos[3] AS cell
                                            FROM tetromino
                                            WHERE id = main.pos[1]
                                                AND rotation = main.pos[2]
                                        ) _
                                    ),
                                    board_with_piece(i, board) AS (
                                        SELECT 1 AS i, main.board
                                        UNION ALL
                                        SELECT board_with_piece.i + 1,
                                            CASE
                                                WHEN piece[i] >= 0 THEN
                                                    board_with_piece.board[:piece[i]] || '{t}'
                                                    || board_with_piece.board[piece[i] + 2:]
                                                ELSE
                                                    board_with_piece.board
                                            END
                                        FROM board_with_piece, last_piece
                                        WHERE board_with_piece.i <= array_length(piece, 1)
                                    )
                                    SELECT board
                                    FROM board_with_piece
                                    ORDER BY i DESC
                                    LIMIT 1
                                )
                                ELSE
                                    main.board
                            END AS board
                    ),
                    new_board_compressed AS (
                        SELECT array_agg(cell ORDER BY line_number, col_number) AS board,
                            (count(*) / (const.width + 1))::int AS num_lines
                        FROM (
                            SELECT line_number, generate_series(0, const.width) AS col_number, unnest(line) AS cell
                            FROM (
                                SELECT i AS line_number, board[i*(const.width + 1)+1:(i+1)*(const.width+1)] line
                                FROM new_board, generate_series(0, const.height - 1) _(i)
                            ) _
                            WHERE NOT line <@ ARRAY[true]
                        ) _
                    )
                    SELECT string_to_array(repeat(repeat('f', const.width) || 't', const.height - num_lines), NULL)::bool[]
                            || board AS board,
                        const.height - num_lines AS lines_cleared,
                        (
                            SELECT points *
                                (greatest(1, (main.lines / const.lines_per_level + 1) * const.level_score_multiplier))
                            FROM points_per_line
                            WHERE lines = const.height - num_lines
                        ) AS earned_points
                    FROM new_board_compressed
                ) next_board,
                LATERAL (
                    WITH RECURSIVE curr_piece(piece) AS (
                        SELECT piece
                        FROM tetromino
                        WHERE id = movement.pos[1]
                            AND rotation = movement.pos[2]
                    ),
                    -- ========== 修改点 2：WITH ORDINALITY → generate_series + 数组下标 ==========
                    t (lines) AS (
                        SELECT -1
                        UNION ALL
                        SELECT lines + 1
                        FROM t, curr_piece
                        WHERE NOT (
                            SELECT bool_or(next_board.board[b.ordinality]) OR bool_or(next_board.board[b.ordinality] IS NULL)
                            FROM unnest(piece) p(coord)
                            LEFT JOIN generate_series(1, array_length(next_board.board, 1)) AS b(ordinality)
                                ON (p.coord + movement.pos[3]) + 1 + (lines + 1) * (const.width + 1) = b.ordinality
                            WHERE (p.coord + movement.pos[3]) + 1 + (lines + 1) * (const.width + 1) >= 1
                        )
                    )
                    -- =======================================================================
                    SELECT max(lines) AS lines
                    FROM t
                ) drop_piece,
                LATERAL (
                    SELECT
                        CASE
                            WHEN movement.pos[4] = 2 THEN (
                                SELECT id
                                FROM (
                                    SELECT id, 0 AS rank
                                    FROM (
                                        SELECT id
                                        FROM tetromino
                                        ORDER BY random() + main.frame
                                        LIMIT 1
                                    ) _
                                    WHERE id != movement.pos[1]
                                    UNION ALL
                                    (
                                        SELECT id, 1 AS rank
                                        FROM tetromino
                                        ORDER BY random() + main.frame
                                        LIMIT 1
                                    )
                                ) _
                                ORDER BY rank
                                LIMIT 1
                            )
                            ELSE
                                main.next_piece
                        END AS id
                ) next_piece,
                LATERAL (
                    SELECT
                        E'\n\n' ||
                        (CASE WHEN input.cmd = 'p' THEN 'PAUSED' ELSE '' END) ||
                        E'\nScore: ' || (main.score + next_board.earned_points) ||
                        ' / Lines: ' || (main.lines + next_board.lines_cleared) ||
                        ' / Level: ' || ((main.lines + next_board.lines_cleared) / const.lines_per_level + 1) ||
                        E'\nNext: ' || (
                            WITH RECURSIVE next_piece(piece) AS (
                                SELECT array_agg(cell)
                                FROM (
                                    SELECT unnest(piece) - 3 AS cell
                                    FROM tetromino
                                    WHERE tetromino.id = next_piece.id
                                        AND tetromino.rotation = 0
                                ) _
                            ),
                            next_piece_block(i, block) AS (
                                SELECT 1 AS i, string_to_array(repeat(repeat('f', const.width) || E'\n', 2), NULL) AS block
                                UNION ALL
                                SELECT i + 1, block[:piece[i]] || '{t}' || block[piece[i] + 2:]
                                FROM next_piece_block, next_piece
                                WHERE i <= array_length(piece, 1)
                            )
                            SELECT replace(replace(replace(
                                        array_to_string(block[:array_length(block, 1) - 1], ''),
                                        't', '[]'), 'f', '  '), E'\n', E'\n      ')
                            FROM next_piece_block
                            ORDER BY i DESC
                            LIMIT 1
                        ) ||
                        E'\n+' || repeat('-', const.width * 2) || E'+\n' || (
                            WITH RECURSIVE pieces(curr_piece, ghost_piece) AS (
                                SELECT array_agg(curr_cell),
                                    array_agg(curr_cell + greatest(drop_piece.lines, 0) * (const.width + 1))
                                FROM (
                                    SELECT unnest(piece) + movement.pos[3] AS curr_cell
                                    FROM tetromino
                                    WHERE id = movement.pos[1]
                                        AND rotation = movement.pos[2]
                                ) _
                            ),
                            board_with_ghost_piece(i, board) AS (
                                SELECT 1 AS i, next_board.board::char[]
                                UNION ALL
                                SELECT i + 1,
                                    CASE
                                        WHEN ghost_piece[i] >= 0 THEN
                                            board[:ghost_piece[i]] || '{.}' || board[ghost_piece[i] + 2:]
                                        ELSE
                                            board
                                    END::char[] AS board
                                FROM board_with_ghost_piece, pieces
                                WHERE i <= array_length(curr_piece, 1)
                            ),
                            board_with_piece(i, board) AS (
                                SELECT 1, board
                                FROM (
                                    SELECT board
                                    FROM board_with_ghost_piece
                                    ORDER BY i DESC
                                    LIMIT 1
                                ) _
                                UNION ALL
                                SELECT i + 1,
                                    CASE
                                        WHEN curr_piece[i] >= 0 THEN
                                            board[:curr_piece[i]] || '{t}' || board[curr_piece[i] + 2:]
                                        ELSE
                                            board
                                    END::char[]
                                FROM board_with_piece, pieces
                                WHERE i <= array_length(curr_piece, 1)
                            ),
                            -- ========== 修改点 3：WITH ORDINALITY → generate_series + 数组下标 ==========
                            complete_board AS (
                                SELECT (ordinality - 1) / (const.width + 1) AS line_number,
                                    ARRAY['|']::char[] ||
                                      (array_agg(cell ORDER BY ordinality))[:const.width] ||
                                      ARRAY['|', E'\n']::char[] AS line
                                FROM (
                                    SELECT (SELECT board FROM board_with_piece ORDER BY i DESC LIMIT 1)[ordinality] AS cell,
                                           ordinality
                                    FROM generate_series(1, array_length((SELECT board FROM board_with_piece ORDER BY i DESC LIMIT 1), 1)) AS _(ordinality)
                                ) _
                                GROUP BY 1
                            )
                            -- =======================================================================
                            SELECT replace(replace(replace(
                                    array_to_string(array_agg(line ORDER BY line_number), ''),
                                    't', '[]'), '.', '()'), 'f', '  ')
                            FROM complete_board
                        ) || '+' || repeat('-', const.width * 2) || '+' AS string
                ) render
            WHERE main.max_drop_lines >= 0
        )
        SELECT 'score: ' || max(score) INTO result
        FROM main;

        RETURN result;
    END;

    PROCEDURE cleanup_env() IS
    BEGIN
        EXECUTE IMMEDIATE 'DROP TABLE IF EXISTS Input';
        EXECUTE IMMEDIATE 'DROP FUNCTION IF EXISTS notify(varchar)';
    END;

END tetris_pkg;
/
