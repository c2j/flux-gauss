CREATE OR REPLACE PACKAGE issue_144_stub_typed IS
    PROCEDURE prc_stub_typed;
    PROCEDURE helper_map(p_a IN VARCHAR2, p_m OUT map_object);
    PROCEDURE helper_num(p_n IN NUMBER, p_out OUT NUMBER);
    PROCEDURE helper_str(p_a IN VARCHAR2, p_out OUT VARCHAR2);
END issue_144_stub_typed;
/

CREATE OR REPLACE PACKAGE BODY issue_144_stub_typed IS
    -- Issue #144 batch 4c: TODO-stub type fidelity. Unresolved cross-package
    -- functions render as `/* TODO: implement f() — ... */ null`; when the
    -- assignment target is (or is promoted to) AtomicReference<Map<...>> /
    -- AtomicReference<Long>, the bare null breaks diamond inference
    -- ("无法推断 AtomicReference<> 的类型参数"). The stub must render a
    -- type-faithful empty value instead.
    PROCEDURE helper_map(p_a IN VARCHAR2, p_m OUT map_object) IS
    BEGIN
        p_m := map_object.map_object();
    END;

    PROCEDURE helper_num(p_n IN NUMBER, p_out OUT NUMBER) IS
    BEGIN
        p_out := p_n * 2;
    END;

    PROCEDURE helper_str(p_a IN VARCHAR2, p_out OUT VARCHAR2) IS
    BEGIN
        p_out := p_a;
    END;

    PROCEDURE prc_stub_typed IS
        -- Map-typed local whose DEFAULT is an unresolved function stub and
        -- which is also an OUT arg → promoted to AtomicReference<Map<...>>
        v_map    map_object := map_object.map_object();
        -- NUMBER local with TODO-stub default, promoted to AR<Long>
        v_num    NUMBER := obj_invest_account_recordset(v_map);
        -- VARCHAR2 local with TODO-stub default, promoted to AR<String>
        -- (control: String inner keeps String.valueOf(null) shape)
        v_txt    VARCHAR2(100) := getclobval(v_map);
        -- non-promoted Map local: fidelity stub without promotion
        v_map2   map_object := map_object.map_object();
        v_in     VARCHAR2(100) := 'x';
        v_cnt    NUMBER := 0;
    BEGIN
        helper_map(v_in, v_map);
        helper_num(v_cnt, v_num);
        helper_str(v_in, v_txt);
        NULL;
    END;
END issue_144_stub_typed;
/
