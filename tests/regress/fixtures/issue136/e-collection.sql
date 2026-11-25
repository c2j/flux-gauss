CREATE OR REPLACE PACKAGE PKG_E_COLLECTION AS
  TYPE t_num_table IS TABLE OF NUMBER;
  TYPE t_item IS RECORD (
    item_count NUMBER
  );
  PROCEDURE prc_collection;
END PKG_E_COLLECTION;
/

CREATE OR REPLACE PACKAGE BODY PKG_E_COLLECTION AS
  PROCEDURE prc_collection IS
    v_nums t_num_table;
    v_item t_item;
    v_i NUMBER;
  BEGIN
    v_nums := t_num_table();
    v_item.item_count := 2;
    FOR v_i IN 1 .. v_item.item_count LOOP
      v_nums.EXTEND;
      v_nums(v_i) := v_i;
    END LOOP;
  END prc_collection;
END PKG_E_COLLECTION;
/
