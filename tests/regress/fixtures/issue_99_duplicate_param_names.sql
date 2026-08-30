CREATE OR REPLACE FUNCTION _group_concat(text, text) RETURNS text AS $$
DECLARE
    v_result text := '';
BEGIN
    v_result := $1 || $2;
    RETURN v_result;
END;
$$ LANGUAGE plpgsql;
