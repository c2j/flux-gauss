CREATE OR REPLACE PACKAGE pkg_coll_types AS
  TYPE num_table IS TABLE OF NUMBER;
  TYPE rec_item IS RECORD (
    item_id   NUMBER,
    item_name VARCHAR2(64)
  );
  TYPE rec_table IS TABLE OF rec_item;
  PROCEDURE prc_collection_demo(p_mode IN VARCHAR2);
  PROCEDURE prc_collection_index_demo(p_mode IN VARCHAR2);
END pkg_coll_types;
/

CREATE OR REPLACE PACKAGE BODY pkg_coll_types AS
  PROCEDURE prc_collection_demo(p_mode IN VARCHAR2) IS
    v_nums    num_table;
    v_rectab  rec_table;
    v_i       NUMBER := 1;
    v_d       DATE;
    v_cnt     NUMBER;
    v_amt     NUMERIC;
  BEGIN
    v_cnt := v_nums(v_i);
    v_cnt := v_nums.COUNT;
    IF v_nums.EXISTS(1) THEN
      v_nums.DELETE(1);
    END IF;
    v_nums.EXTEND(2);
    v_nums.TRIM(1);
    v_nums(v_i) := v_cnt;
    v_cnt := v_nums.NEXT(v_i);
    v_amt := -v_amt;
    SELECT item_id, item_name BULK COLLECT INTO v_rectab FROM emp;
    v_d := ADD_MONTHS(SYSDATE, 1);
    v_d := LAST_DAY(v_d);
    UPDATE cfg_table SET val = v_cnt WHERE key = p_mode;
  END;

  -- Review #129 §5: non-trivial index expressions must be bound once
  -- (`_collIdxN`), and record-element field writes must emit Map.put.
  PROCEDURE prc_collection_index_demo(p_mode IN VARCHAR2) IS
    v_nums    num_table;
    v_rectab  rec_table;
    v_i       NUMBER := 1;
    v_cnt     NUMBER := 7;
  BEGIN
    v_nums(v_i + 1) := v_cnt;
    v_rectab(v_i).item_id := v_cnt;
    v_rectab(v_i).item_name := 'demo';
    v_cnt := v_nums(v_i + 1);
  END;
END pkg_coll_types;
/
