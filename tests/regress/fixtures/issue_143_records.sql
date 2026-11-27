CREATE OR REPLACE PACKAGE pkg_issue143rec IS
    TYPE rec_param IS RECORD (
        p_id    NUMBER,
        p_name  VARCHAR2(50),
        p_flag  BOOLEAN
    );
    PROCEDURE prc_record_fields(p_rec IN rec_param);
    PROCEDURE prc_collection_count;
END pkg_issue143rec;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue143rec IS
    -- PR #143 review: RECORD field access must keep the real field type —
    -- Boolean/Timestamp fields have no .doubleValue(); only numeric fields
    -- may be unboxed.
    PROCEDURE prc_record_fields(p_rec IN rec_param) IS
        v_flag BOOLEAN;
        v_name VARCHAR2(50);
        v_id   NUMBER;
    BEGIN
        v_flag := p_rec.p_flag;
        v_name := p_rec.p_name;
        v_id   := p_rec.p_id;
    END;

    -- PR #143 review: collection .COUNT lowers to List.size() (primitive
    -- int) — Integer→Long coercion must wrap in ((Number) ...).longValue(),
    -- never emit size().longValue() (int has no longValue()).
    PROCEDURE prc_collection_count IS
        TYPE num_table IS TABLE OF NUMBER;
        v_nums num_table;
        v_cnt  NUMBER;
    BEGIN
        v_cnt := v_nums.COUNT;
    END;
END pkg_issue143rec;
/
