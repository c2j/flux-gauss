CREATE OR REPLACE PACKAGE pkg_lob_str AS
  PROCEDURE prc_lob_demo(p_src IN VARCHAR2, p_out OUT VARCHAR2);
  PROCEDURE prc_byte_demo(p_src IN VARCHAR2, p_out OUT VARCHAR2);
  PROCEDURE prc_regex_demo(p_src IN VARCHAR2, p_out OUT VARCHAR2);
END pkg_lob_str;
/

CREATE OR REPLACE PACKAGE BODY pkg_lob_str AS
  -- Issue #125 Phase A: LOB scalar family.
  PROCEDURE prc_lob_demo(p_src IN VARCHAR2, p_out OUT VARCHAR2) IS
    v_clob    CLOB;
    v_txt     VARCHAR2(4000);
    v_len     NUMBER;
    v_part    VARCHAR2(4000);
  BEGIN
    v_txt := GETCLOBVAL(v_clob);
    v_txt := TO_CLOB(p_src);
    v_txt := EMPTY_CLOB();
    v_len := DBE_LOB.LOB_GET_LENGTH(v_clob);
    v_part := DBE_LOB.LOB_SUBSTR(v_clob, 10, 1);
    v_part := DBMS_LOB.SUBSTR(v_clob, 5, 2);
    IF v_txt IS NULL THEN
      p_out := 'null';
    ELSE
      p_out := v_txt;
    END IF;
  END;

  -- Issue #125 Phase B: byte-semantic family (UTF-8 byte window).
  PROCEDURE prc_byte_demo(p_src IN VARCHAR2, p_out OUT VARCHAR2) IS
    v_n       NUMBER;
    v_pos     NUMBER;
  BEGIN
    v_n := LENGTHB(p_src);
    v_pos := INSTRB(p_src, '中');
    p_out := SUBSTRB(p_src, 1, 10);
    p_out := SUBSTRB(p_src, -4);
  END;

  -- Issue #125 Phase C: regex family.
  PROCEDURE prc_regex_demo(p_src IN VARCHAR2, p_out OUT VARCHAR2) IS
    v_flag    BOOLEAN;
    v_n       NUMBER;
  BEGIN
    v_flag := REGEXP_LIKE(p_src, '[0-9]+');
    p_out := REGEXP_SUBSTR(p_src, '[a-z]+', 1, 2);
    v_n := REGEXP_INSTR(p_src, '[0-9]+');
    v_n := REGEXP_COUNT(p_src, '[aeiou]');
    p_out := REGEXP_REPLACE(p_src, '([a-z]+)([0-9]*)', '\2-\1', 1, 1, 'i');
  END;
END pkg_lob_str;
/
