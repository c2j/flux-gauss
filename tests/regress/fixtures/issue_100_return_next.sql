-- Issue #100: SETOF functions with RETURN NEXT must accumulate rows
-- into a result list and return it at method end (not a TODO stub).
CREATE OR REPLACE FUNCTION f_issue100_collect_rows() RETURNS SETOF public.customer
    LANGUAGE plpgsql
    AS $fn$
DECLARE
    rr RECORD;
BEGIN
    FOR rr IN SELECT * FROM customer LOOP
        RETURN NEXT rr;
    END LOOP;
    RETURN;
END
$fn$ LANGUAGE plpgsql;
