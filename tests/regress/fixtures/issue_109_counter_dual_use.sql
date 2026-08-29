CREATE OR REPLACE FUNCTION fn_split_counter(p_i_str VARCHAR2)
     RETURN VARCHAR2 IS
      v_pos    INTEGER := NULL;
      v_oldpos INTEGER := NULL;
      i        INTEGER := NULL;
      t_res    VARCHAR2(300) := NULL;
    BEGIN
      v_oldpos := 1;
      i        := 1;
      v_pos    := instr(p_i_str, ',');
      WHILE v_pos > 0 LOOP
        t_res := substr(p_i_str, v_oldpos, v_pos - v_oldpos);
        i := i + 1;
        v_oldpos := v_pos + 1;
        v_pos := instr(p_i_str, ',', 1, i);
      END LOOP;
      FOR i IN 1 .. 5 LOOP
        NULL;
      END LOOP;
      RETURN t_res;
    END;
/
