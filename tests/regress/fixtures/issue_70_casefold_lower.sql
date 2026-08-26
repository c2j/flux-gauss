CREATE OR REPLACE PACKAGE other.pkg_casefold IS
    PROCEDURE del_entry(p_i_id IN VARCHAR2);
END pkg_casefold;
/

CREATE OR REPLACE PACKAGE BODY other.pkg_casefold IS
    PROCEDURE del_entry(p_i_id IN VARCHAR2) IS
    BEGIN
        NULL;
    END;
END pkg_casefold;
/
