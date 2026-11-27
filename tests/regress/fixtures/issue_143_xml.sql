CREATE OR REPLACE PACKAGE pkg_issue143xml IS
    FUNCTION f_xmlforest(p_value IN VARCHAR2) RETURN VARCHAR2;
    FUNCTION f_extractvalue(p_xml IN VARCHAR2) RETURN VARCHAR2;
    FUNCTION f_xmlconcat_init() RETURN VARCHAR2;
END pkg_issue143xml;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue143xml IS
    -- PR #143 review: XMLFOREST items are {alias, expr} dicts — must emit
    -- <alias>escaped</alias> elements, not the dict repr. XMLTYPE vars are
    -- String-typed so _xmlConcat's String return matches the declaration.
    FUNCTION f_xmlforest(p_value IN VARCHAR2) RETURN VARCHAR2 IS
        v_xml XMLTYPE;
    BEGIN
        v_xml := XMLFOREST(p_value AS foo, 'a<b&c' AS bar);
        RETURN v_xml::text;
    END;

    -- PR #143 review: EXTRACTVALUE must evaluate the XPath against the XML
    -- argument (arg0), not return the XPath string itself (arg1).
    FUNCTION f_extractvalue(p_xml IN VARCHAR2) RETURN VARCHAR2 IS
    BEGIN
        RETURN EXTRACTVALUE('<foo>abc</foo>', '/foo/text()');
    END;

    -- PR #143 review (Rust): initializer-only _xmlConcat call must still
    -- trigger _xmlConcat/_xmlElement helper emission.
    FUNCTION f_xmlconcat_init() RETURN VARCHAR2 IS
        v_xml XMLTYPE := XMLCONCAT('<a/>', '<b/>');
    BEGIN
        RETURN v_xml;
    END;
END pkg_issue143xml;
/
